"""Classes related to handling platform service messages of a Crazyflie."""

from collections.abc import AsyncIterator

from aiocflib.crtp.crtpstack import CRTPPacket, CRTPPort

from .base import CrazyflieSubsystem
from .platform import PlatformChannel

__all__ = ("AppChannel",)


class AppChannel(CrazyflieSubsystem):
    """Class representing the handler of app channel messages for a Crazyflie
    instance.
    """

    def get_port(self) -> CRTPPort:
        return CRTPPort.PLATFORM

    async def packets(self) -> AsyncIterator[CRTPPacket]:
        """Async generator that yields messages coming on the app-specific
        channel from a Crazyflie.
        """
        async for packet in super().packets():
            if packet.channel == PlatformChannel.APP_CHANNEL:
                yield packet
