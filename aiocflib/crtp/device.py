"""Superclass for devices that use the CRTP protocol for communication."""

from anyio import move_on_after
from async_generator import async_generator, yield_from_
from sys import exc_info
from typing import Iterable, Optional, Union

from .crtpstack import CRTPDispatcher, CRTPPacket, CRTPDataLike, CRTPPortLike
from .drivers import CRTPDriver

from aiocflib.errors import TimeoutError
from aiocflib.utils.concurrency import create_daemon_task_group, TaskStartedNotifier

__all__ = ("CRTPDevice",)


class CRTPDevice:
    """Superclass for devices that use the CRTP protocol for communication."""

    def __init__(self, uri: str):
        """Constructor.

        Creates a CRTPDevice_ from a URI specification.

        Parameters:
            uri: the URI describing the data link where the device can be reached
        """
        self._uri = uri
        self._dispatcher = CRTPDispatcher()

        self._driver = None
        self._task_group = None

    async def __aenter__(self):
        self._task_group = create_daemon_task_group()

        try:
            spawner = await self._task_group.__aenter__()
            await spawner.spawn_and_wait_until_started(self._open_connection)
        except BaseException:
            await self._task_group.__aexit__(*exc_info())
            raise

        return self

    async def __aexit__(self, exc_type, exc_value, tb):
        return await self._task_group.__aexit__(exc_type, exc_value, tb)

    async def _message_handler(self, driver):
        """Worker task that receives incoming messages from the device and
        dispatches them to the coroutines that are interested in receiving these
        messages.

        Raises:
            IOError: when an IO error happens while receiving packets from the
                device (typically when the USB port or the radio dongle is
                disconnected)
        """
        while True:
            packet = await driver.receive_packet()
            await self._dispatcher.dispatch(packet)

    async def _open_connection(self, notify_started: TaskStartedNotifier):
        """Task that opens a connection to the device and yields control
        to the message handler task until the connection is closed.

        Parameters:
            notify_started: an async function that must be called and awaited
                when the connection has been established
        """
        async with CRTPDriver.connected_to(self._uri) as driver:
            self._driver = driver

            # TODO(ntamas): maybe handle IOError here?
            try:
                await self._prepare_link(driver)
                await notify_started()
                await self._message_handler(driver)
            finally:
                self._driver = None

    async def _prepare_link(self, driver: CRTPDriver) -> None:
        """Performs initial setup on the CRTP driver before the connection is
        declared to be established.
        """
        pass

    @property
    def dispatcher(self) -> CRTPDispatcher:
        """Returns the packet dispatcher that dispatches incoming messages to
        the appropriate handler functions.

        You may then use the dispatcher to register handler functions to the
        messages you are interested in.
        """
        return self._dispatcher

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

    async def run_command(
        self,
        *,
        port: CRTPPortLike,
        channel: int = 0,
        command: Optional[Union[int, bytes, Iterable[Union[int, bytes]]]] = None,
        data: CRTPDataLike = None,
        timeout: float = 0.2,
        retries: int = 0
    ):
        """Sends a command packet to the device and waits for the next
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
            timeout: maximum number of seconds to wait for a response
            retries: number of retries in case of a timeout

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

        response = None

        # Send the packet and wait for the corresponding response
        with self.dispatcher.wait_for_next_packet(
            matching_response, port=port
        ) as value:
            while retries >= 0:
                await self._driver.send_packet(packet)
                if timeout > 0:
                    async with move_on_after(timeout):
                        response = await value.wait()
                else:
                    response = await value.wait()

                # If there was no timeout (i.e. the response is not `None`),
                # break out.
                if response is not None:
                    break

                # Otherwise, try again.
                retries -= 1

        if response is None:
            raise TimeoutError()

        response = response.data
        return response[len(command) :] if command else response
