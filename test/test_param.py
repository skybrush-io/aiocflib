"""Tests for the parameter subsystem of the Crazyflie."""

from struct import Struct
from typing import Any

from pytest import fixture, mark

from aiocflib.crazyflie.param import (
    ParameterChannel,
    Parameters,
    ParameterSpecification,
    ParameterType,
)
from aiocflib.crtp.crtpstack import CRTPPort

pytestmark = mark.anyio


@fixture
def anyio_backend():
    return "trio"


class FakeCrazyflie:
    """Fake Crazyflie that answers parameter read requests."""

    def __init__(self, values: dict[int, int]):
        self.values = values
        self.reads: list[int] = []

    def _get_cache_for(self, name: str) -> None:
        return None

    async def run_command(
        self, *, port: int, channel: int, command: Any, data: bytes = b""
    ) -> bytes:
        assert port == CRTPPort.PARAMETERS
        assert channel == ParameterChannel.READ
        index = command[0] | (command[1] << 8)
        self.reads.append(index)
        return bytes([0]) + Struct("<B").pack(self.values[index])


def create_parameters(cf: FakeCrazyflie) -> Parameters:
    spec = ParameterSpecification(
        id=3,
        type=ParameterType.UINT8,
        group="locSrv",
        name="enLhMtchStm",
        read_only=False,
        has_extended_info=False,
    )

    async def fake_validate():
        return [spec], {spec.full_name: spec}

    parameters = Parameters(cf)  # type: ignore
    parameters._validate = fake_validate  # type: ignore[method-assign]
    return parameters


async def test_get_downloads_toc_on_first_use():
    cf = FakeCrazyflie({3: 42})
    parameters = create_parameters(cf)

    assert await parameters.get("locSrv.enLhMtchStm") == 42
    assert cf.reads == [3]

    # The second query is answered from the cache
    assert await parameters.get("locSrv.enLhMtchStm") == 42
    assert cf.reads == [3]

    cf.values[3] = 7
    assert await parameters.get("locSrv.enLhMtchStm", fetch=True) == 7
    assert cf.reads == [3, 3]
