from typing import Any

from pytest import fixture, mark

from aiocflib.crazyflie.localization import (
    GenericLocalizationCommand,
    Localization,
    LocalizationChannel,
)
from aiocflib.crtp import CRTPPort

pytestmark = mark.anyio


@fixture
def anyio_backend():
    return "trio"


class FakeCrazyflie:
    """Fake Crazyflie that responds to LPP short packets like the firmware
    does.
    """

    def __init__(self, success: bool):
        self.requests: list[dict[str, Any]] = []
        self.success = success

    async def run_command(self, **kwds: Any) -> bytes:
        self.requests.append(kwds)
        # The firmware sends back the command byte, the destination ID and a
        # success flag; run_command() strips the command byte
        dest_id = kwds["data"][0]
        return bytes([dest_id, int(self.success)])


@mark.parametrize("dest_id", [0, 7])
@mark.parametrize("success", [False, True])
async def test_send_lpp_short_packet(dest_id: int, success: bool):
    cf = FakeCrazyflie(success)
    localization = Localization(cf)  # type: ignore

    assert await localization.send_lpp_short_packet(dest_id, b"\x01abc") is success
    assert cf.requests == [
        {
            "port": CRTPPort.LOCALIZATION,
            "channel": LocalizationChannel.GENERIC,
            "command": GenericLocalizationCommand.LPP_SHORT_PACKET,
            "data": bytes([dest_id]) + b"\x01abc",
        }
    ]
