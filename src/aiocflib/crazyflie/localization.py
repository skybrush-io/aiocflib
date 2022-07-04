"""Classes related to accessing the localization subsystem of a Crazyflie."""

from enum import IntEnum
from struct import Struct
from typing import ClassVar, Iterable, Optional, Sequence, Tuple, Union

from aiocflib.crtp import CRTPPort
from aiocflib.utils.quaternion import compress_unit_quaternion, QuaternionXYZW

from .crazyflie import Crazyflie

__all__ = ("Localization",)


#: Maximum number of supported Lighthouse base stations
NUM_LIGHTHOUSE_BASE_STATIONS = 16


class LocalizationChannel(IntEnum):
    """Enum representing the names of the channels in the localization service
    of the CRTP protocol.
    """

    EXTERNAL_POSITION = 0
    GENERIC = 1
    POSITION_PACKED = 2


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
    LH_ANGLE_STREAM = 10
    LH_PERSIST_DATA = 11


class Localization:
    """Class representing the handler of messages related to the localization
    subsystem of a Crazyflie instance.
    """

    _crazyflie: Crazyflie

    _external_position_struct: ClassVar[Struct] = Struct("<fff")
    _external_position_packed_item_struct: ClassVar[Struct] = Struct("<Bhhh")
    _external_pose_struct: ClassVar[Struct] = Struct("<Bfffffff")
    _external_pose_packed_item_struct: ClassVar[Struct] = Struct("<BhhhL")
    _lpp_short_packet_struct: ClassVar[Struct] = Struct("<BB")
    _lighthouse_angle_struct: ClassVar[Struct] = Struct("<Bfhhhfhhh")
    _lighthouse_persist_struct: ClassVar[Struct] = Struct("<HH")

    @classmethod
    def encode_external_position_packed(
        cls, items: Sequence[Tuple[int, Tuple[float, float, float]]]
    ) -> bytes:
        """Encodes the payload of a "packed external position" packet that
        contains position information for multiple Crazyflie drones.

        The packet is intended to be broadcast to all Crazyflies in a network
        using the ``send_packet()`` method of a `Broadcaster` object.

        Parameters:
            items: a sequence of pairs containing a numeric Crazyflie ID
                (the last byte of its radio address) and a 3D coordinate.
                Coordinates must be less than ~32.7 meters in absolute value.
                At most four items fit into a single Crazyflie CRTP packet.
        """
        return b"".join(
            cls._external_position_packed_item_struct.pack(
                id, int(x * 1000), int(y * 1000), int(z * 1000)
            )
            for id, (x, y, z) in items
        )

    @classmethod
    def encode_external_pose_packed(
        cls, items: Sequence[Tuple[int, Tuple[float, float, float], QuaternionXYZW]]
    ) -> bytes:
        """Encodes the payload of a "packed external pose" packet that
        contains position and attitude information for multiple Crazyflie drones.

        The packet is intended to be broadcast to all Crazyflies in a network
        using the ``send_packet()`` method of a `Broadcaster` object.

        Parameters:
            items: a sequence of triplets containing a numeric Crazyflie ID
                (the last byte of its radio address), a 3D coordinate and a
                4D quaternion in XYZW order. Coordinates must be less than ~32.7
                meters in absolute value. At most two items fit into a single
                Crazyflie CRTP packet.
        """
        return b"".join(
            cls._external_pose_packed_item_struct.pack(
                id,
                int(x * 1000),
                int(y * 1000),
                int(z * 1000),
                compress_unit_quaternion(quat),
            )
            for id, (x, y, z), quat in items
        )

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle the localization
                subsystem related messages
        """
        self._crazyflie = crazyflie

    async def _send_packet(
        self,
        data: Union[int, bytes],
        channel: LocalizationChannel = LocalizationChannel.GENERIC,
    ) -> None:
        await self._crazyflie.send_packet(
            port=CRTPPort.LOCALIZATION, channel=channel, data=data
        )

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
            if isinstance(x, Iterable):
                data = self._external_position_struct.pack(*x)
            else:
                raise TypeError(
                    "x must be a sequence of floats when y and z are not given"
                )
        else:
            data = self._external_position_struct.pack(x, y, z)

        await self._send_packet(
            data,
            channel=LocalizationChannel.EXTERNAL_POSITION,
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
        await self._send_packet(
            self._external_pose_struct.pack(
                GenericLocalizationCommand.EXT_POSE, x, y, z, qx, qy, qz, qw
            ),
        )

    async def send_lpp_short_packet(self, dest_id: int, data: bytes) -> bool:
        """Sends an LPP short packet to the Loco Positioning System, using the
        Crazyflie as a proxy.

        Parameters:
            dest_id: ID of the Loco Positioning System node to send the packet to
            data: the raw LPP short packet to send

        Returns:
            whether the LPP short packet response indicated a success or a failure
        """
        response = await self._crazyflie.run_command(
            port=CRTPPort.LOCALIZATION,
            channel=LocalizationChannel.GENERIC,
            command=GenericLocalizationCommand.LPP_SHORT_PACKET,
            data=self._lpp_short_packet_struct.pack(dest_id) + data,
        )
        return len(response) > 0 and bool(response[0])

    async def enable_emergency_stop(self) -> None:
        """Sends an "enable emergency stop" packet to the Crazyflie."""
        await self._send_packet(
            GenericLocalizationCommand.ENABLE_EMERGENCY_STOP,
        )

    async def reset_emergency_stop_timeout(self) -> None:
        """Sends a "reset emergency stop timeout" packet to the Crazyflie to
        prevent it from stopping when the emergency stop watchdog is enabled.
        """
        await self._send_packet(
            GenericLocalizationCommand.RESET_EMERGENCY_STOP_TIMEOUT,
        )

    async def persist_lighthouse_data(
        self,
        geo_list: Optional[Iterable[int]] = None,
        calib_list: Optional[Iterable[int]] = None,
    ) -> bool:
        """Instructs the Crazyflie to persist the currently estimated geometry
        and calibration data of the Lighthouse subsystem into permanent storage.

        Parameters:
            geo_list: IDs of the Lighthouse base stations (0-based) whose
                geometry data must be persisted. Defaults to all stations when
                omitted.
            calib_list: IDs of the Lighthouse base stations (0-based) whose
                calibration data must be persisted. Defaults to the same value
                as the geometry list when omitted.

        Returns:
            whether the data was persisted successfully
        """
        if geo_list is None:
            geo_list = range(NUM_LIGHTHOUSE_BASE_STATIONS)

        if calib_list is None:
            calib_list = geo_list

        if not _is_valid_lighthouse_base_station_id_list(geo_list):
            raise ValueError("Geometry base station ID list is invalid")
        if not _is_valid_lighthouse_base_station_id_list(calib_list):
            raise ValueError("Calibration base station ID list is invalid")

        geo_mask, calib_mask = 0, 0
        for id in geo_list:
            geo_mask |= 1 << id
        for id in calib_list:
            calib_mask |= 1 << id

        response = await self._crazyflie.run_command(
            port=CRTPPort.LOCALIZATION,
            channel=LocalizationChannel.GENERIC,
            command=GenericLocalizationCommand.LH_PERSIST_DATA,
            data=self._lighthouse_persist_struct.pack(geo_mask, calib_mask),
        )

        return len(response) > 0 and bool(response[0])


def _is_valid_lighthouse_base_station_id_list(ids: Iterable[int]) -> bool:
    return all(
        isinstance(id, int) and id >= 0 and id < NUM_LIGHTHOUSE_BASE_STATIONS
        for id in ids
    )
