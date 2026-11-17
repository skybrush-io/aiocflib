"""Tests for the Loco positioning subsystem of the Crazyflie."""

from struct import Struct

from pytest import fixture, mark, raises

from aiocflib.crazyflie.loco import Loco, LppShortPacketType
from aiocflib.crtp.crtpstack import MemoryType

pytestmark = mark.anyio


@fixture
def anyio_backend():
    return "trio"


class FakeLocoMemory:
    """Fake Loco memory that answers read requests the same way as the
    firmware of the Crazyflie.
    """

    def __init__(
        self,
        positions: dict[int, tuple[float, float, float] | None],
        active_ids: list[int],
    ):
        self.positions = positions
        self.active_ids = active_ids
        self.reads: list[tuple[int, int]] = []

    async def read(self, addr: int, length: int) -> bytes:
        self.reads.append((addr, length))
        if addr < 0x1000:
            return self._read_id_list(addr, length, 0x0000, list(self.positions))
        elif addr < 0x2000:
            return self._read_id_list(addr, length, 0x1000, self.active_ids)

        page_address = addr - 0x2000
        if page_address % 0x100 or length != 13:
            raise OSError("read failed")

        position = self.positions.get(page_address // 0x100)
        x, y, z = position or (0.0, 0.0, 0.0)
        return Struct("<fffB").pack(x, y, z, position is not None)

    @staticmethod
    def _read_id_list(addr: int, length: int, base: int, ids: list[int]) -> bytes:
        data = [len(ids), *ids]
        return bytes(
            data[index] if index < len(data) else 0
            for index in range(addr - base, addr - base + length)
        )


class FakeMemory:
    def __init__(self, loco: FakeLocoMemory | None):
        self.loco = loco
        self.lookups = 0

    async def find(self, type: MemoryType) -> FakeLocoMemory:
        assert type == MemoryType.LOCO2
        self.lookups += 1
        if self.loco is None:
            raise ValueError(f"no memory matching type {type!r}")
        return self.loco


class FakeLocalization:
    def __init__(self, accept: bool = True):
        self.accept = accept
        self.packets: list[tuple[int, bytes]] = []

    async def send_lpp_short_packet(self, dest_id: int, data: bytes) -> bool:
        self.packets.append((dest_id, data))
        return self.accept


class FakeCrazyflie:
    def __init__(self, loco: FakeLocoMemory | None = None, accept: bool = True):
        self.mem = FakeMemory(loco)
        self.localization = FakeLocalization(accept)


def create_loco(memory: FakeLocoMemory | None = None, accept: bool = True) -> Loco:
    return Loco(FakeCrazyflie(memory, accept))  # type: ignore


async def test_get_anchor_positions():
    memory = FakeLocoMemory(
        positions={3: (1.0, -2.5, 0.25), 0: None, 7: (4.0, 5.0, 6.0)},
        active_ids=[7, 3],
    )
    positions = await create_loco(memory).get_anchor_positions()

    assert positions == {3: (1.0, -2.5, 0.25), 0: None, 7: (4.0, 5.0, 6.0)}
    assert list(positions) == [3, 0, 7]
    assert memory.reads == [
        (0x0000, 1),
        (0x0001, 3),
        (0x2300, 13),
        (0x2000, 13),
        (0x2700, 13),
    ]


async def test_get_anchor_positions_without_anchors():
    memory = FakeLocoMemory(positions={}, active_ids=[])
    assert await create_loco(memory).get_anchor_positions() == {}
    assert memory.reads == [(0x0000, 1)]


async def test_get_anchor_positions_with_large_anchor_ids():
    positions: dict[int, tuple[float, float, float] | None] = {
        anchor_id: (float(anchor_id), 0.0, 0.0) for anchor_id in range(240, 256)
    }
    memory = FakeLocoMemory(positions=positions, active_ids=[])
    assert await create_loco(memory).get_anchor_positions() == positions


async def test_get_anchor_ids():
    memory = FakeLocoMemory(positions={3: None, 0: None, 7: None}, active_ids=[7, 3])
    loco = create_loco(memory)
    assert await loco.get_anchor_ids() == [3, 0, 7]
    assert await loco.get_active_anchor_ids() == [7, 3]


async def test_memory_is_looked_up_once():
    cf = FakeCrazyflie(FakeLocoMemory(positions={1: None}, active_ids=[]))
    loco = Loco(cf)  # type: ignore
    await loco.get_anchor_positions()
    await loco.get_active_anchor_ids()
    assert cf.mem.lookups == 1


async def test_get_anchor_positions_without_loco_deck():
    with raises(ValueError, match="no memory"):
        await create_loco().get_anchor_positions()


async def test_set_anchor_position():
    cf = FakeCrazyflie()
    assert await Loco(cf).set_anchor_position(5, (1.5, -2.0, 3.25))  # type: ignore
    assert cf.localization.packets == [
        (
            5,
            bytes([LppShortPacketType.ANCHOR_POSITION])
            + Struct("<fff").pack(1.5, -2.0, 3.25),
        )
    ]


async def test_set_anchor_position_rejected():
    loco = create_loco(accept=False)
    assert not await loco.set_anchor_position(5, (0.0, 0.0, 0.0))
