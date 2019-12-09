from __future__ import annotations

from anyio import create_event, Event
from async_generator import async_generator, yield_from_
from sys import exc_info
from typing import Iterable, Optional, Union

from aiocflib.crtp import (
    CRTPDataLike,
    CRTPDispatcher,
    CRTPDriver,
    CRTPPacket,
    CRTPPortLike,
)
from aiocflib.utils.concurrency import create_daemon_task_group, ObservableValue
from aiocflib.utils.toc import TOCCache, TOCCacheLike

__all__ = ("Crazyflie",)

MYPY = False
if MYPY:
    from .console import Console
    from .mem import Memory
    from .param import Parameters
    from .platform import Platform


class Crazyflie:
    """Objects representing a single Crazyflie device.

    This object should be used as a context manager; the methods of this object
    that communicate with the Crazyflie must only be called within the context
    established by the instance, e.g.::

        async with Crazyflie(uri) as cf:
            # ...do anything with the Crazyflie here...
            pass
        # Connection to the Crazyflie closes when the context is exited
    """

    def __init__(self, uri: str, cache: TOCCacheLike):
        """Constructor.

        Creates a Crazyflie_ instance from a URI specification.

        Parameters:
            uri: the URI where the Crazyflie can be reached
        """
        self._cache = TOCCache.create(cache)

        self._uri = uri
        self._dispatcher = CRTPDispatcher()

        self._driver = None
        self._task_group = None

        # Initialize sub-modules; avoid circular import
        from .console import Console
        from .mem import Memory
        from .param import Parameters
        from .platform import Platform

        self._console = Console(self)
        self._memory = Memory(self)
        self._parameters = Parameters(self)
        self._platform = Platform(self)

    async def __aenter__(self):
        self._task_group = create_daemon_task_group()
        on_opened = create_event()

        try:
            spawner = await self._task_group.__aenter__()
            await spawner.spawn(self._open_connection, on_opened)
            await on_opened.wait()
        except BaseException:
            await self._task_group.__aexit__(*exc_info())
            raise

        return self

    async def __aexit__(self, exc_type, exc_value, tb):
        return await self._task_group.__aexit__(exc_type, exc_value, tb)

    async def _message_handler(self, driver):
        """Worker task that receives incoming messages from the Crazyflie and
        dispatches them to the coroutines that are interested in receiving these
        messages.

        Raises:
            IOError: when an IO error happens while receiving packets from the
                Crazyflie (typically when the USB port or the radio dongle is
                disconnected)
        """
        while True:
            packet = await driver.receive_packet()
            await self._dispatcher.dispatch(packet)

    async def _open_connection(self, on_opened: Event):
        """Task that opens a connection to the Crazyflie and yields control
        to the message handler task until the connection is closed.

        Parameters:
            on_opened: an event that must be set when the connection has been
                established
        """
        async with CRTPDriver.connected_to(self._uri) as driver:
            self._driver = driver
            await on_opened.set()

            # TODO(ntamas): maybe handle IOError here?
            try:
                await self._message_handler(driver)
            finally:
                self._driver = None

    @property
    def console(self) -> Console:
        """The console message handler module of the Crazyflie."""
        return self._console

    @property
    def dispatcher(self) -> CRTPDispatcher:
        """Returns the packet dispatcher that dispatches incoming messages to
        the appropriate handler functions.

        You may then use the dispatcher to register handler functions to the
        messages you are interested in.
        """
        return self._dispatcher

    @property
    def link_quality(self) -> ObservableValue[float]:
        """Returns an observable link quality measure from the underlying
        link.
        """
        return (
            self._driver.link_quality if self._driver else ObservableValue.constant(0.0)
        )

    @property
    def mem(self) -> Memory:
        """The memory subsystem of the Crazyflie. This is a compatibility alias
        of ``self.memory`` for sake of compatibility with the official
        Crazyflie library.
        """
        return self._memory

    @property
    def memory(self) -> Memory:
        """The memory subsystem of the Crazyflie."""
        return self._memory

    @property
    def param(self) -> Parameters:
        """The parameters subsystem of the Crazyflie. This is a compatibility
        alias of ``self.parameters`` for sake of compatibility with the official
        Crazyflie library.
        """
        return self._parameters

    @property
    def parameters(self) -> Parameters:
        """The parameters subsystem of the Crazyflie."""
        return self._parameters

    @property
    def platform(self) -> Platform:
        """The platform-related message handler module of the Crazyflie."""
        return self._platform

    @property
    def uri(self):
        """The URI where the Crazyflie resides."""
        return self._uri

    async def run_command(
        self,
        port: CRTPPortLike,
        channel: int = 0,
        command: Optional[Union[int, bytes, Iterable[Union[int, bytes]]]] = None,
        data: CRTPDataLike = None,
    ):
        """Sends a command packet to the Crazyflie and waits for the next
        matching response packet. Returns the data section of the response
        packet.

        The response packet is expected to have the same CRTP port and channel
        as the packet that was sent as a request. Additionally, if a command
        is present, the data section of the response packet is expected to
        start with the same command byte(s), and _only_ the rest of the data
        section is returned as the response. If there is no command specified,
        the response must match the port and the channel only, and the entire
        data section of the response packet will be returned.

        Parameters:
            port: the CRTP port to send the packet to
            channel: the CRTP channel to send the packet to
            command: the command byte(s) to insert before the data bytes in
                the data section of the packet. When this is not `None`, the
                matching response packet is expected to have the same prefix as
                the command itself.
            data: the data of the request packet. When a command is present, the
                command is inserted before the data in the body of the CRTP
                packet.

        Returns:
            the data section of the response packet
        """
        if command is not None:
            if isinstance(command, int):
                command = bytes((command,))
            elif isinstance(command, bytes):
                pass
            else:
                parts = []
                for part in command:
                    if isinstance(part, int):
                        part = bytes((part,))
                    parts.append(part)
                command = b"".join(parts)

            packet = CRTPPacket(port=port, channel=channel)
            request = command + (bytes(data) if data else b"")
            packet.data = request

            def matching_response(packet: CRTPPacket) -> bool:
                return packet.channel == channel and packet.data.startswith(command)

        else:
            packet = CRTPPacket(port=port, channel=channel, data=data)

            def matching_response(packet: CRTPPacket) -> bool:
                return packet.channel == channel

        with self.dispatcher.wait_for_next_packet(
            matching_response, port=port
        ) as value:
            await self._driver.send_packet(packet)
            response = await value.wait()

        response = response.data
        return response[len(command) :] if command else response

    @async_generator
    async def packets(
        self, port: Optional[CRTPPortLike] = None, *, queue_size: int = 0
    ):
        """Asynchronous generator that yields incoming packets matching the
        given port.

        Parameters:
            port: the CRTP port to match; `None` means to match all CRTP ports
            queue_size: number of pending packets that may stay in the backlog
                of the generator before blocking. Typically 0 is enough.

        Yields:
            incoming CRTP packets
        """
        with self.dispatcher.create_packet_queue(
            port=port, queue_size=queue_size
        ) as queue:
            await yield_from_(queue)


async def test():
    from aiocflib.utils import timing

    def print_packets(packet):
        print(repr(packet))

    uri = "radio+log://0/80/2M/E7E7E7E704"
    cache = TOCCache.create("memory://")

    async with Crazyflie(uri, cache=cache) as cf:
        print("Firmware version:", await cf.platform.get_firmware_version())
        print("Protocol version:", await cf.platform.get_protocol_version())
        print("Device type:", await cf.platform.get_device_type_name())
        await cf.parameters.set_fast("kalman.resetEstimation", "u8", 1)
        with timing("Fetching memory TOC"):
            await cf.memory.validate()
        with timing("Fetching parameters TOC"):
            await cf.parameters.validate()

    async with Crazyflie(uri, cache=cache) as cf:
        with timing("Fetching memory TOC again - should be faster"):
            await cf.parameters.validate()


if __name__ == "__main__":
    from aiocflib.crtp import init_drivers
    import anyio

    init_drivers()
    anyio.run(test)
