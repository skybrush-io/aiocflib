"""Classes related to accessing the supervisor subsystem of a Crazyflie."""

from contextlib import asynccontextmanager
from enum import IntEnum, IntFlag

from aiocflib.crtp import CRTPPort

from .base import CrazyflieSubsystem

__all__ = ("Supervisor",)


class SupervisorChannel(IntEnum):
    """Enum representing the names of the channels of the supervisor service in
    the CRTP protocol.
    """

    STATE_INFO = 0
    COMMANDS = 1


class SupervisorCommand(IntEnum):
    """Enum representing the names of the commands in the command channel of the
    supervisor service of
    the CRTP protocol.
    """

    ARM_DISARM = 1
    RECOVER_SYSTEM = 2
    EMERGENCY_STOP = 3
    EMERGENCY_STOP_WATCHDOG = 4


class StateInfoCommand(IntEnum):
    """Enum representing the names of the commands in the state info channel of
    the supervisor service in the CRTP protocol.
    """

    CAN_BE_ARMED = 1
    IS_ARMED = 2
    IS_AUTO_ARMED = 3
    CAN_FLY = 4
    IS_FLYING = 5
    IS_TUMBLED = 6
    IS_LOCKED = 7
    IS_CRASHED = 8
    HL_CONTROL_ACTIVE = 9
    HL_TRAJ_FINISHED = 10
    HL_CONTROL_DISABLED = 11
    GET_STATE_BITFIELD = 12


class StateBitfield(IntFlag):
    """Enum representing the bits of the state bitfield of the state info channel of the
    supervisor service in the CRTP protocol.
    """

    CAN_BE_ARMED = 1 << 0
    IS_ARMED = 1 << 1
    IS_AUTO_ARMED = 1 << 2
    CAN_FLY = 1 << 3
    IS_FLYING = 1 << 4
    IS_TUMBLED = 1 << 5
    IS_LOCKED = 1 << 6
    IS_CRASHED = 1 << 7
    HL_CONTROL_ACTIVE = 1 << 8
    HL_TRAJ_FINISHED = 1 << 9
    HL_CONTROL_DISABLED = 1 << 10


class Supervisor(CrazyflieSubsystem):
    """Class representing the handler of supervisor messages of a Crazyflie instance."""

    def get_port(self) -> CRTPPort:
        return CRTPPort.SUPERVISOR

    async def arm(self) -> None:
        """Arms the Crazyflie."""
        return await self.arm_or_disarm(arm=True)

    async def arm_or_disarm(self, arm: bool) -> None:
        """Arms or disarms the Crazyflie."""
        response = await self._crazyflie.run_command(
            port=self.get_port(),
            channel=SupervisorChannel.COMMANDS,
            command=SupervisorCommand.ARM_DISARM,
            data=[1 if arm else 0],
            flip_msb=True,
        )
        if len(response) < 1:
            raise RuntimeError(
                f"Supervisor command {SupervisorCommand.ARM_DISARM} returned "
                f"an invalid response: {response.hex(' ')}"
            )
        if not response[0]:
            raise RuntimeError(
                "Failed to arm the Crazyflie."
                if arm
                else "Failed to disarm the Crazyflie."
            )

    @asynccontextmanager
    async def armed(self):
        """Context manager that arms the Crazyflie on entry and disarms it on exit."""
        await self.arm()
        try:
            yield
        finally:
            await self.disarm()

    async def disarm(self) -> None:
        """Disarmd the Crazyflie."""
        return await self.arm_or_disarm(arm=False)

    async def can_be_armed(self) -> bool:
        """Returns whether the Crazyflie can be armed."""
        return await self._send_state_info_command(StateInfoCommand.CAN_BE_ARMED)

    async def is_armed(self) -> bool:
        """Returns whether the Crazyflie is armed."""
        return await self._send_state_info_command(StateInfoCommand.IS_ARMED)

    async def is_auto_armed(self) -> bool:
        """Returns whether the Crazyflie is auto-armed."""
        return await self._send_state_info_command(StateInfoCommand.IS_AUTO_ARMED)

    async def can_fly(self) -> bool:
        """Returns whether the Crazyflie can fly."""
        return await self._send_state_info_command(StateInfoCommand.CAN_FLY)

    async def is_flying(self) -> bool:
        """Returns whether the Crazyflie is flying."""
        return await self._send_state_info_command(StateInfoCommand.IS_FLYING)

    async def is_tumbled(self) -> bool:
        """Returns whether the Crazyflie is tumbled."""
        return await self._send_state_info_command(StateInfoCommand.IS_TUMBLED)

    async def is_locked(self) -> bool:
        """Returns whether the Crazyflie is locked."""
        return await self._send_state_info_command(StateInfoCommand.IS_LOCKED)

    async def is_crashed(self) -> bool:
        """Returns whether the Crazyflie has crashed."""
        return await self._send_state_info_command(StateInfoCommand.IS_CRASHED)

    async def is_high_level_control_active(self) -> bool:
        """Returns whether the high-level control is active on the Crazyflie."""
        return await self._send_state_info_command(StateInfoCommand.HL_CONTROL_ACTIVE)

    async def is_high_level_trajectory_finished(self) -> bool:
        """Returns whether the high-level trajectory is finished on the Crazyflie."""
        return await self._send_state_info_command(StateInfoCommand.HL_TRAJ_FINISHED)

    async def is_high_level_control_disabled(self) -> bool:
        """Returns whether the high-level control is disabled on the Crazyflie."""
        return await self._send_state_info_command(StateInfoCommand.HL_CONTROL_DISABLED)

    async def get_state(self) -> StateBitfield:
        """Returns the state bitfield of the Crazyflie."""
        response = await self._crazyflie.run_command(
            port=self.get_port(),
            channel=SupervisorChannel.STATE_INFO,
            command=StateInfoCommand.GET_STATE_BITFIELD,
            flip_msb=True,
        )
        if len(response) != 2:
            raise RuntimeError(
                f"Supervisor command {StateInfoCommand.GET_STATE_BITFIELD} returned "
                f"an invalid response: {response.hex(' ')}"
            )
        return StateBitfield(int.from_bytes(response, byteorder="little"))

    async def _send_state_info_command(self, command: StateInfoCommand) -> bool:
        response = await self._crazyflie.run_command(
            port=self.get_port(),
            channel=SupervisorChannel.STATE_INFO,
            command=command,
            flip_msb=True,
        )
        if len(response) != 1:
            raise RuntimeError(
                f"Supervisor command {StateInfoCommand.GET_STATE_BITFIELD} returned "
                f"an invalid response: {response.hex(' ')}"
            )
        return bool(response[0])
