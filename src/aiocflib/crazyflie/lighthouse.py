from dataclasses import dataclass
from struct import Struct
from typing import Any, ClassVar, Dict, List, Tuple, Type, cast

__all__ = ("LighthouseBsGeometry",)


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

    sweeps: Tuple[LighthouseCalibrationSweep, LighthouseCalibrationSweep]
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
