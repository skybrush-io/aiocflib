"""High-level estimation of the geometry of Lighthouse base stations from the
samples of a calibration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aiocflib.crazyflie.lighthouse import LighthouseBsGeometry

from .alignment import find_room_transform
from .errors import RoomAlignmentError
from .initial_guess import estimate_initial_guess
from .solver import Sample, refine_geometry

__all__ = ("CalibrationSamples", "GeometryEstimate", "estimate_geometry")


@dataclass
class CalibrationSamples:
    """Samples collected during the calibration of a Lighthouse system.

    The samples are stored in a single list in a fixed order:

    1. the sample taken at the origin of the room,
    2. the samples taken on the positive X axis,
    3. the samples taken elsewhere in the XY plane,
    4. the samples taken anywhere else, typically while the drone is moving.

    The sizes of the second and the third group are stored separately. All the
    remaining samples belong to the last group.
    """

    samples: list[Sample] = field(default_factory=list)
    """All the samples, in the order described above."""

    num_x_axis_samples: int = 0
    """Number of samples taken on the positive X axis."""

    num_xy_plane_samples: int = 0
    """Number of samples taken in the XY plane, but not on the X axis."""

    @property
    def num_static_samples(self) -> int:
        """Number of samples that define the coordinate system of the room,
        including the one at the origin.
        """
        return 1 + self.num_x_axis_samples + self.num_xy_plane_samples

    def validate(self) -> None:
        """Checks that the samples are consistent with the sample counts.

        Raises:
            ValueError: if there is no sample at the origin or on the X axis,
                the counts are negative, or there are fewer samples than the
                counts require
        """
        if self.num_x_axis_samples < 1:
            raise ValueError("At least one sample is needed on the X axis")
        if self.num_xy_plane_samples < 0:
            raise ValueError("The number of samples in the XY plane is negative")
        if len(self.samples) < self.num_static_samples:
            raise ValueError(
                f"Expected at least {self.num_static_samples} samples, "
                f"got {len(self.samples)}"
            )


@dataclass(frozen=True)
class GeometryEstimate:
    """Estimated geometry of the base stations of a Lighthouse system."""

    geometries: dict[int, LighthouseBsGeometry]
    """Estimated geometry of each base station in the coordinate system of the
    room, keyed by base station ID.
    """

    error: float
    """Root mean square difference between the measured sweep angles and the
    angles predicted from the estimated geometry, in radians.
    """

    converged: bool
    """Whether the solver stopped because the estimate stopped improving. The
    estimate may still be usable if this is ``False``; check the error.
    """


def estimate_geometry(samples: CalibrationSamples) -> GeometryEstimate:
    """Estimates the geometry of the base stations from the samples of a
    calibration.

    The estimation is CPU-bound and may take a few seconds with thousands of
    samples. Asynchronous callers should run it in a worker thread.

    Args:
        samples: the samples of the calibration

    Returns:
        the estimated geometry in the coordinate system of the room

    Raises:
        BaseStationPlacementError: if some base stations cannot be placed
            because they share too few samples with the others
        RoomAlignmentError: if the samples at the origin, on the X axis and in
            the XY plane do not determine the coordinate system of the room
        ValueError: if the samples are inconsistent with the sample counts or
            some samples contain no measurements
    """
    samples.validate()

    guess = estimate_initial_guess(samples.samples)
    solution = refine_geometry(samples.samples, guess.base_stations, guess.samples)

    poses = solution.samples
    num_x_axis = samples.num_x_axis_samples
    num_static = samples.num_static_samples
    try:
        to_room = find_room_transform(
            poses[0],
            [pose.translation for pose in poses[1 : 1 + num_x_axis]],
            [pose.translation for pose in poses[1 + num_x_axis : num_static]],
        )
    except ValueError as ex:
        raise RoomAlignmentError(str(ex)) from None

    return GeometryEstimate(
        geometries={
            bs_id: to_room.compose(pose).to_bs_geometry()
            for bs_id, pose in solution.base_stations.items()
        },
        error=solution.error,
        converged=solution.converged,
    )
