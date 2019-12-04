from __future__ import annotations

from anyio import create_event, Event
from async_generator import async_generator, yield_from_
from typing import Optional

from aiocflib.crtp import (
    CRTPDataLike,
    CRTPDispatcher,
    CRTPDriver,
    CRTPPacket,
    CRTPPortLike,
)
from aiocflib.utils.concurrency import create_daemon_task_group

__all__ = ("Crazyflie",)

MYPY = False
if MYPY:
    from .console import Console
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

    def __init__(self, uri: str):
        """Constructor.

        Creates a Crazyflie_ instance from a URI specification.

        Parameters:
            uri: the URI where the Crazyflie can be reached
        """
        self._uri = uri
        self._dispatcher = CRTPDispatcher()

        self._driver = None
        self._task_group = None

        # Initialize sub-modules; avoid circular import
        from .console import Console
        from .platform import Platform

        self._console = Console(self)
        self._platform = Platform(self)

    async def __aenter__(self):
        self._task_group = create_daemon_task_group()
        spawner = await self._task_group.__aenter__()

        on_opened = create_event()
        await spawner.spawn(self._open_connection, on_opened)
        await on_opened.wait()

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
        command: Optional[int] = None,
        data: CRTPDataLike = None,
    ):
        """Sends a command packet to the Crazyflie and waits for the next
        matching response packet. Returns the data section of the response
        packet.

        The response packet is expected to have the same CRTP port and channel
        as the packet that was sent as a request. Additionally, if a command
        byte is present, the data section of the response packet is expected to
        start with the same command byte and only the rest of the data section
        is returned as the response. Otherwise, if there is no command byte
        specified, the entire data section of the response packet will be
        returned.

        Parameters:
            port: the CRTP port to send the packet to
            channel: the CRTP channel to send the packet to
            command: the command byte to insert as the first byte of the packet
                data section. When this is not `None`, the matching response
                packet is expected to have the same byte as the first byte of
                the packet.
            data: the data section of the request packet. When a command byte
                is present, it is inserted before the data section.

        Returns:
            the data section of the response packet
        """
        if command is not None:
            packet = CRTPPacket(port=port, channel=channel)
            packet.data = bytes((command,)) + (bytes(data) if data else b"")

            def matching_response(packet: CRTPPacket) -> bool:
                return packet.channel == channel and packet.command == command

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
        return response[1:] if command is not None else response

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
    def print_packets(packet):
        print(repr(packet))

    uri = "bradio://0/80/2M/E7E7E7E704"
    async with Crazyflie(uri) as cf:
        print("Firmware version:", await cf.platform.get_firmware_version())
        print("Protocol version:", await cf.platform.get_protocol_version())
        print("Device type:", await cf.platform.get_device_type_name())


if __name__ == "__main__":
    from aiocflib.crtp import init_drivers
    import trio

    init_drivers()
    trio.run(test)
