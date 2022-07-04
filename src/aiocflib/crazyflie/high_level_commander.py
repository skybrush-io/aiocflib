"""Classes related to sending high-level navigation commands to a Crazyflie.
"""

from contextlib import asynccontextmanager
from enum import IntEnum
from math import radians
from struct import Struct
from typing import AsyncIterator, ClassVar, Optional

from aiocflib.crtp import CRTPCommandLike, CRTPDataLike, CRTPPort
from aiocflib.errors import CRTPCommandError

from .crazyflie import Crazyflie

__all__ = ("HighLevelCommander", "HighLevelCommanderError")


class HighLevelCommanderError(CRTPCommandError):
    """CRTPCommandError subclass for errors emitted from the high-level
    commander module.
    """

    pass


class HighLevelCommand(IntEnum):
    """Enum representing the names of the high-level navigation commands in the
    high-level commander service of the CRTP protocol.
    """

    SET_GROUP_MASK = 0
    TAKEOFF = 1
    LAND = 2
    STOP = 3
    GO_TO = 4
    START_TRAJECTORY = 5
    DEFINE_TRAJECTORY = 6
    TAKEOFF_2 = 7
    LAND_2 = 8
    TAKEOFF_WITH_VELOCITY = 9
    LAND_WITH_VELOCITY = 10


class TrajectoryType(IntEnum):
    """Enum representing the possible trajectory types supported by the
    Crazyflie.
    """

    POLY4D = 0
    COMPRESSED = 1


class TrajectoryLocation(IntEnum):
    """Enum representing the possible locations where a trajectory can reside
    on the Crazyflie.
    """

    INVALID = 0
    MEMORY = 1


#: Default group mask representing "all groups"
ALL_GROUPS = 0


