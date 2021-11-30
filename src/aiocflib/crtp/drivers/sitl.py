from anyio import create_memory_object_stream, move_on_after, WouldBlock
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from aiocflib.crtp.crtpstack import CRTPPacket
from aiocflib.drivers.sitl import SITL
from aiocflib.utils.concurrency import create_daemon_task_group, ObservableValue

from .base import CRTPDriver
from .registry import register

from ..strategies import (
    BackoffPollingStrategy,
    DefaultPollingStrategy,
    NoPollingStrategy,
)

_link_quality = None  # type: Optional[ObservableValue[float]]


@register("sitl")
class SITLDriver(CRTPDriver):
    """CRTP driver that allows us to communicate with a simulated Crazyflie that
    is running in a software-in-the-loop simulator and is accessible via a
    TCP connection.

    Attributes:
        polling_strategy: a callable that decides how often we should poll the
            downlink if there are no packets that we want to send to the
            Crazyflie
    """

    PRESETS = {
        "default": (DefaultPollingStrategy,),
        "patient": (BackoffPollingStrategy,),
        "noPolling": (NoPollingStrategy,),
    }

    @asynccontextmanager
    async def _connected_to(self, uri: str):
        parts = urlparse(uri)
        host, _, port = parts.netloc.partition(":")
        host = host or "localhost"
        port = int(port) or 5432

        async with SITL(host, port) as sitl:
            async with create_daemon_task_group() as task_group:
                task_group.start_soon(self._worker, sitl)
                yield self

    def __init__(self, preset: str = "default"):
        """Constructor.

        Parameters:
            preset: name of a preset from the PRESETS attribute of the class
                that determines how often the driver should poll the downlink
                with null packets and how it should handle packet resending
        """
        try:
            self.apply_preset(preset)
        except KeyError:
            self.apply_preset("default")

        # TODO(ntamas): what if the in_queue is full?
        self._in_queue_tx, self._in_queue_rx = create_memory_object_stream(256)
        self._out_queue_tx, self._out_queue_rx = create_memory_object_stream(1)

    def apply_preset(self, name: str) -> None:
        """Applies a preset strategy to the given connection to control how
        often should the driver pull the downlink with null packets.

        This method can be called with an active connection; the new preset
        will take effect as soon as possible.
        """
        try:
            preset = self.PRESETS[name]
        except KeyError:
            raise KeyError("no such preset: {0}".format(name)) from None

        self.polling_strategy = preset[0]()

    @property
    def is_safe(self) -> bool:
        return True

    @property
    def link_quality(self) -> ObservableValue[float]:
        global _link_quality
        if _link_quality is None:
            _link_quality = ObservableValue.constant(1.0)
        return _link_quality

    @property
    def name(self) -> str:
        return "SITL"

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

    async def _worker(self, sitl: SITL) -> None:
        """Worker task that runs continuously and handles the sending and
        receiving of packets to/from a given SITL instance.

        Parameters:
            sitl: the SITL instance to use
        """
        null_packet = outbound_packet = CRTPPacket.null()
        delay_before_next_null_packet = 0.01

        while True:
            to_send = outbound_packet.to_bytes()
            await sitl.send_bytes(to_send)

            response = None
            with move_on_after(0.02):
                response = await sitl.receive_bytes()

            if response is not None:
                inbound_packet = CRTPPacket.from_bytes(response)
                await self._in_queue_tx.send(inbound_packet)
            else:
                response = b"\xff"

            # Figure out how much to wait before the next null packet is sent
            delay_before_next_null_packet = self.polling_strategy(response, to_send)
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
