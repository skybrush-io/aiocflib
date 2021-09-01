from dataclasses import dataclass, field
from enum import IntEnum
from errno import EIO
from struct import Struct
from typing import (
    Any,
    ClassVar,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Tuple,
    Type,
    cast,
)

from aiocflib.crtp.crtpstack import MemoryType

from .crazyflie import Crazyflie
from .mem import MemoryHandler

__all__ = ("LighthouseBsCalibration", "LighthouseBsGeometry", "LighthouseConfiguration")


Vector3D = Tuple[float, float, float]
Matrix3D = Tuple[Vector3D, Vector3D, Vector3D]


@dataclass(frozen=True)
class LighthouseBsGeometry:
    """Container for geometry data of one Lighthouse base station"""

    origin: Vector3D = (0, 0, 0)
    rotation_matrix: Matrix3D = ((0, 0, 0), (0, 0, 0), (0, 0, 0))
    valid: bool = False

    _struct: ClassVar[Struct] = Struct("<ffffffffffff?")
    size_in_bytes: ClassVar[int] = _struct.size

    @classmethod
    def from_bytes(cls, data: bytes):
        """Constructs a Lighthouse base station geometry object from its raw
        byte-level representation from the Lighthouse memory of a Crazyflie.

        Parameters:
            data: the data to unpack from

        Returns:
            the unpacked object
        """
        if len(data) != cls.size_in_bytes:
            raise ValueError(
                f"invalid length, expected {cls.size_in_bytes} bytes, got {len(data)}"
            )

        result, _ = cls.unpack_from_bytes(data)
        return result

    @classmethod
    def from_json(cls, obj: Dict[str, Any]):
        """Constructs a Lighthouse base station geometry object from its JSON
        object representation created earlier with `to_json()`.
        """
        assert len(obj["origin"]) == 3
        assert len(obj["rotation"]) == 3 and all(
            len(row) == 3 for row in obj["rotation"]
        )
        return cls(
            origin=tuple(obj["origin"]),  # type: ignore
            rotation_matrix=tuple(tuple(row) for row in obj["rotation"]),  # type: ignore
            valid=True,
        )

    @classmethod
    def unpack_from_bytes(
        cls: Type["LighthouseBsGeometry"], data: bytes, offset: int = 0
    ) -> Tuple["LighthouseBsGeometry", int]:
        """Constructs a Lighthouse sweep calibration object from its raw
        byte-level representation from the Lighthouse memory of a Crazyflie.

        Parameters:
            data: the data to unpack from
            offset: optional offset into the data object

        Returns:
            the unpacked object and the index of the first _unconsumed_ byte
            from the incoming data
        """
        if len(data) - offset < cls.size_in_bytes:
            raise ValueError(
                f"invalid length, expected {cls.size_in_bytes} bytes, got {len(data) - offset}"
            )

        items = cls._struct.unpack_from(data, offset)
        origin = cast(Vector3D, items[:3])
        rotation_matrix = cast(Matrix3D, (items[3:6], items[6:9], items[9:12]))
        valid = items[12]

        return (
            cls(origin=origin, rotation_matrix=rotation_matrix, valid=valid),
            offset + cls.size_in_bytes,
        )

    def to_bytes(self) -> bytes:
        """Converts the Lighthouse base station geometry object into a raw
        byte-level representation used in the Lighthouse memory.
        """
        items: List[Any] = []
        items.extend(self.origin)
        items.extend(sum(self.rotation_matrix, ()))
        items.append(self.valid)
        return self._struct.pack(*items)

    def to_json(self) -> Dict[str, Any]:
        """Converts the Lighthouse base station data into a Python object that
        can be written directly into a JSON or YAML file.
        """
        return {"origin": self.origin, "rotation": self.rotation_matrix}


