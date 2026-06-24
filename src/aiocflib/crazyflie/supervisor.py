"""Classes related to accessing the supervisor subsystem of a Crazyflie."""

from aiocflib.crtp import CRTPPort

from .base import CrazyflieSubsystem
from .crazyflie import Crazyflie

__all__ = ("Supervisor",)


class Supervisor(CrazyflieSubsystem):
    """Class representing the handler of supervisor messages of a Crazyflie instance."""

    def get_port(self) -> CRTPPort:
        return CRTPPort.SUPERVISOR


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
