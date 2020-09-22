"""Classes related to accessing the localization subsystem of a Crazyflie."""

from enum import IntEnum
from struct import Struct
from typing import Optional, Sequence, Union

from aiocflib.crtp import CRTPPort

from .crazyflie import Crazyflie

__all__ = ("Localization",)


class LocalizationChannel(IntEnum):
    """Enum representing the names of the channels in the localization service
    of the CRTP protocol.
    """

    EXTERNAL_POSITION = 0
    GENERIC = 1


class GenericLocalizationCommand(IntEnum):
    """Enum representing the names of the commands in the generic channel of the
    localization service of the CRTP protocol.
    """

    RANGE_STREAM_REPORT = 0
    RANGE_STREAM_REPORT_FP16 = 1
    LPP_SHORT_PACKET = 2
    ENABLE_EMERGENCY_STOP = 3
    RESET_EMERGENCY_STOP_TIMEOUT = 4
    COMM_GNSS_NMEA = 6
    COMM_GNSS_PROPRIETARY = 7
    EXT_POSE = 8
    EXT_POSE_PACKED = 9


class Localization:
    """Class representing the handler of messages related to the localization
    subsystem of a Crazyflie instance.
    """

    _external_position_struct = Struct("<fff")
    _external_pose_struct = Struct("<Bfffffff")

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle the localization
                subsystem related messages
        """
        self._crazyflie = crazyflie

    async def send_external_position(
        self,
        x: Union[float, Sequence[float]],
        y: Optional[float] = None,
        z: Optional[float] = None,
    ) -> None:
        """Sends position information originating from an external positioning
        system into the Crazyflie.

        Parameters:
            x: the X coordinate. May also be a full 3D position vector; in this
                case y and z must be `None`.
            y: the Y coordinate
            z; the Z coordinate
        """
        if y is None and z is None:
            x, y, z = x
        await self._crazyflie.send_packet(
            port=CRTPPort.LOCALIZATION,
            channel=LocalizationChannel.EXTERNAL_POSITION,
            data=self._external_position_struct.pack(x, y, z),
        )

    async def send_external_pose(
        self, pos: Sequence[float], quat: Sequence[float]
    ) -> None:
        """Sends pose (position and attitude) information originating from an
        external positioning system into the Crazyflie.

        Parameters:
            pos: the position vector (x, y, z)
            quat: the attitude quaternion (qx, qy, qz, qw)
        """
        x, y, z = pos
        qx, qy, qz, qw = quat
        await self._crazyflie.send_packet(
            port=CRTPPort.LOCALIZATION,
            channel=LocalizationChannel.GENERIC,
            data=self._external_pose_struct.pack(
                GenericLocalizationCommand.EXT_POSE, x, y, z, qx, qy, qz, qw
            ),
        )

    async def send_lpp_short_packet(self, data: bytes) -> None:
        """Sends an LPP short packet to the Loco Positioning System, using the
        Crazyflie as a proxy.

        Parameters:
            data: the raw LPP short packet to send
        """
        await self._crazyflie.send_packet(
            port=CRTPPort.LOCALIZATION,
            channel=LocalizationChannel.GENERIC,
            command=GenericLocalizationCommand.LPP_SHORT_PACKET,
            data=data,
        )
