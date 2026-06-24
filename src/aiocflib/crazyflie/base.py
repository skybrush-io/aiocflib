from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from aiocflib.crtp import CRTPPacket, CRTPPort

__all__ = ("CrazyflieSubsystem",)

if TYPE_CHECKING:
    from .crazyflie import Crazyflie


class CrazyflieSubsystem:
    """Base class for Crazyflie subsystem handlers."""

    _crazyflie: Crazyflie

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie instance that owns this subsystem
        """
        self._crazyflie = crazyflie

    @abstractmethod
    def get_port(self) -> CRTPPort:
        """Returns the CRTP port of the subsystem."""
        ...

    async def packets(self) -> AsyncIterator[CRTPPacket]:
        """Yields packets related to the CRTP port of the subsystem.

        If the subsystem is not directly related to a single CRTP port, this
        generator yields no packets.
        """
        async for packet in self._crazyflie.packets(port=self.get_port()):
            yield packet
