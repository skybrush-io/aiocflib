"""Classes related to handling console messages of a Crazyflie."""

from aiocflib.crtp import CRTPPort

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

    async def messages(self):
        """Async generator that yields full console messages from a
        Crazyflie.

        This generator essentially re-assembles the individual console message
        packets into full lines.
        """
        # TODO(ntamas): handle <F> marker
        # TODO(ntamas): timeout if a partial line is received and no messages
        # follow it for a while; yield the partial line back to the user
        parts = []

        async for packet in self.packets():
            data = packet.data.rstrip(b"\x00")
            while True:
                data, sep, rest = data.partition(b"\n")
                parts.append(data)
                if sep:
                    yield (b"".join(parts).decode("UTF-8", errors="backslashreplace"))
                    parts.clear()
                    data = rest
                else:
                    break

    async def packets(self):
        """Async generator that yields console message packets from a Crazyflie,
        without reassembling them to full messages.
        """
        async for packet in self._crazyflie.packets(port=CRTPPort.CONSOLE):
            yield packet
