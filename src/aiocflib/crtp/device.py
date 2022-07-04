"""Superclass for devices that use the CRTP protocol for communication."""

from anyio import move_on_after
from contextlib import AsyncExitStack
from sys import exc_info
from typing import AsyncIterable, Iterable, Optional, Union

from .crtpstack import (
    CRTPDispatcher,
    CRTPPacket,
    CRTPCommandLike,
    CRTPDataLike,
    CRTPPortLike,
)
from .drivers import CRTPDriver

from aiocflib.errors import TimeoutError
from aiocflib.utils.concurrency import (
    create_daemon_task_group,
    DaemonTaskGroup,
    TaskStartedNotifier,
)

__all__ = ("CRTPDevice",)


class CRTPDevice:
    """Superclass for devices that use the CRTP protocol for communication."""

    _dispatcher: CRTPDispatcher
    _driver: CRTPDriver
    _exit_stack: Optional[AsyncExitStack]
    _task_group: Optional[DaemonTaskGroup]
    _uri: str

    def __init__(self, uri: str):
        """Constructor.

        Creates a CRTPDevice_ from a URI specification.

        Parameters:
            uri: the URI describing the data link where the device can be reached
        """
        self._uri = uri
        self._dispatcher = CRTPDispatcher()

        self._driver = None  # type: ignore
        self._task_group = None
        self._exit_stack = None

    async def __aenter__(self):
        assert self._exit_stack is None

        exit_stack = AsyncExitStack()
        await exit_stack.__aenter__()

        self._task_group = create_daemon_task_group()
        try:
            spawner = await exit_stack.enter_async_context(self._task_group)
            await spawner.start(self._open_connection)
            self._exit_stack = exit_stack
        finally:
            if self._exit_stack is None:
                await exit_stack.__aexit__(*exc_info())

        return self

    async def __aexit__(self, exc_type, exc_value, tb) -> bool:
        assert self._exit_stack is not None
        exit_stack = self._exit_stack
        self._exit_stack = None
        return await exit_stack.__aexit__(exc_type, exc_value, tb)

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
                notify_started()
                await self._message_handler(driver)
            finally:
                self._driver = None  # type: ignore

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

    async def packets(
        self, port: Optional[CRTPPortLike] = None, *, queue_size: int = 0
    ) -> AsyncIterable[CRTPPacket]:
        """Asynchronous generator that yields incoming packets matching the
        given port.

        Parameters:
            port: the CRTP port to match; `None` means to match all CRTP ports
            queue_size: number of pending packets that may stay in the backlog
                of the generator before blocking. Typically 0 is enough.

        Yields:
            incoming CRTP packets
        """
        async with self.dispatcher.create_packet_queue(
            port=port, queue_size=queue_size
        ) as queue:
            async for packet in queue:
                yield packet

    async def run_command(
        self,
        *,
        port: CRTPPortLike,
        channel: int = 0,
        command: Optional[CRTPCommandLike] = None,
        data: Optional[CRTPDataLike] = None,
        timeout: float = 0.2,
        attempts: int = 3,
    ) -> bytes:
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
            attempts: maximum number of attempts to send the command and wait
                for the response

        Returns:
            the data section of the response packet
        """
        if command is not None:
            encoded_command = _handle_data_argument(command)
            packet = CRTPPacket(port=port, channel=channel)
            request = (encoded_command + bytes(data)) if data else encoded_command
            packet.data = request

            def matching_response(packet: CRTPPacket) -> bool:
                return packet.channel == channel and packet.data.startswith(
                    encoded_command
                )

        else:
            encoded_command = b""
            packet = CRTPPacket(port=port, channel=channel, data=data)

            def matching_response(packet: CRTPPacket) -> bool:
                return packet.channel == channel

        response = None

        # Send the packet and wait for the corresponding response
        with self.dispatcher.wait_for_next_packet(
            matching_response, port=port
        ) as value:
            while attempts > 0:
                # TODO(ntamas): self._driver may become `None` here if the
                # connection is closed for any reason in another async task.
                # This needs to be handled.
                await self._driver.send_packet(packet)
                if timeout > 0:
                    with move_on_after(timeout):
                        response = await value.wait()
                else:
                    response = await value.wait()

                # If there was no timeout (i.e. the response is not `None`),
                # break out.
                if response is not None:
                    break

                # Otherwise, try again.
                attempts -= 1

        if response is None:
            raise TimeoutError()

        response = response.data
        return response[len(encoded_command) :] if encoded_command else response

    async def send_packet(
        self,
        *,
        port: CRTPPortLike,
        channel: int = 0,
        data: Optional[Union[int, bytes, Iterable[Union[int, bytes]]]] = None,
    ):
        """Sends a packet to the device with the given CRTP port, channel and
        the given body.

        Parameters:
            port: the CRTP port to send the packet to
            channel: the CRTP channel to send the packet to
            data: the body of the request packet
        """
        packet = CRTPPacket(port=port, channel=channel)
        packet.data = _handle_data_argument(data)
        await self._driver.send_packet(packet)


def _handle_data_argument(command: Optional[CRTPCommandLike] = None) -> bytes:
    """Helper function to handle the conversion of the `command` argument in
    `CRTPDevice.run_command()` to a `bytes` object.
    """
    if command is None:
        return b""
    elif isinstance(command, int):
        return bytes((command,))
    elif isinstance(command, bytes):
        return command
    else:
        parts = []
        for part in command:
            if isinstance(part, int):
                part = bytes((part,))
            parts.append(part)
        return b"".join(parts)
