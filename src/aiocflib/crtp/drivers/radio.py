from __future__ import annotations

from anyio import (
    create_memory_object_stream,
    move_on_after,
    sleep,
    Event,
    WouldBlock,
)
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from functools import partial
from operator import attrgetter
from sys import exc_info
from typing import Callable, Dict, Optional, Tuple, List

from aiocflib.crtp.crtpstack import CRTPPacket
from aiocflib.drivers.crazyradio import (
    Acknowledgment,
    Crazyradio,
    CrazyradioAddress,
    RadioConfiguration,
    _CfRadioCommunicator,
)
from aiocflib.utils.addressing import parse_radio_uri
from aiocflib.utils.concurrency import create_daemon_task_group, gather, ObservableValue
from aiocflib.utils.statistics import SlidingWindowMean

from ..exceptions import WrongURIType
from ..strategies import (
    BackoffPollingStrategy,
    DefaultPollingStrategy,
    DefaultResendingStrategy,
    NoPollingStrategy,
    PatientResendingStrategy,
    PollingStrategy,
    ResendingStrategy,
)

from .base import CRTPDriver
from .registry import register

__all__ = ("RadioDriver",)


_instances: Dict[int, "_SharedCrazyradioState"] = {}


@dataclass
class _SharedCrazyradioState:
    radio: Optional[Crazyradio] = None
    instance: Optional[_CfRadioCommunicator] = None
    count: int = 0

    _initializing_event: Event = field(default_factory=Event)
    _destroying_event: Optional[Event] = None

    @property
    def destroying(self) -> bool:
        return self._destroying_event is not None

    async def initialize(self, index: int) -> None:
        assert self.radio is None and self.instance is None
        assert self.count == 0

        radio = await Crazyradio.detect_one(index=index)
        instance = await radio.__aenter__()

        self.radio = radio
        self.instance = instance

        await self._initializing_event.set()

    def invalidate(self) -> None:
        self.invalidated = True

    def start_destruction(self) -> Tuple[Crazyradio, Event]:
        assert self.radio is not None
        assert self.count == 1
        assert self._destroying_event is None

        radio = self.radio
        self.radio = None
        self.instance = None
        self.count = 0
        self._destroying_event = Event()

        return radio, self._destroying_event

    async def wait_until_initialized(self) -> None:
        await self._initializing_event.wait()

    async def wait_until_destroyed(self) -> None:
        if self._destroying_event:
            await self._destroying_event.wait()


@asynccontextmanager
async def SharedCrazyradio(index: int):
    global _instances

    while True:
        state = _instances.get(index)
        if state is None:
            # This radio was not used yet so we need to initialize it
            _instances[index] = _SharedCrazyradioState()
            await _instances[index].initialize(index)
        elif state.destroying:
            # This radio is being destroyed in another task so let's wait until
            # it is released and then try to acquire it again
            await state.wait_until_destroyed()
        else:
            # This radio is either already initialized or is currently being
            # initialized so we are okay
            break

    # Wait until the radio is initialized
    await state.wait_until_initialized()
    state.count += 1

    try:
        yield state.instance
    finally:
        # When using Trio, we are not allowed to start any additional cancel
        # scopes here, otherwise Trio will complain about a "corrupted cancel
        # scope stack".
        state = _instances[index]
        if state.count > 1:
            state.count -= 1
        elif state.count == 1:
            radio, event = state.start_destruction()
            try:
                await radio.__aexit__(*exc_info())
            finally:
                _instances.pop(index)
            await event.set()
        else:
            raise RuntimeError("Corrupted radio reference counter")


#: Type specification for radio driver presets
RadioDriverPreset = Tuple[
    Callable[[PollingStrategy], None], Callable[[ResendingStrategy], None]
]


@dataclass
class _EnabledAcquired:
    enabled: bool
    acquired: bool