@dataclass(frozen=True)
class LighthouseCalibrationSweep:
    """Container for calibration data of a single sweep plane of a Lighthouse
    base station.
    """

    phase: float = 0.0
    tilt: float = 0.0
    curve: float = 0.0
    gibmag: float = 0.0
    gibphase: float = 0.0
    ogeemag: float = 0.0
    ogeephase: float = 0.0

    _struct: ClassVar[Struct] = Struct("<fffffff")
    size_in_bytes: ClassVar[int] = _struct.size

    @classmethod
    def from_bytes(cls, data: bytes):
        """Constructs a Lighthouse sweep calibration object from its raw
        byte-level representation from the Lighthouse memory of a Crazyflie.

        Parameters:
            data: the data to unpack from

        Returns:
            the unpacked object
        """
        if len(data) != cls.size_in_bytes:
            raise ValueError(
                f"invalid length, expected {cls.size_in_bytes} bytes, got {len(data)}"
            )

        obj, _ = cls.unpack_from_bytes(data)
        return obj

    @classmethod
    def from_json(cls, obj: Dict[str, Any]):
        """Constructs a Lighthouse sweep calibration object from its JSON
        object representation created earlier with `to_json()`.
        """
        return cls(
            phase=obj["phase"],
            tilt=obj["tilt"],
            curve=obj["curve"],
            gibmag=obj["gibmag"],
            gibphase=obj["gibphase"],
            ogeemag=obj["ogeemag"],
            ogeephase=obj["ogeephase"],
        )

    @classmethod
    def unpack_from_bytes(cls, data: bytes, offset: int = 0):
        """Constructs a Lighthouse sweep calibration object from its raw
        byte-level representation from the Lighthouse memory of a Crazyflie.

        Parameters:
            data: the data to unpack from
            offset: optional offset into the data object

        Returns:
            the unpacked object and the index of the first _unconsumed_ byte
            from the incoming data
        """
        if len(data) - offset < cls.size_in_bytes:
            raise ValueError(
                f"invalid length, expected {cls.size_in_bytes} bytes, got {len(data) - offset}"
            )

        items = cls._struct.unpack_from(data, offset)
        return (
            cls(
                phase=items[0],
                tilt=items[1],
                curve=items[2],
                gibmag=items[3],
                gibphase=items[4],
                ogeemag=items[5],
                ogeephase=items[6],
            ),
            offset + cls.size_in_bytes,
        )

    def to_bytes(self) -> bytes:
        """Converts the Lighthouse sweep calibration object into a raw byte-level
        representation used in the Lighthouse memory.
        """
        return self._struct.pack(
            self.phase,
            self.tilt,
            self.curve,
            self.gibmag,
            self.gibphase,
            self.ogeemag,
            self.ogeephase,
        )

    def to_json(self) -> Dict[str, Any]:
        """Converts the Lighthouse sweep calibration data into a Python object that
        can be written directly into a JSON or YAML file.
        """
        return {
            "phase": self.phase,
            "tilt": self.tilt,
            "curve": self.curve,
            "gibmag": self.gibmag,
            "gibphase": self.gibphase,
            "ogeemag": self.ogeemag,
            "ogeephase": self.ogeephase,
        }


@dataclass(frozen=True)
class LighthouseBsCalibration:
    """Container for calibration data of one Lighthouse base station."""

    sweeps: Tuple[LighthouseCalibrationSweep, LighthouseCalibrationSweep] = field(
        default=(LighthouseCalibrationSweep(), LighthouseCalibrationSweep())
    )
    uid: int = 0
    valid: bool = False

    _struct: ClassVar[Struct] = Struct("<L?")
    size_in_bytes: ClassVar[int] = (
        _struct.size + 2 * LighthouseCalibrationSweep.size_in_bytes
    )

    @classmethod
    def from_bytes(cls, data: bytes):
        """Constructs a Lighthouse base station calibration object from its raw
        byte-level representation from the Lighthouse memory of a Crazyflie.

        Parameters:
            data: the data to unpack from

        Returns:
            the unpacked object
        """
        if len(data) != cls.size_in_bytes:
            raise ValueError(
                f"invalid length, expected {cls.size_in_bytes} bytes, got {len(data)}"
            )

        obj, _ = cls.unpack_from_bytes(data)
        return obj

    @classmethod
    def from_json(cls, obj: Dict[str, Any]):
        """Constructs a Lighthouse base station calibration object from its JSON
        object representation created earlier with `to_json()`.
        """
        assert len(obj["sweeps"]) == 2
        return cls(
            uid=int(obj["uid"]),
            sweeps=tuple(  # type: ignore
                LighthouseCalibrationSweep.from_json(item) for item in obj["sweeps"]
            ),
            valid=True,
        )

    @classmethod
    def unpack_from_bytes(cls, data: bytes, offset: int = 0):
        """Constructs a Lighthouse base station calibration object from its raw
        byte-level representation from the Lighthouse memory of a Crazyflie.

        Parameters:
            data: the data to unpack from
            offset: optional offset into the data object

        Returns:
            the unpacked object and the index of the first _unconsumed_ byte
            from the incoming data
        """
        if len(data) - offset < cls.size_in_bytes:
            raise ValueError(
                f"invalid length, expected {cls.size_in_bytes} bytes, got {len(data) - offset}"
            )

        sweep1, offset = LighthouseCalibrationSweep.unpack_from_bytes(data, offset)
        sweep2, offset = LighthouseCalibrationSweep.unpack_from_bytes(data, offset)

        items = cls._struct.unpack_from(data, offset)
        return (
            cls(sweeps=(sweep1, sweep2), uid=items[0], valid=items[1]),
            offset + cls._struct.size,
        )

    def to_bytes(self) -> bytes:
        """Converts the Lighthouse base station calibration object into a raw
        byte-level representation used in the Lighthouse memory.
        """
        return b"".join(
            [
                self.sweeps[0].to_bytes(),
                self.sweeps[1].to_bytes(),
                self._struct.pack(self.uid, self.valid),
            ]
        )

    def to_json(self) -> Dict[str, Any]:
        """Converts the Lighthouse base station calibration data into a Python
        object that can be written directly into a JSON or YAML file.
        """
        return {
            "sweeps": tuple(sweep.to_json() for sweep in self.sweeps),
            "uid": self.uid,
        }


