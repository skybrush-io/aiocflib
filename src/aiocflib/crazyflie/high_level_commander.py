"""Classes related to sending high-level navigation commands to a Crazyflie.
"""

from contextlib import asynccontextmanager
from enum import IntEnum
from math import radians
from struct import Struct
from typing import Optional

from aiocflib.crtp import CRTPPort

from .crazyflie import Crazyflie

__all__ = ("HighLevelCommander",)


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

    _define_trajectory_struct = Struct("<BBBBIB")
    _go_to_struct = Struct("<BBBfffff")
    _land_struct = Struct("<BBff?f")
    _start_trajectory_struct = Struct("<BBBBBf")
    _takeoff_struct = Struct("<BBff?f")

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
        data = self._define_trajectory_struct.pack(
            HighLevelCommand.DEFINE_TRAJECTORY, id, location, type, addr, num_pieces,
        )
        await self._crazyflie.send_packet(port=CRTPPort.HIGH_LEVEL_COMMANDER, data=data)

    async def disable(self):
        """Disables the high-level controller on the Crazyflie."""
        await self._crazyflie.parameters.set("commander.enHighLevel", 0)

    async def enable(self):
        """Enables the high-level controller on the Crazyflie."""
        await self._crazyflie.parameters.set("commander.enHighLevel", 1)

    @asynccontextmanager
    async def enabled(self):
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
            HighLevelCommand.GO_TO,
            group_mask,
            relative,
            x,
            y,
            z,
            radians(yaw),
            duration,
        )
        await self._crazyflie.send_packet(port=CRTPPort.HIGH_LEVEL_COMMANDER, data=data)

    async def is_enabled(self, fetch: bool = False) -> bool:
        """Retrieves whether the high-level command is currently enabled on
        the Crazyflie.

        Parameters:
            fetch: whether to forcefully fetch the current value of this
                parameter from the drone even if we have a locally cached copy
        """
        return await self._crazyflie.parameters.get(
            "commander.enHighLevel", fetch=fetch
        )

    async def land(
        self,
        height: float,
        *,
        duration: float,
        yaw: Optional[float] = None,
        group_mask: int = ALL_GROUPS,
    ) -> None:
        """Sends a takeoff command to the Crazyflie.

        Parameters:
            height: the target height to land to, in meters
            duration: duration of the landing, in seconds
            yaw: the target yaw, in degrees; `None` to use the current yaw
            group_mask: mask that defines which Crazyflie drones this command
                should apply to
        """
        use_current_yaw = yaw is None
        yaw = yaw or 0.0
        data = self._land_struct.pack(
            HighLevelCommand.LAND_2,
            group_mask,
            height,
            radians(yaw),
            use_current_yaw,
            duration,
        )
        await self._crazyflie.send_packet(port=CRTPPort.HIGH_LEVEL_COMMANDER, data=data)

    async def set_group_mask(self, group_mask: int = ALL_GROUPS) -> None:
        """Sets the group mask of the Crazyflie, defining which groups the
        drone will belong to.

        Parameters:
            group_mask: mask that defines which groups this Crazyflie drone
                belongs to
        """
        await self._crazyflie.send_packet(
            port=CRTPPort.HIGH_LEVEL_COMMANDER,
            data=(HighLevelCommand.SET_GROUP_MASK, group_mask),
        )

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
            HighLevelCommand.START_TRAJECTORY,
            group_mask,
            relative,
            reversed,
            id,
            time_scale,
        )
        await self._crazyflie.send_packet(port=CRTPPort.HIGH_LEVEL_COMMANDER, data=data)

    async def stop(self, group_mask: int = ALL_GROUPS) -> None:
        """Sends a command to the Crazyflie to turn off the motors immediately.

        Parameters:
            group_mask: mask that defines which Crazyflie drones this command
                should apply to
        """
        await self._crazyflie.send_packet(
            port=CRTPPort.HIGH_LEVEL_COMMANDER, data=(HighLevelCommand.STOP, group_mask)
        )

    async def takeoff(
        self,
        height: float,
        *,
        duration: float,
        yaw: Optional[float] = None,
        group_mask: int = ALL_GROUPS,
    ) -> None:
        """Sends a takeoff command to the Crazyflie.

        Parameters:
            height: the absolute height to take off to, in meters
            duration: duration of the takeoff, in seconds
            yaw: the target yaw, in degrees; `None` to use the current yaw
            group_mask: mask that defines which Crazyflie drones this command
                should apply to
        """
        use_current_yaw = yaw is None
        yaw = yaw or 0.0
        data = self._takeoff_struct.pack(
            HighLevelCommand.TAKEOFF_2,
            group_mask,
            height,
            radians(yaw),
            use_current_yaw,
            duration,
        )
        await self._crazyflie.send_packet(port=CRTPPort.HIGH_LEVEL_COMMANDER, data=data)