class _SafeLinkState:
    """Private class that stores the current state of the safe link mode."""

    def __init__(self):
        """Constructor."""
        self._up, self._down = 8, 4
        self._enabled_acquired = ObservableValue(_EnabledAcquired(False, False))

    @property
    def acquired(self) -> bool:
        """Returns whether the safe link mode has been acquired."""
        return self._enabled_acquired.value.acquired

    @property
    def counter_bits(self) -> Tuple[int, int]:
        """Returns the current state of the counter bits, for debugging purposes."""
        return self._up, self._down

    async def disable(self) -> None:
        """Disables the safe link mode on the Crazyflie. Note that the Crazyflie
        will still operate in safe link mode if it has already been acquired,
        but it will not be a requirement any more that the safe link mode must
        be enabled at all times.
        """
        if self.enabled:
            value = self._enabled_acquired.value
            await self._enabled_acquired.set(replace(value, enabled=False))

    @property
    def enabled(self) -> bool:
        """Returns whether the safe link mode _should_ be enabled on the
        Crazyflie. This does not necessarily mean that it _is_ enabled; it only
        means that we are trying to negotiate the safe link mode with the
        Crazyflie.
        """
        return self._enabled_acquired.value.enabled

    async def notify_acquired(self) -> None:
        """Notifies the state object that the safe link mode has been
        successfully negotiated with the Crazyflie."""
        await self._set_acquired(True)

    async def notify_lost(self) -> None:
        """Notifies the state object that the safe link mode has been lost."""
        await self._set_acquired(False)

    async def enable(self) -> None:
        """Enables the safe link mode on the Crazyflie."""
        if not self.enabled:
            value = self._enabled_acquired.value
            await self._enabled_acquired.set(replace(value, enabled=True))

    def encode(self, packet: CRTPPacket) -> bytes:
        """Encodes the given CRTP packet, incorporating the safe link status
        bits in the header.
        """
        return packet.to_bytes(safelink_bits=self._up + self._down)

    def observe(self) -> ObservableValue[_EnabledAcquired]:
        """Returns an observable value that reports whether the safe link mode
        is currently enabled and acquired or not.
        """
        return self._enabled_acquired

    async def _set_acquired(self, value: bool) -> None:
        """Sets the 'acquired' flag of the safe link mode."""
        value = bool(value)

        if self.acquired == value:
            return

        if value:
            self._up, self._down = 0, 0
        else:
            self._up, self._down = 8, 4

        state = self._enabled_acquired.value
        await self._enabled_acquired.set(replace(state, acquired=value))

    def update(self, response: Acknowledgment) -> None:
        """Processes an acknowledgment received from the peer."""
        if not self.acquired:
            return

        if not response.ack:
            return

        self._up = 8 - self._up
        if response.data and response.data[0] & 0x04 == self._down:
            self._down = 4 - self._down

    async def wait_until_acquired(self) -> None:
        """Waits until the safe-link mode is acquired."""
        await self._enabled_acquired.wait_until(attrgetter("acquired"))


