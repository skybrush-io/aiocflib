import sys

from anyio import create_condition, create_queue, move_on_after, sleep
from async_generator import asynccontextmanager, async_generator, yield_
from functools import partial
from typing import Callable, Tuple, List
from urllib.parse import urlparse

from aiocflib.crtp.crtpstack import CRTPPacket
from aiocflib.drivers.crazyradio import Crazyradio, RadioConfiguration
from aiocflib.utils.concurrency import create_daemon_task_group
from aiocflib.utils.statistics import SlidingWindowMean

from .crtpdriver import CRTPDriver, register
from .exceptions import WrongURIType
from .strategies import (
    BackoffPollingStrategy,
    DefaultPollingStrategy,
    DefaultResendingStrategy,
    PatientResendingStrategy,
    PollingStrategy,
    ResendingStrategy,
)

__all__ = ("RadioDriver",)


_instances = {}


@asynccontextmanager
@async_generator
async def SharedCrazyradio(index: int):
    global _instances

    radio, instance, count = _instances.get(index, (None, None, None))
    if radio is None:
        radio = await Crazyradio.detect_one(index=index)
        instance = await radio.__aenter__()
        _instances[index] = (radio, instance, 1)
    else:
        _instances[index] = (radio, instance, count + 1)

    try:
        await yield_(instance)
    finally:
        radio, instance, count = _instances[index]
        if count == 1:
            await radio.__aexit__(*sys.exc_info())
            _instances.pop(index)
        else:
            _instances[index] = radio, instance, count - 1


#: Type specification for radio driver presets
RadioDriverPreset = Tuple[
    Callable[[PollingStrategy], None], Callable[[ResendingStrategy], None]
]


@register("radio")
class RadioDriver(CRTPDriver):
    """CRTP driver that allows us to communicate with a Crazyflie via a
    Crazyradio dongle.

    Attributes:
        polling_strategy: a callable that decides how often we should poll the
            downlink if there are no packets that we want to send to the
            Crazyflie
        resending_strategy: a callable that decides whether we should resend the
            last packet if it failed or whether we should drop the connection
    """

    PRESETS = {
        "default": (DefaultPollingStrategy, DefaultResendingStrategy),
        "patient": (BackoffPollingStrategy, PatientResendingStrategy),
    }

    @asynccontextmanager
    @async_generator
    async def _connected_to(self, uri: str):
        parts = urlparse(uri)

        try:
            index = int(parts.netloc)
        except ValueError:
            raise WrongURIType("Invalid radio URI: {0!r}".format(uri))

        if index < 0:
            raise WrongURIType("Radio port index must be non-negative")

        configuration = RadioConfiguration.from_uri_path(parts.path)

        async with SharedCrazyradio(index) as radio:
            async with create_daemon_task_group() as task_group:
                await task_group.spawn(self._worker, radio, configuration)
                await yield_(self)

    def __init__(self, preset: str = "default"):
        """Constructor.

        Parameters:
            preset: name of a preset from the PRESETS attribute of the class
                that determines how often the driver should poll the downlink
                with null packets and how it should handle packet resending
        """
        self._has_safe_link = False
        self._link_quality = 0.0
        self._link_quality_condition = create_condition()

        preset = self.PRESETS.get(preset)
        if not preset:
            preset = self.PRESETS["default"]

        self.polling_strategy, self.resending_strategy = preset[0](), preset[1]()

        # TODO(ntamas): what if the in_queue is full?
        self._in_queue = create_queue(256)
        self._out_queue = create_queue(1)

    async def get_status(self) -> str:
        return "Crazyradio version {0}".format(self._device.version)

    @property
    def is_safe(self) -> bool:
        return self._has_safe_link

    @property
    def link_quality(self) -> float:
        return self._link_quality

    @property
    def name(self) -> str:
        return "radio"

    async def receive_packet(self) -> CRTPPacket:
        """Receives a single CRTP packet.

        Returns:
            the next CRTP packet that was received
        """
        return await self._in_queue.get()

    async def send_packet(self, packet: CRTPPacket):
        """Sends a CRTP packet.

        Parameters:
            packet: the packet to send
        """
        return await self._out_queue.put(packet)

    @classmethod
    async def scan_interface(cls, address=None) -> List[str]:
        """Scans all interfaces of this type for available Crazyflie quadcopters
        and returns a list with appropriate connection URIs that could be used
        to connect to them.

        Returns:
            the list of connection URIs where a Crazyflie was detected; an empty
            list is returned for interfaces that do not support scanning
        """
        # TODO(ntamas)
        return []

    async def _set_link_quality(self, value: float) -> None:
        """Sets a new link quality measure and notifies all listeners
        waiting for a new link quality measure.
        """
        self._link_quality = value
        async with self._link_quality_condition:
            await self._link_quality_condition.notify_all()

    async def _worker(
        self, radio: Crazyradio, configuration: RadioConfiguration
    ) -> None:
        """Worker task that runs continuously and handles the sending and
        receiving of packets to/from a given Crazyradio instance.

        Parameters:
            radio: the Crazyradio instance to use
            configuration: the radio configuration to use; specifies the
                channel, the data rate and the address to send the packets to
        """
        null_packet = outbound_packet = CRTPPacket.null()
        to_send = outbound_packet.to_bytes()
        delay_before_next_null_packet = 0.01

        # TODO(ntamas): try enabling safelink

        link_quality_estimator = SlidingWindowMean(100)

        while True:
            async with radio.configure(configuration):
                response = await radio.send_and_receive_bytes(to_send)

            if response is None:
                # Resend immediately
                continue

            # Link quality is determined as the mean of the score of the
            # last 100 packets, where the score is determined as follows.
            # The score of a packet is 10 if it went through the first
            # time we tried to send it (retry count is zero). The score
            # decreases by 1 for every retry, and also by 1 if the
            # packet was not acknowledged at the end (meaning that it
            # got lost). Then the score is divided by 10 so we get a
            # mean link quality between 0 and 1.
            score = 9 - response.retry_count + int(response.ack)
            link_quality_estimator.add(score)
            await self._set_link_quality(link_quality_estimator.mean / 10.0)

            # Check whether the packet has to be re-sent
            action = self.resending_strategy(response.ack, to_send)
            if action == "accept":
                # Accept the packet, no resending needed
                pass
            elif action == "stop":
                # Bail out -- too many packets lost
                raise IOError("Too many packets lost")
            elif action == 0:
                # Resend immediately
                continue
            elif action > 0:
                # Wait a bit before resending
                await sleep(action)
                continue
            else:
                # Invalid response, resend immediately
                continue

            # No resending needed, process response and get next packet to send
            if response.data:
                await self._in_queue.put(CRTPPacket.from_bytes(response.data))

            # Figure out how much to wait before the next null packet is sent
            delay_before_next_null_packet = self.polling_strategy(response.data)
            outbound_packet = null_packet
            async with move_on_after(delay_before_next_null_packet):
                outbound_packet = await self._out_queue.get()
            to_send = outbound_packet.to_bytes()


register("bradio")(partial(RadioDriver, preset="patient"))
