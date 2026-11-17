"""Loco positioning subsystem of a Crazyflie.

The Loco positioning system consists of ultra-wideband (UWB) anchors placed
around the flight area. Each anchor stores its own position and broadcasts it
to the drones. A drone with a Loco positioning deck keeps a list of the anchors
that it has heard from, together with their positions, and exposes the list
in a read-only memory. The drone can also relay short LPP (Loco Positioning
Protocol) packets to the anchors, e.g., to change the position stored in an
anchor.
"""

from enum import IntEnum
from struct import Struct
from typing import ClassVar

from aiocflib.crtp.crtpstack import MemoryType

from .crazyflie import Crazyflie
from .mem import MemoryHandler

__all__ = ("Loco", "LppShortPacketType")


Vector3D = tuple[float, float, float]


class LppShortPacketType(IntEnum):
    """Types of the short LPP packets that a Crazyflie can relay to the Loco
    anchors.
    """

    ANCHOR_POSITION = 1
    REBOOT = 2
    MODE = 3


class Loco:
    """Class representing the Loco positioning subsystem of a Crazyflie
    instance.
    """

    ID_LIST_ADDR: ClassVar[int] = 0x0000
    ACTIVE_ID_LIST_ADDR: ClassVar[int] = 0x1000
    ANCHOR_START_ADDR: ClassVar[int] = 0x2000
    PAGE_SIZE: ClassVar[int] = 0x100

    _anchor_page_struct: ClassVar[Struct] = Struct("<fffB")
    _anchor_position_struct: ClassVar[Struct] = Struct("<Bfff")

    _crazyflie: Crazyflie
    _mem: MemoryHandler | None

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie instance
        """
        self._crazyflie = crazyflie
        self._mem = None

    async def get_active_anchor_ids(self) -> list[int]:
        """Returns the IDs of the anchors that the Crazyflie has heard from
        recently.
        """
        return await self._get_anchor_id_list(self.ACTIVE_ID_LIST_ADDR)

    async def get_anchor_ids(self) -> list[int]:
        """Returns the IDs of all the anchors that the Crazyflie has heard
        from.
        """
        return await self._get_anchor_id_list(self.ID_LIST_ADDR)

    async def get_anchor_position(self, anchor_id: int) -> Vector3D | None:
        """Returns the position of a single anchor as known by the Crazyflie.

        Parameters:
            anchor_id: the ID of the anchor

        Returns:
            the position of the anchor, or ``None`` if the Crazyflie does not
            know the position of the anchor
        """
        mem = await self._get_memory()
        data = await mem.read(
            self._get_address_of_anchor(anchor_id), self._anchor_page_struct.size
        )
        x, y, z, valid = self._anchor_page_struct.unpack(data)
        return (x, y, z) if valid else None

    async def get_anchor_positions(self) -> dict[int, Vector3D | None]:
        """Returns the positions of all the anchors that the Crazyflie has
        heard from, keyed by anchor ID.

        The position of an anchor is ``None`` if the Crazyflie has heard from
        the anchor but does not know its position yet.
        """
        return {
            anchor_id: await self.get_anchor_position(anchor_id)
            for anchor_id in await self.get_anchor_ids()
        }

    async def set_anchor_position(self, anchor_id: int, position: Vector3D) -> bool:
        """Asks the Crazyflie to send a new position to an anchor.

        The anchor does not acknowledge the packet. The only way to confirm that
        the anchor received it is to wait until the Crazyflie reports the new
        position of the anchor.

        Parameters:
            anchor_id: the ID of the anchor
            position: the new position of the anchor, in metres

        Returns:
            whether the Crazyflie accepted the packet for sending
        """
        x, y, z = position
        return await self._crazyflie.localization.send_lpp_short_packet(
            anchor_id,
            self._anchor_position_struct.pack(
                LppShortPacketType.ANCHOR_POSITION, x, y, z
            ),
        )

    async def _get_anchor_id_list(self, address: int) -> list[int]:
        """Reads a list of anchor IDs from the Loco memory of the Crazyflie.

        The list starts with the number of IDs, followed by the IDs themselves.
        """
        mem = await self._get_memory()
        (count,) = await mem.read(address, 1)
        return list(await mem.read(address + 1, count)) if count else []

    def _get_address_of_anchor(self, anchor_id: int) -> int:
        """Returns the address of the data of the anchor with the given ID."""
        return self.ANCHOR_START_ADDR + anchor_id * self.PAGE_SIZE

    async def _get_memory(self) -> MemoryHandler:
        """Returns the memory handler object of the Loco memory.

        Raises:
            ValueError: if the Crazyflie has no Loco memory
        """
        if self._mem is None:
            self._mem = await self._crazyflie.mem.find(MemoryType.LOCO2)
        return self._mem
