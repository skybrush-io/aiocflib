"""Classes related to handling console messages of a Crazyflie."""

from anyio import fail_after

from aiocflib.crtp import CRTPPort
from aiocflib.utils.concurrency import aclosing

from .base import CrazyflieSubsystem
from .crazyflie import Crazyflie

__all__ = ("Console",)


class Console(CrazyflieSubsystem):
    """Class representing the handler of console messages for a Crazyflie
    instance.
    """

    async def messages(self, timeout: float = 1, partial_message_marker: str = "…"):
        """Async generator that yields full console messages from a
        Crazyflie.

        This generator essentially re-assembles the individual console message
        packets into full lines.

        Parameters:
            timeout: maximum number of seconds to wait for a newline character
                after a console message. If no newline character is received
                in this timeframe after a console message, the message will
                be posted separately, followed by a partial message marker
            partial_message_marker: marker to append to messages if a newline
                was not received in time after having received the message
        """
        partial_message_marker_bytes = partial_message_marker.encode("UTF-8")
        if not partial_message_marker_bytes.endswith(b"\n"):
            partial_message_marker_bytes += b"\n"

        parts: list[bytes] = []
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

    def get_port(self) -> CRTPPort:
        return CRTPPort.CONSOLE


async def test():
    uri = "radio+log://0/80/2M/E7E7E7E709"
    # uri = "usb+log://0"

    async with Crazyflie(uri) as cf:
        async for message in cf.console.messages():
            print(message)


if __name__ == "__main__":
    import trio

    from aiocflib.crtp import init_drivers

    init_drivers()
    try:
        trio.run(test)
    except KeyboardInterrupt:
        pass