#: Dictionary mapping human-readable names to pairs or polling and resending
#: strategies that can be used by a radio driver
RADIO_DRIVER_PRESETS = {
    "default": (DefaultPollingStrategy, DefaultResendingStrategy),
    "patient": (BackoffPollingStrategy, PatientResendingStrategy),
    "noPolling": (NoPollingStrategy, DefaultResendingStrategy),
}


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

    PRESETS = RADIO_DRIVER_PRESETS

    @asynccontextmanager
    async def _connected_to(self, uri: str):
        try:
            parts = parse_radio_uri(uri)
        except ValueError:
            raise WrongURIType from None

        index = parts.pop("index")

        self._configuration = RadioConfiguration(**parts)

        async with SharedCrazyradio(index) as radio:
            async with create_daemon_task_group() as task_group:
                task_group.start_soon(self._worker, radio)
                task_group.start_soon(self._safe_link_supervisor, radio)
                yield self

    def __init__(self, preset: str = "default"):
        """Constructor.

        Parameters:
            preset: name of a preset from the PRESETS attribute of the class
                that determines how often the driver should poll the downlink
                with null packets and how it should handle packet resending
        """
        self._configuration = None
        self._link_quality = ObservableValue(0.0)
        self._safe_link_state = _SafeLinkState()

        try:
            self.apply_preset(preset)
        except KeyError:
            raise ValueError(f"No such preset: {preset}")

        # TODO(ntamas): what if the in_queue is full?
        self._in_queue_tx, self._in_queue_rx = create_memory_object_stream(256)
        self._out_queue_tx, self._out_queue_rx = create_memory_object_stream(1)

    @property
    def address(self) -> Optional[CrazyradioAddress]:
        """The address that the driver will be configured for, or ``None`` if
        the driver has no URI.
        """
        if not self._uri:
            return None

        try:
            config = parse_radio_uri(self._uri)
        except Exception:
            return None

        return config["address"] if config else None

    def apply_preset(self, name: str) -> None:
        """Applies a preset strategy to the given connection to control how
        often should the driver pull the downlink with null packets and how it
        should handle acknowledgment failures.

        This method can be called with an active connection; the new preset
        will take effect as soon as possible.
        """
        try:
            preset = self.PRESETS[name]
        except KeyError:
            raise KeyError("no such preset: {0}".format(name)) from None

        self.polling_strategy, self.resending_strategy = preset[0](), preset[1]()

    @property
    def configuration(self) -> Optional[RadioConfiguration]:
        """The address, channel and data rate that the driver is configured for,
        or ``None`` if the driver is not configured.
        """
        return self._configuration

    @property
    def is_safe(self) -> bool:
        return self._safe_link_state.acquired

    @property
    def link_quality(self) -> ObservableValue[float]:
        return self._link_quality

    @property
    def name(self) -> str:
        return "radio"

    async def notify_rebooted(self) -> None:
        safe_link_was_enabled = self._safe_link_state.enabled
        if not safe_link_was_enabled:
            # Nothing to do
            return
        else:
            # Disable the safe link mode temporarily until the Crazyflie boots
            await self._safe_link_state.disable()
            await self._notify_safe_link_lost()
            # Wait for the Crazyflie to boot
            await sleep(1)
            # Notify the driver that it is now safe to re-enable the safe link mode
            await self._safe_link_state.enable()

    async def receive_packet(self) -> CRTPPacket:
        """Receives a single CRTP packet.

        Returns:
            the next CRTP packet that was received
        """
        return await self._in_queue_rx.receive()

    async def send_packet(self, packet: CRTPPacket) -> None:
        """Sends a CRTP packet.

        Parameters:
            packet: the packet to send
        """
        await self._out_queue_tx.send(packet)

    @classmethod
    async def scan_interfaces(
        cls, address: Optional[CrazyradioAddress] = None
    ) -> List[str]:
        """Scans all interfaces of this type for available Crazyflie quadcopters
        and returns a list with appropriate connection URIs that could be used
        to connect to them.

        Parameters:
            address: the address of the Crazyflie to look for; `None` means to
                use the default address

        Returns:
            the list of connection URIs where a Crazyflie was detected; an empty
            list is returned for interfaces that do not support scanning
        """
        devices = await Crazyradio.detect_all()
        results: List[str] = await gather(
            (cls._scan_single_interface, device, address) for device in devices
        )
        return sum(results, [])

    async def use_safe_link(self) -> None:
        """Instructs the driver to start using safe-link mode to ensure
        guaranteed packet delivery to the remote peer.
        """
        await self._safe_link_state.enable()

    async def _notify_safe_link_lost(self) -> None:
        """Notifies the driver that the established safe link state has been
        lost and it should re-establish the safe link state as soon as possible.
        """
        await self._safe_link_state.notify_lost()

    @classmethod
    async def _scan_single_interface(
        cls, radio: Crazyradio, address: Optional[CrazyradioAddress] = None
    ) -> List[str]:
        """Scans a single interface for available Crazyflie quadcopters and
        returns a list with appropriate connection URIs that could be used
        to connect to them.

        Parameters:
            radio: the radio device to use for scanning
            address: the address of the Crazyflie to look for; `None` means to
                use the default address

        Returns:
            the list of connection URIs where a Crazyflie was detected; an empty
            list is returned for interfaces that do not support scanning
        """
        async with radio as device:
            return await device.scan(address=address)

    async def _safe_link_supervisor(self, radio: Crazyradio) -> None:
        """Worker task that ensures that the radio is in safe link mode when it
        should be in safe link mode.
        """
        async for state in self._safe_link_state.observe():
            if state.enabled and not state.acquired:
                success = False
                while not success:
                    async with radio.configure(self._configuration):
                        success = await self._try_to_acquire_safe_link_mode(radio)
                    if not success:
                        await sleep(0.25)

    async def _try_to_acquire_safe_link_mode(self, radio: Crazyradio):
        """Attempts to acquire safe link mode on the Crazyflie found at the
        address, channel and data rate that the radio is currently configured to.

        Returns:
            whether the safe link mode was acquired successfully
        """
        safe_link_packet = CRTPPacket.safe_link().to_bytes()

        for _ in range(10):
            response = await radio.send_and_receive_bytes(safe_link_packet)
            if response and response.data == safe_link_packet:
                await self._safe_link_state.notify_acquired()
                return True

        return False

    async def _worker(self, radio: Crazyradio) -> None:
        """Worker task that runs continuously and handles the sending and
        receiving of packets between a given Crazyradio instance and a single
        Crazyflie (or other CRTP device).

        Parameters:
            radio: the Crazyradio instance to use
        """
        if self._safe_link_state.enabled:
            # Wait at most 2 seconds for safe-link mode before proceeding
            # without it
            with move_on_after(2):
                await self._safe_link_state.wait_until_acquired()

        null_packet = outbound_packet = CRTPPacket.null()
        delay_before_next_null_packet = 0.01

        link_quality_estimator = SlidingWindowMean(100)

        while True:
            to_send = self._safe_link_state.encode(outbound_packet)
            response = await radio.configure_send_and_receive_bytes(
                self._configuration, to_send
            )

            if response is None:
                # Resend immediately
                continue

            # Update the safe-link state
            self._safe_link_state.update(response)

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
            await self._link_quality.update(link_quality_estimator.mean / 10.0)

            # Check whether the packet has to be re-sent
            action = self.resending_strategy(response.ack, to_send)
            if action == "accept":
                # Accept the packet, no resending needed
                pass
            elif action == "stop":
                # Bail out -- too many packets lost
                raise IOError("Too many packets lost")
            elif action == 0:
                # Resend immediately. If the packet that we are trying to send
                # is a filler (null) packet, poll the outbound packet queue and
                # check whether we can send something useful instead of just
                # pulling the downstream
                if outbound_packet is null_packet:
                    try:
                        outbound_packet = self._out_queue_rx.receive_nowait()
                    except WouldBlock:
                        outbound_packet = null_packet
                continue
            elif action > 0:
                # Wait a bit before resending. If the packet that we are trying
                # to send is a filler (null) packet, poll the outbound packet
                # queue after the delay and check whether we can send something
                # useful instead of just pulling the downstream
                if outbound_packet is null_packet:
                    with move_on_after(action):
                        outbound_packet = await self._out_queue_rx.receive()
                else:
                    await sleep(action)
                continue
            else:
                # Invalid response, resend immediately
                continue

            # No resending needed, process response and get next packet to send
            if response.data:
                inbound_packet = CRTPPacket.from_bytes(response.data)
                await self._in_queue_tx.send(inbound_packet)

            # Figure out how much to wait before the next null packet is sent
            delay_before_next_null_packet = self.polling_strategy(
                response.data, to_send
            )
            if delay_before_next_null_packet > 0:
                # Wait for a given number of seconds
                outbound_packet = null_packet
                with move_on_after(delay_before_next_null_packet):
                    outbound_packet = await self._out_queue_rx.receive()
            elif delay_before_next_null_packet < 0:
                # Wait indefinitely
                outbound_packet = await self._out_queue_rx.receive()
            else:
                # Poll the outbound queue; send a null packet if the queue is
                # empty
                try:
                    outbound_packet = self._out_queue_rx.receive_nowait()
                except WouldBlock:
                    outbound_packet = null_packet


register("bradio")(partial(RadioDriver, preset="patient"))
