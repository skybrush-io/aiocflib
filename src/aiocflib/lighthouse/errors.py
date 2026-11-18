"""Errors raised by the estimation of the geometry of Lighthouse base
stations.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = (
    "BaseStationPlacementError",
    "GeometryEstimationError",
    "RoomAlignmentError",
)


class GeometryEstimationError(ValueError):
    """Base class for errors that indicate that the samples of a calibration
    are not sufficient to estimate the geometry of the base stations.
    """


class BaseStationPlacementError(GeometryEstimationError):
    """Error raised when some base stations share too few samples with the
    others to be placed relative to them. More samples are needed where these
    base stations are visible together with the others.
    """

    base_stations: list[int]
    """IDs of the base stations that could not be placed."""

    def __init__(self, base_stations: Iterable[int]):
        self.base_stations = sorted(base_stations)
        super().__init__(
            "Base stations share too few samples with the others: "
            + ", ".join(map(str, self.base_stations))
        )


class RoomAlignmentError(GeometryEstimationError):
    """Error raised when the samples at the origin, on the X axis and in the XY
    plane do not determine the coordinate system of the room, for instance
    because the samples on the X axis are too close to the origin.
    """
