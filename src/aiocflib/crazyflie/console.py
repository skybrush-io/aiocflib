"""Classes related to handling console messages of a Crazyflie."""

from typing import AsyncIterator, List

from anyio import fail_after

from aiocflib.crtp import CRTPPacket, CRTPPort
from aiocflib.utils.concurrency import aclosing

from .crazyflie import Crazyflie

__all__ = ("Console",)


class Console:
    """Class representing the handler of console messages for a Crazyflie
    instance.
    """

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle the console
                messages
        """
        self._crazyflie = crazyflie

    async def messages(self, timeout: float = 1, partial_message_marker: str = "…"):
        """Async generator that yields full console messages from a
        Crazyflie.

        This generator essentially re-assembles the individual console message
        packets into full lines.

        Parameters:
            timeout: maximum number of seconds to wait for a newline character
                after a console message. If no newline character is received
                in this timeframe after a console message, the message will
                be posted separately, followd by a partial message marker
            partial_message_marker: marker to append to messages if a newline
                was not received in time after having received the message
        """
        partial_message_marker_bytes = partial_message_marker.encode("UTF-8")
        if not partial_message_marker_bytes.endswith(b"\n"):
            partial_message_marker_bytes += b"\n"

        parts: List[bytes] = []
        gen = self.packets()

        async with aclosing(gen):
            while True:
                packet = None

                try:
                    if not parts:
                        packet = await gen.__anext__()
                    else:
                        try:
                            with fail_after(timeout):
                                packet = await gen.__anext__()
                        except TimeoutError:
                            packet = None
                except StopAsyncIteration:
                    break

                if packet is not None:
                    data = packet.data.rstrip(b"\x00")
                else:
                    data = partial_message_marker_bytes

                while True:
                    data, sep, rest = data.partition(b"\n")
                    if data:
                        parts.append(data)
                    if sep:
                        yield (
                            b"".join(parts).decode("UTF-8", errors="backslashreplace")
                        )
                        parts.clear()
                        data = rest
                    else:
                        break

    async def packets(self) -> AsyncIterator[CRTPPacket]:
        """Async generator that yields console message packets from a Crazyflie,
        without reassembling them to full messages.
        """
        async for packet in self._crazyflie.packets(port=CRTPPort.CONSOLE):
            yield packet


async def test():
    uri = "radio+log://0/80/2M/E7E7E7E709"
    # uri = "sitl+log://"
    # uri = "usb+log://0"

    async with Crazyflie(uri) as cf:
        async for message in cf.console.messages():
            print(message)


if __name__ == "__main__":
    from aiocflib.crtp import init_drivers
    import trio

    init_drivers()
    try:
        trio.run(test)
    except KeyboardInterrupt:
        pass
