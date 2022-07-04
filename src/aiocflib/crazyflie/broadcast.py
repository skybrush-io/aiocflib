"""Functions for broadcasting packets that are typically sent to multiple
Crazyflie drones using a single radio.
"""

from typing import Sequence, Tuple, TYPE_CHECKING

from aiocflib.crtp import CRTPPort
from aiocflib.utils.quaternion import QuaternionXYZW

from .localization import GenericLocalizationCommand, Localization, LocalizationChannel

if TYPE_CHECKING:
    from aiocflib.crtp.broadcaster import _Broadcaster


async def broadcast_external_position_packed(
    broadcaster: "_Broadcaster", items: Sequence[Tuple[int, Tuple[float, float, float]]]
) -> None:
    """Broadcasts a packet containing external position information for multiple
    Crazyflies using the given broadcaster.

    Parameters:
        broadcaster: the broadcaster to use
        items: a sequence of pairs containing a numeric Crazyflie ID
            (the last byte of its radio address) and a 3D coordinate.
            Coordinates must be less than ~32.7 meters in absolute value.
            At most four items fit into a single Crazyflie CRTP packet.
    """
    data = Localization.encode_external_position_packed(items)
    await broadcaster.send_packet(
        port=CRTPPort.LOCALIZATION,
        channel=LocalizationChannel.POSITION_PACKED,
        data=data,
    )


async def broadcast_external_pose_packed(
    broadcaster: "_Broadcaster",
    items: Sequence[Tuple[int, Tuple[float, float, float], QuaternionXYZW]],
) -> None:
    """Broadcasts a packet containing external pose (position + attitude)
    information for multiple Crazyflies using the given broadcaster.

    Parameters:
        broadcaster: the broadcaster to use
        items: a sequence of triplets containing a numeric Crazyflie ID
            (the last byte of its radio address), a 3D coordinate and a
            4D quaternion in XYZW order. Coordinates must be less than ~32.7
            meters in absolute value. At most two items fit into a single
            Crazyflie CRTP packet.
    """
    data = Localization.encode_external_pose_packed(items)
    await broadcaster.send_packet(
        port=CRTPPort.LOCALIZATION,
        channel=LocalizationChannel.GENERIC,
        data=bytes([GenericLocalizationCommand.EXT_POSE_PACKED]) + data,
    )


async def broadcast_emergency_stop(broadcaster: "_Broadcaster") -> None:
    """Broadcasts an emergency stop packet for multiple Crazyflies using the
    given broadcaster.

    Parameters:
        broadcaster: the broadcaster to use
    """
    await broadcaster.send_packet(
        port=CRTPPort.LOCALIZATION,
        channel=LocalizationChannel.GENERIC,
        data=bytes([GenericLocalizationCommand.ENABLE_EMERGENCY_STOP]),
    )
