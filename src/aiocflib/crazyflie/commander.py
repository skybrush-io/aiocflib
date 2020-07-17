"""Classes related to sending low-level roll-pitch-yaw-thrust and setpoint
messages to a Crazyflie.
"""

from enum import IntEnum
from struct import Struct

from aiocflib.crtp import CRTPPort

from .crazyflie import Crazyflie

__all__ = ("Commander",)


class SetpointType(IntEnum):
    """Enum representing the setpoint types that we can send to the generic
    commander port.
    """

    STOP = 0
    VELOCITY_WORLD = 1  # vx, vy, vz, yawrate, <Bffff
    ZDISTANCE = 2  # roll, pitch, yawrate, zdistance, <Bffff
    HOVER = 5  # vx, vy, yawrate, zdistance, <Bffff
    POSITION = 7  # x, y, z, yaw, <Bffff


class Commander:
    """Class responsible for sending low-level roll-pitch-yaw-thrust and
    setpoint messages to a Crazyflie instance.
    """

    _setpoint_struct = Struct("<fffH")

    def __init__(self, crazyflie: Crazyflie, x_mode: bool = False):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie to which we need to send the messages
            x_mode: whether to enable client-side X-mode. This will
                recalculate the setpoints before sending them to the
        """
        self._crazyflie = crazyflie
        self._x_mode = False

    def set_client_xmode(self, enabled):
        """Enables or disables the client-side X-mode of the commander. When
        the mode is enabled, setpoints are automatically recalculated in the
        client to treat a cross-framed drone as if it was an X-framed one.

        This function is kept for sake of compatibility with the official
        Crazyflie Python library. You can also use the `x_mode` property.
        """
        self.x_mode = enabled

    async def send_setpoint(self, roll, pitch, yaw, thrust):
        """Sends a low-level roll-pitch-yaw-thrust setpoint to the
        Crazyflie.

        Thrust is automatically capped between 0 and 65535. Roll, pitch and
        yaw are in degrees.
        """
        if thrust > 0xFFFF:
            thrust = 0xFFFF
        elif thrust < 0:
            thrust = 0

        if self._x_mode:
            roll, pitch = 0.707 * (roll - pitch), 0.707 * (roll + pitch)

        data = self._setpoint_struct.pack(roll, -pitch, yaw, thrust)
        await self._crazyflie.send_packet(port=CRTPPort.COMMANDER, data=data)

    async def send_stop_setpoint(self):
        """Sends an immediate stop command, stopping the motors and potentially
        making the Crazyflie fall down and crash.
        """
        await self._crazyflie.send_packet(
            port=CRTPPort.COMMANDER_GENERIC, data=SetpointType.STOP
        )

    stop = send_stop_setpoint

    @property
    def x_mode(self):
        """Returns whether the commander is in client-side X-mode. When the
        mode is enabled, setpoints are automatically recalculated in the
        client to treat a cross-framed drone as if it was an X-framed one.
        """
        return self._x_mode

    @x_mode.setter
    def x_mode(self, value):
        self._x_mode = bool(value)