class LighthouseSystemType(IntEnum):
    """Enum representing the different Lighthouse system versions that we
    support.
    """

    UNKNOWN = 0
    V1 = 1
    V2 = 2

    def describe(self) -> str:
        """Returns a human-readable description of the system type."""
        if int(self) in (1, 2):
            return f"Lighthouse v{int(self)}"
        else:
            return "unknown'"


@dataclass(frozen=True)
class LighthouseConfiguration:
    """Data class that encapsulates the geometry _and_ calibration data of
    all base stations in a Lighthouse system.
    """

    system_type: LighthouseSystemType = LighthouseSystemType.UNKNOWN
    calibrations: Dict[int, LighthouseBsCalibration] = field(default_factory=dict)
    geometries: Dict[int, LighthouseBsGeometry] = field(default_factory=dict)

    @property
    def bs_ids(self) -> FrozenSet[int]:
        return frozenset(self.calibrations.keys()) | frozenset(self.geometries.keys())

    @property
    def valid_bs_ids(self) -> FrozenSet[int]:
        return frozenset(
            k for k, v in self.calibrations.items() if v.valid
        ) & frozenset(k for k, v in self.geometries.items() if v.valid)

    def to_json(self) -> Dict[str, Any]:
        """Converts the Lighthouse configuration into a Python object that can
        be written directly into a JSON or YAML file.
        """
        result = {
            "version": "1",
            "type": "lighthouse_system_configuration",
            "calibs": {k: v.to_json() for k, v in self.calibrations.items()},
            "geos": {k: v.to_json() for k, v in self.geometries.items()},
        }
        if self.system_type != LighthouseSystemType.UNKNOWN:
            result["systemType"] = int(self.system_type)
        return result

    @property
    def valid(self) -> bool:
        """Returns whether the Lighthouse configuration is valid.

        A configuration is valid if it has a valid system type, it has at least
        one base station and all the calibrations and geometries are valid.
        """
        return (
            self.system_type != LighthouseSystemType.UNKNOWN
            and all(calib.valid for calib in self.calibrations.values())
            and all(geo.valid for geo in self.geometries.values())
            and len(set(self.calibrations.keys()) & set(self.geometries.keys())) > 0
        )


