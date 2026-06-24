"""Classes related to accessing the supervisor subsystem of a Crazyflie."""

from collections.abc import AsyncIterator

from aiocflib.crtp import CRTPPacket, CRTPPort

from .crazyflie import Crazyflie

__all__ = ("Supervisor",)


class Supervisor:
    """Class representing the handler of supervisor messages of a Crazyflie instance."""

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle the supervisor
                messages
        """
        self._crazyflie = crazyflie

    async def packets(self) -> AsyncIterator[CRTPPacket]:
        """Async generator that yields supervisor message packets from a Crazyflie,
        without reassembling them to full messages.
        """
        async for packet in self._crazyflie.packets(port=CRTPPort.SUPERVISOR):
            yield packet


async def test():
    uri = "radio+log://0/80/2M/E7E7E7E709"
    # uri = "usb+log://0"

    async with Crazyflie(uri) as cf:
        async for message in cf.supervisor.messages():
            print(message)


if __name__ == "__main__":
    import trio

    from aiocflib.crtp import init_drivers

    init_drivers()
    try:
        trio.run(test)
    except KeyboardInterrupt:
        pass
