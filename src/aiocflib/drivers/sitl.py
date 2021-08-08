"""Asynchronous USB driver for the Crazyflie."""

from anyio import connect_tcp
from anyio.abc import ByteStream
from anyio.streams.buffered import BufferedByteReceiveStream
from array import array
from contextlib import AsyncExitStack
from functools import partial
from typing import Optional


__author__ = "CollMot Robotics Ltd"
__all__ = ("SITL",)


class SITL:
    r"""Low-level driver object that is used for communication with the
    Crazyflie software-in-the-loop simulator via a TCP connection.

    This object is intended to be used as an asynchronous context manager as
    follows::

        async with SITL(port=5432) as sitl:
            await sitl.send_bytes(b"\xfd\x01")
            await sitl.receive_bytes()
    """

    def __init__(self, host: str = "localhost", port: int = 5432):
        """Constructor.

        Creates a low-level driver object that communicates with a Crazyflie
        SITL simulator at the specified hostname and port.

        Parameters:
            host: the hostname to connect to
            port: the port to connect to
        """
        self._address = host, port
        self._exit_stack = None  # type: Optional[AsyncExitStack]
        self._client = None

    async def __aenter__(self):
        """Opens the driver object. This function must be called before you
        start using the driver.
        """
        self._exit_stack = AsyncExitStack()

        await self._exit_stack.__aenter__()
        tcp_context = await connect_tcp(*self._address)
        client = await self._exit_stack.enter_async_context(tcp_context)
        client = BufferedByteReceiveStream(client)

        return _SITLCommunicator(
            partial(self._send_bytes, client), partial(self._receive_bytes, client)
        )

    async def __aexit__(self, exc_type, exc_value, tb) -> bool:
        assert self._exit_stack is not None
        try:
            return await self._exit_stack.__aexit__(exc_type, exc_value, tb)
        finally:
            self._exit_stack = None

    async def _receive_bytes(
        self, client: BufferedByteReceiveStream
    ) -> Optional[array]:
        """Receives some data from the SITL connection in a synchronous manner.

        Parameters:
            client: the TCP client to read data from

        Returns:
            the data that was received

        Raises:
            IOError: when the simulator was disconnected
        """
        length = ord(await client.receive_exactly(1))
        return await client.receive_exactly(length)

    async def _send_bytes(self, client: ByteStream, data: bytes) -> None:
        """Sends some data via the TCP connection in a synchronous manner.

        Parameters:
            client: the TCP client to send data to
            data: the data to send

        Raises:
            IOError: when the simulator was disconnected
        """
        length = len(data)
        assert length <= 64
        await client.send_all(bytes((length,)) + data)


class _SITLCommunicator:
    """Object that is returned when entering a SITL context and that allows
    us to send packets to and receive packets from the SITL connection.

    This is an internal class; you do not need to construct it yourself.
    """

    def __init__(self, sender, receiver):
        """Constructor.

        Parameters:
            sender: an async function that can be used to send some data to the
                simulator
            receiver: an async function that can be used to receive the next
                packet from the simulator
        """
        self._sender = sender
        self._receiver = receiver

    async def send_bytes(self, data: array) -> None:
        """Sends some bytes to the connected simulator via the TCP connection.

        Parameters:
            data: the data to send to the simulator

        Raises:
            IOError: if the simulator was shut down or there was some other IO
                error during sending
        """
        return await self._sender(data)

    async def receive_bytes(self) -> array:
        """Receives some bytes from the connected simulator via the TCP
        connection.

        Returns:
            the data that was received. It is guaranteed to have at least one
            byte in it.

        Raises:
            IOError: if the simulator was shut down or there was some other IO
                error during receiving
        """
        while True:
            data = await self._receiver()
            if data:
                return data


async def test():
    async with SITL() as sitl:
        # \xfd\x01 sends a "get version" command to the link control port
        await sitl.send_bytes(b"\xfd\x01")
        data = await sitl.receive_bytes()
        print("Received:", data)


if __name__ == "__main__":
    import trio

    trio.run(test)