class HighLevelCommander:
    """Class responsible for sending high-level navigation commands to a
    Crazyflie.
    """

    _crazyflie: Crazyflie

    _define_trajectory_struct: ClassVar[Struct] = Struct("<BBIB")
    _go_to_struct: ClassVar[Struct] = Struct("<Bfffff")
    _land_struct: ClassVar[Struct] = Struct("<ff?f")
    _land_with_velocity_struct: ClassVar[Struct] = Struct("<f?f?f")
    _start_trajectory_struct: ClassVar[Struct] = Struct("<BBBf")
    _takeoff_struct: ClassVar[Struct] = Struct("<ff?f")
    _takeoff_with_velocity_struct: ClassVar[Struct] = Struct("<f?f?f")

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie to which we need to send the messages
        """
        self._crazyflie = crazyflie

    async def define_trajectory(
        self,
        id: int,
        *,
        addr: int,
        num_pieces: int = 0,
        location: TrajectoryLocation = TrajectoryLocation.MEMORY,
        type: TrajectoryType = TrajectoryType.POLY4D,
    ) -> None:
        """Defines a trajectory in the trajectory memory of the Crazyflie.

        Parameters:
            id: ID of the trajectory to define
            addr: address where the trajectory starts in the trajectory memory
            num_pieces: number of segments in the trajectory if it is stored in
                uncompressed format; ignored for compressed trajectories
            location: specifies where (in which memory) the trajectory is on the
                Crazyflie
            type: specifies the type (encoding) of the trajectory
        """
        data = self._define_trajectory_struct.pack(location, type, addr, num_pieces)
        await self._run_command(
            command=(HighLevelCommand.DEFINE_TRAJECTORY, id), data=data
        )

    async def disable(self) -> None:
        """Disables the high-level controller on the Crazyflie."""
        await self._crazyflie.parameters.set("commander.enHighLevel", 0)

    async def enable(self) -> None:
        """Enables the high-level controller on the Crazyflie."""
        await self._crazyflie.parameters.set("commander.enHighLevel", 1)

    @asynccontextmanager
    async def enabled(self) -> AsyncIterator[None]:
        """Async context manager that enables the high-level controller when
        entering the context and disables it when exiting the context.
        """
        async with self._crazyflie.parameters.set_and_restore(
            "commander.enHighLevel", 1, 0
        ):
            yield

    async def go_to(
        self,
        x: float,
        y: float,
        z: float,
        *,
        duration: float,
        yaw: float = 0.0,
        relative: bool = False,
        group_mask: int = ALL_GROUPS,
    ) -> None:
        """Sends a command to navigate to the given absolute or relative
        position.

        Parameters:
            x: the X coordinate of the new position
            y: the Y coordinate of the new position
            z: the Z coordinate of the new position
            yaw: the yaw angle to reach at the new position, in degrees
            duration: duration of the movement, in seconds
            relative: defines whether the coordinates are absolute (`False`)
                or relative to the current position (`True`)
            group_mask: mask that defines which Crazyflie drones this command
                should apply to
        """
        data = self._go_to_struct.pack(
            relative,
            x,
            y,
            z,
            radians(yaw),
            duration,
        )
        await self._run_command(command=(HighLevelCommand.GO_TO, group_mask), data=data)

    async def is_enabled(self, fetch: bool = False) -> bool:
        """Retrieves whether the high-level command is currently enabled on
        the Crazyflie.

        Parameters:
            fetch: whether to forcefully fetch the current value of this
                parameter from the drone even if we have a locally cached copy
        """
        value = await self._crazyflie.parameters.get(
            "commander.enHighLevel", fetch=fetch
        )
        return bool(value)

    async def land(
        self,
        height: float,
        *,
        duration: Optional[float] = None,
        velocity: Optional[float] = None,
        relative: Optional[bool] = False,
        yaw: Optional[float] = None,
        group_mask: int = ALL_GROUPS,
    ) -> None:
        """Sends a landing command to the Crazyflie.

        The landing velocity may be specified directly (with the `velocity`
        parameter) or indirectly (with the `duration` parameter). When neither
        the velocity nor the duration are prescribed, a default safe landing
        velocity will be applied; this is decided by the firmware.

        Parameters:
            height: the absolute or relative height to land to, in meters
            duration: duration of the landing, in seconds; mutually exclusive
                with `velocity`
            velocity: average velocity of the landing, in m/s; mutually
                exclusive with `duration`
            relative: whether the landing height is relative to the current
                height; positive relative height is below the current height
            yaw: the target yaw, in degrees; `None` to use the current yaw
            group_mask: mask that defines which Crazyflie drones this command
                should apply to
        """
        if duration is not None and velocity is not None:
            raise ValueError("duration and velocity are mutually exclusive")

        if duration is not None:
            if duration < 0:
                raise ValueError("duration may not be negative")
            elif duration == 0:
                duration = None

        use_current_yaw = yaw is None
        yaw = yaw or 0.0

        if relative and duration is not None:
            # Firmware supports relative height only when the velocity is
            # specified, so we need to turn the duration into velocity
            velocity, duration = abs(height) / duration, None

        if velocity is None:
            velocity = 0  # firmware picks a velocity
        elif velocity < 0:
            raise ValueError("velocity may not be negative")

        # Convert yaw into radians
        yaw = radians(yaw)

        # Decide which command to use
        if duration is not None:
            data = self._land_struct.pack(height, yaw, use_current_yaw, duration)
            await self._run_command(
                command=(HighLevelCommand.LAND_2, group_mask), data=data
            )
        else:
            data = self._land_with_velocity_struct.pack(
                height, relative, yaw, use_current_yaw, velocity
            )
            await self._run_command(
                command=(HighLevelCommand.LAND_WITH_VELOCITY, group_mask), data=data
            )

    async def set_group_mask(self, group_mask: int = ALL_GROUPS) -> None:
        """Sets the group mask of the Crazyflie, defining which groups the
        drone will belong to.

        Parameters:
            group_mask: mask that defines which groups this Crazyflie drone
                belongs to
        """
        await self._run_command(command=(HighLevelCommand.SET_GROUP_MASK, group_mask))

    async def start_trajectory(
        self,
        id: int,
        *,
        time_scale: float = 1,
        relative: bool = False,
        reversed: bool = False,
        group_mask: int = ALL_GROUPS,
    ) -> None:
        """Starts a trajectory that was already uploaded to the trajectory memory
        of the Crazyflie.

        Parameters:
            id: ID of the trajectory to start
            time_scale: specifies the time scale of the playback; 1.0 means
                "native speed" (as prescribed by the trajectory), >1.0 slows
                playback down, <1.0 speeds playback up
            relative: whether to treat the trajectory coordinates relative to
                the current position of the Crazyflie
            reversed: whether to play the trajectory backwards. Not supported
                for compressed trajectories
        """
        data = self._start_trajectory_struct.pack(
            relative,
            reversed,
            id,
            time_scale,
        )
        await self._run_command(
            command=(HighLevelCommand.START_TRAJECTORY, group_mask), data=data
        )

    async def stop(self, group_mask: int = ALL_GROUPS) -> None:
        """Sends a command to the Crazyflie to turn off the motors immediately.

        Parameters:
            group_mask: mask that defines which Crazyflie drones this command
                should apply to
        """
        await self._run_command(command=(HighLevelCommand.STOP, group_mask))

    async def takeoff(
        self,
        height: float,
        *,
        duration: Optional[float] = None,
        velocity: Optional[float] = None,
        relative: Optional[bool] = False,
        yaw: Optional[float] = None,
        group_mask: int = ALL_GROUPS,
    ) -> None:
        """Sends a takeoff command to the Crazyflie.

        The takeoff velocity may be specified directly (with the `velocity`
        parameter) or indirectly (with the `duration` parameter). When neither
        the velocity nor the duration are prescribed, a default safe takeoff
        velocity will be applied; this is decided by the firmware.

        Parameters:
            height: the absolute or relative height to take off to, in meters
            duration: duration of the takeoff, in seconds; mutually exclusive
                with `velocity`
            velocity: average velocity of the takeoff, in m/s; mutually
                exclusive with `duration`
            relative: whether the takeoff height is relative to the current
                height; positive relative height is above the current height
            yaw: the target yaw, in degrees; `None` to use the current yaw
            group_mask: mask that defines which Crazyflie drones this command
                should apply to
        """
        if duration is not None and velocity is not None:
            raise ValueError("duration and velocity are mutually exclusive")
        if duration is not None:
            if duration < 0:
                raise ValueError("duration may not be negative")
            elif duration == 0:
                duration = None

        use_current_yaw = yaw is None
        yaw = yaw or 0.0

        if relative and duration is not None:
            # Firmware supports relative height only when the velocity is
            # specified, so we need to turn the duration into velocity
            velocity, duration = abs(height) / duration, None

        if velocity is None:
            velocity = 0  # firmware picks a velocity
        elif velocity < 0:
            raise ValueError("velocity may not be negative")

        # Convert yaw into radians
        yaw = radians(yaw)

        # Decide which command to use
        if duration is not None:
            data = self._takeoff_struct.pack(height, yaw, use_current_yaw, duration)
            await self._run_command(
                command=(HighLevelCommand.TAKEOFF_2, group_mask), data=data
            )
        else:
            data = self._takeoff_with_velocity_struct.pack(
                height, relative, yaw, use_current_yaw, velocity
            )
            await self._run_command(
                command=(HighLevelCommand.TAKEOFF_WITH_VELOCITY, group_mask), data=data
            )

    async def _run_command(
        self,
        *,
        command: Optional[CRTPCommandLike] = None,
        data: Optional[CRTPDataLike] = None,
        **kwds,
    ) -> None:
        """Sends a command packet to the high-level commander port and channel
        of the Crazyflie, waits for the next matching response packet and
        handles the error code in the packet.

        Keyword arguments not mentioned here are forwarded to the
        ``run_command()`` method of the Crazyflie itself.

        Parameters:
            command: the command byte(s) to insert before the data bytes in
                the data section of the packet. When this is not `None`, the
                matching response packet is expected to have the same prefix as
                the command itself.
            data: the data of the request packet. When a command is present, the
                command is inserted before the data in the body of the CRTP
                packet.

        Raises:
            HighLevelCommanderError: if the high-level commander refused to
                execute the command
        """
        response = await self._crazyflie.run_command(
            port=CRTPPort.HIGH_LEVEL_COMMANDER, command=command, data=data, **kwds
        )
        if len(response) < 1:
            raise HighLevelCommanderError(message="Response too short")
        elif response[-1]:
            raise HighLevelCommanderError(code=response[-1])
