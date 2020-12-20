"""Classes related to handling platform service messages of a Crazyflie."""

from aiocflib.crtp import CRTPPort

from .crazyflie import Crazyflie
from .platform import PlatformChannel

__all__ = ("AppChannel",)


class AppChannel:
    """Class representing the handler of app channel messages for a Crazyflie
    instance.
    """

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle the app channel
                messages
        """
        self._crazyflie = crazyflie

    async def packets(self):
        """Async generator that yields messages coming on the app-specific
        channel from a Crazyflie.
        """
        async for packet in self._crazyflie.packets(port=CRTPPort.PLATFORM):
            if packet.channel == PlatformChannel.APP_CHANNEL:
                yield packet