class Lighthouse:
    """Class representing the Lighthouse subsystem of a Crazyflie instance."""

    CALIB_START_ADDR: ClassVar[int] = 0x1000
    GEO_START_ADDR: ClassVar[int] = 0
    PAGE_SIZE: ClassVar[int] = 0x100

    _crazyflie: Crazyflie
    _mem: Optional[MemoryHandler]

    number_of_base_stations: int

    def __init__(self, crazyflie: Crazyflie, *, number_of_base_stations: int = 2):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie instance
        """
        self._crazyflie = crazyflie
        self._mem = None
        self.number_of_base_stations = number_of_base_stations

    async def clear_all(self) -> None:
        """Clears the calibration and geometry data of all base stations on
        the Crazyflie.
        """
        await self.clear_calibrations()
        await self.clear_geometries()

    async def clear_calibration(self, index: int) -> None:
        """Clears the calibration data of a single base station on the Crazyflie."""
        await self.set_calibration(index, LighthouseBsCalibration())

    async def clear_calibrations(self, indices: Optional[Iterable[int]] = None) -> None:
        """Clears the calibration data of multiple base stations on the Crazyflie.

        Parameters:
            indices: the indices of the base stations to clear; `None` means to
                clear all base stations.
        """
        if indices is None:
            ignore_errors = True
            indices = range(self.number_of_base_stations)
        else:
            ignore_errors = False

        for index in indices:
            try:
                await self.clear_calibration(index)
            except IOError as ex:
                if ex.errno == EIO and ignore_errors:
                    # Probably just an invalid base station ID
                    continue
                else:
                    raise

    async def clear_geometry(self, index: int) -> None:
        """Clears the geometry data of a single base station on the Crazyflie."""
        await self.set_geometry(index, LighthouseBsGeometry())

    async def clear_geometries(
        self, indices: Optional[Iterable[int]] = None, *, ignore_errors: bool = False
    ) -> None:
        """Clears the geometry data of multiple base stations on the Crazyflie.

        Parameters:
            indices: the indices of the base stations to clear; `None` means to
                clear all base stations.
        """
        if indices is None:
            ignore_errors = True
            indices = range(self.number_of_base_stations)
        else:
            ignore_errors = False

        for index in indices:
            try:
                await self.clear_geometry(index)
            except IOError as ex:
                if ex.errno == EIO and ignore_errors:
                    # Probably just an invalid base station ID
                    continue
                else:
                    raise

    async def get_calibration(self, index: int) -> Optional[LighthouseBsCalibration]:
        """Retrieves the calibration data of a single base station from the
        Crazyflie.

        Parameters:
            index: the index of the base station

        Returns: the calibration data of a single base station from the Crazyflie,
            or `None` if the calibration data is not valid for the given base
            station index.
        """
        mem = await self._get_memory()
        try:
            data = await mem.read(
                self._get_address_of_bs_calibration(index),
                LighthouseBsCalibration.size_in_bytes,
            )
        except IOError as ex:
            if ex.errno == EIO:
                # This is okay, probably the base station index is invalid
                return None
            else:
                raise

        calibration = LighthouseBsCalibration.from_bytes(data)
        return calibration if calibration.valid else None

    async def get_calibrations(self) -> Dict[int, LighthouseBsCalibration]:
        """Returns the calibration data of all the base stations from the Crazyflie."""
        result: Dict[int, LighthouseBsCalibration] = {}
        for i in range(self.number_of_base_stations):
            calibration = await self.get_calibration(i)
            if calibration:
                result[i] = calibration
        return result

    async def get_configuration(self) -> LighthouseConfiguration:
        """Returns the Lighthouse configuration of the Crazyflie."""
        calib = await self.get_calibrations()
        geos = await self.get_geometries()
        system_type = await self.get_system_type()
        return LighthouseConfiguration(
            system_type=system_type, calibrations=calib, geometries=geos
        )

    async def get_geometry(self, index: int) -> Optional[LighthouseBsGeometry]:
        """Retrieves the geometry of a single base station from the Crazyflie.

        Parameters:
            index: the index of the base station

        Returns: the geometry of the base station or `None` if the geometry is
            not valid for the given base station index.
        """
        mem = await self._get_memory()
        try:
            data = await mem.read(
                self._get_address_of_bs_geometry(index),
                LighthouseBsGeometry.size_in_bytes,
            )
        except IOError as ex:
            if ex.errno == EIO:
                # This is okay, probably the base station index is invalid
                return None
            else:
                raise

        geometry = LighthouseBsGeometry.from_bytes(data)
        return geometry if geometry.valid else None

    async def get_geometries(self) -> Dict[int, LighthouseBsGeometry]:
        """Returns the geometries of all the base stations from the Crazyflie."""
        result: Dict[int, LighthouseBsGeometry] = {}
        for i in range(self.number_of_base_stations):
            geometry = await self.get_geometry(i)
            if geometry:
                result[i] = geometry
        return result

    async def get_system_type(self) -> LighthouseSystemType:
        """Returns the configured Lighthouse system type of the Crazyflie."""
        value = await self._crazyflie.param.get("lighthouse.systemType")
        try:
            return LighthouseSystemType(int(value))
        except Exception:
            return LighthouseSystemType.UNKNOWN

    async def persist(self) -> None:
        """Copies the current calibration and geometry data on the Crazyflie to
        persistent storage so it gets loaded the next time the Crazyflie boots.
        """
        await self._crazyflie.localization.persist_lighthouse_data()

    async def set_calibration(
        self,
        index: int,
        calibration: LighthouseBsCalibration,
    ) -> None:
        """Sets the calibration data of the base station with the given index.

        Note that this function does _not_ persist the calibration data on the
        Crazyflie into permanent memory. Call `persist()` to make sure that the
        changes are permanent.

        Parameters:
            index: the index of the base station
            calibration: the calibration data to set on the Crazyflie
        """
        mem = await self._get_memory()
        await mem.write(
            self._get_address_of_bs_calibration(index), calibration.to_bytes()
        )

    async def set_calibrations(self, data: Dict[int, LighthouseBsCalibration]) -> None:
        """Sets the calibration data of multiple base stations.

        Parameters:
            data: a dictionary mapping base station IDs to calibration data
        """
        for index, calibration in data.items():
            await self.set_calibration(index, calibration)

    async def set_configuration(self, config: LighthouseConfiguration) -> None:
        """Replaces the entire Lighthouse base station configuration with the
        given object.

        Note that this function does _not_ persist the geometry data on the
        Crazyflie into permanent memory. Call `persist()` to make sure that the
        changes are permanent.
        """
        if not config.valid:
            raise RuntimeError("Configuration is invalid")

        await self.clear_all()
        await self._crazyflie.param.set(
            "lighthouse.systemType", int(config.system_type)
        )
        for bs_id in config.valid_bs_ids:
            await self.set_calibration(bs_id, config.calibrations[bs_id])
            await self.set_geometry(bs_id, config.geometries[bs_id])
        await self._crazyflie.param.set("lighthouse.bsCalibReset", 1)
        await self.persist()

    async def set_geometry(
        self,
        index: int,
        geometry: LighthouseBsGeometry,
    ) -> None:
        """Sets the geometry object of the base station with the given index.

        Note that this function does _not_ persist the geometry data on the
        Crazyflie into permanent memory. Call `persist()` to make sure that the
        changes are permanent.

        Parameters:
            index: the index of the base station
            geometry: the geometry data to set on the Crazyflie
        """
        mem = await self._get_memory()
        await mem.write(self._get_address_of_bs_geometry(index), geometry.to_bytes())

    async def set_geometries(self, data: Dict[int, LighthouseBsGeometry]) -> None:
        """Sets the geometry data of multiple base stations.

        Parameters:
            data: a dictionary mapping base station IDs to calibration data
        """
        for index, geometry in data.items():
            await self.set_geometry(index, geometry)

    def _get_address_of_bs_calibration(self, index: int) -> int:
        """Returns the address of the calibration data of the base station with
        the given index.
        """
        return self.CALIB_START_ADDR + index * self.PAGE_SIZE

    def _get_address_of_bs_geometry(self, index: int) -> int:
        """Returns the address of the geometry data of the base station with
        the given index.
        """
        return self.GEO_START_ADDR + index * self.PAGE_SIZE

    async def _get_memory(self) -> MemoryHandler:
        """Returns the memory handler object of the Lighthouse memory."""
        if self._mem is None:
            self._mem = await self._crazyflie.mem.find(MemoryType.LIGHTHOUSE)
        return self._mem


async def test():
    from pprint import pprint

    uri = "radio+log://0/80/2M/E7E7E7E701"
    async with Crazyflie(uri) as cf:
        cf.lighthouse.number_of_base_stations = 16

        calibration = await cf.lighthouse.get_calibrations()
        print("Calibration:")
        pprint(calibration)
        print("")

        geom = await cf.lighthouse.get_geometries()
        print("Geometry:")
        pprint(geom)

        await cf.lighthouse.clear_all()

        calibration = await cf.lighthouse.get_calibrations()
        print("Calibration:")
        pprint(calibration)
        print("")

        geom = await cf.lighthouse.get_geometries()
        print("Geometry:")
        pprint(geom)

        await cf.lighthouse.persist()


if __name__ == "__main__":
    from aiocflib.crtp import init_drivers
    import trio

    init_drivers()
    try:
        trio.run(test)
    except KeyboardInterrupt:
        pass
