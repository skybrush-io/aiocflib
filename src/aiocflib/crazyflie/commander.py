"""Classes related to sending low-level roll-pitch-yaw-thrust and setpoint
messages to a Crazyflie.
"""

from enum import IntEnum
from struct import Struct
from typing import ClassVar

from aiocflib.crtp import CRTPPort

from .crazyflie import Crazyflie

__all__ = ("Commander",)


class SetpointType(IntEnum):
    """Enum representing the setpoint types that we can send to the generic
    commander port.
    """

    STOP = 0
    VELOCITY_WORLD = 1
    Z_DISTANCE = 2
    CPPM = 3
    ALTITUDE_HOLD = 4
    HOVER = 5
    FULL_STATE = 6
    POSITION = 7


class Commander:
    """Class responsible for sending low-level roll-pitch-yaw-thrust and
    setpoint messages to a Crazyflie instance.
    """

    _crazyflie: Crazyflie

    _altitude_hold_setpoint_struct: ClassVar[Struct] = Struct("<Bffff")
    _hover_setpoint_struct: ClassVar[Struct] = Struct("<Bffff")
    _position_setpoint_struct: ClassVar[Struct] = Struct("<Bffff")
    _rpyt_setpoint_struct: ClassVar[Struct] = Struct("<fffH")
    _velocity_world_setpoint_struct: ClassVar[Struct] = Struct("<Bffff")
    _z_distance_setpoint_struct: ClassVar[Struct] = Struct("<Bffff")

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie to which we need to send the messages
        """
        self._crazyflie = crazyflie

    async def send_altitude_hold_setpoint(
        self,
        roll: float = 0.0,
        pitch: float = 0.0,
        yaw_rate: float = 0.0,
        z_velocity: float = 0.0,
    ) -> None:
        """Sends an altitude hold setpoint to the Crazyflie; velocity is
        specified along the Z axis, while the XY axes are controlled by roll
        and pitch angles.

        Roll and pitch are in degrees. Yaw rate is in degrees/s. Z velocity is
        in m/s.
        """
        data = self._altitude_hold_setpoint_struct.pack(
            SetpointType.ALTITUDE_HOLD, roll, pitch, yaw_rate, z_velocity
        )
        await self._crazyflie.send_packet(port=CRTPPort.GENERIC_COMMANDER, data=data)

    async def send_hover_setpoint(
        self,
        vx: float = 0.0,
        vy: float = 0.0,
        yaw_rate: float = 0.0,
        z_distance: float = 0.0,
    ) -> None:
        """Sends a hover setpoint to the Crazyflie; velocity is specified in the
        XY plane, along with the desired distance from the ground and the yaw
        rate.

        Velocity components are in m/s. Yaw rate is in degrees/s. Z distance is
        in meters.
        """
        data = self._hover_setpoint_struct.pack(
            SetpointType.HOVER, vx, vy, yaw_rate, z_distance
        )
        await self._crazyflie.send_packet(port=CRTPPort.GENERIC_COMMANDER, data=data)

    async def send_position_setpoint(
        self,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        yaw: float = 0.0,
    ) -> None:
        """Sends a position setpoint to the Crazyflie with a fixed X-Y-Z
        coordinate triplet and a yaw angle.

        Coordinates are in meters; yaw is in degrees.
        """
        data = self._position_setpoint_struct.pack(SetpointType.POSITION, x, y, z, yaw)
        await self._crazyflie.send_packet(port=CRTPPort.GENERIC_COMMANDER, data=data)

    async def send_setpoint(
        self, roll: float, pitch: float, yaw: float, thrust: int
    ) -> None:
        """Sends a low-level roll-pitch-yaw-thrust setpoint to the
        Crazyflie.

        Thrust is automatically capped between 0 and 65535. Roll, pitch and
        yaw are in degrees.
        """
        if thrust > 0xFFFF:
            thrust = 0xFFFF
        elif thrust < 0:
            thrust = 0

        data = self._rpyt_setpoint_struct.pack(roll, -pitch, yaw, thrust)
        await self._crazyflie.send_packet(port=CRTPPort.COMMANDER, data=data)

    async def send_stop_setpoint(self) -> None:
        """Sends an immediate stop command, stopping the motors and potentially
        making the Crazyflie fall down and crash.
        """
        await self._crazyflie.send_packet(
            port=CRTPPort.GENERIC_COMMANDER, data=SetpointType.STOP
        )

    async def send_velocity_world_setpoint(
        self, vx: float = 0.0, vy: float = 0.0, vz: float = 0.0, yaw_rate: float = 0.0
    ) -> None:
        """Sends a low-level velocity setpoint in the world coordinate frame to
        the Crazyflie.

        Thrust is automatically capped between 0 and 65535. Velocity components
        are in m/s.
        """
        data = self._velocity_world_setpoint_struct.pack(
            SetpointType.VELOCITY_WORLD, vx, vy, vz, yaw_rate
        )
        await self._crazyflie.send_packet(port=CRTPPort.GENERIC_COMMANDER, data=data)

    async def send_z_distance_setpoint(
        self,
        roll: float = 0.0,
        pitch: float = 0.0,
        yaw_rate: float = 0.0,
        z_distance: float = 0.0,
    ) -> None:
        """Sends a low-level setpoint where the height is defined as an absolute
        setpoint along with the desired roll and pitch angles and the yaw rate.

        Roll and pitch are in degrees; yaw rate is in degrees/s.
        """
        data = self._z_distance_setpoint_struct.pack(
            SetpointType.Z_DISTANCE, roll, pitch, yaw_rate, z_distance
        )
        await self._crazyflie.send_packet(port=CRTPPort.GENERIC_COMMANDER, data=data)

    stop = send_stop_setpoint
