"""Initial guess for the poses of Lighthouse base stations and drones, to be
refined by the solver in `aiocflib.lighthouse.solver`.

The initial guess is built in the following steps:

1. The pose of the drone relative to each base station in each sample is
   estimated from the sweep angles of its four sensors, with the method in
   `aiocflib.lighthouse.ippe`. The position of the drone is accurate: its
   direction comes straight from the angles and its distance is within a few
   percent. The orientation is less accurate, and it is ambiguous: the method
   yields two possible orientations.

2. The base stations are placed one by one. The first one is the base station
   with the most measurements, and its reference frame becomes the reference
   frame of the result. Each further base station is the one that shares the
   most samples with the base stations placed so far. Its pose is the rigid
   transformation that maps the positions of the drone that it sees to the
   positions seen by the placed base stations. Only positions are used in this
   step, not the ambiguous orientations. The estimated distance is far off in
   some samples, typically when the deck is seen almost edge-on. These samples
   are ignored when fitting the transformation.

3. The position of the drone in each sample is the average of its positions
   seen by the individual base stations. The orientation is the average of
   the orientations seen by the individual base stations, choosing the
   candidate orientations that agree with each other the most.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import product

from numpy import array, float64, inf, mean, tan
from numpy.typing import ArrayLike, NDArray

from .arrays import to_float_array
from .ippe import PoseCandidate, estimate_planar_pose
from .model import DECK_SENSOR_POSITIONS, Pose
from .registration import fit_rigid_transform_robust
from .rotation import mean_rotation

__all__ = ("InitialGuess", "estimate_drone_pose", "estimate_initial_guess")


_CAMERA_FROM_BASE_STATION: NDArray[float64] = array(
    [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]]
)
"""Rotation from the reference frame of a base station to the reference frame
of a pinhole camera that looks along the X axis of the base station. A point
seen at sweep angles ``(h, v)`` appears at ``(tan(h), tan(v))`` in the image of
this camera.
"""

_MIN_SPREAD = 0.05
"""Minimum spread of the positions of the drone in the samples shared by two
groups of base stations, in metres, in two independent directions. A base
station is not placed using fewer or less spread out samples.
"""

_MIN_OUTLIER_DISTANCE = 0.05
"""Positions of the drone that agree with the fitted pose of a base station
within this distance, in metres, are never treated as outliers.
"""

_MAX_CANDIDATE_COMBINATIONS = 256
"""Maximum number of combinations of candidate orientations to try when
choosing the orientation of the drone in a sample.
"""


@dataclass(frozen=True)
class InitialGuess:
    """Initial guess for the poses of the base stations and the drones."""

    base_stations: dict[int, Pose]
    """Poses of the base stations, keyed by base station ID."""

    samples: list[Pose]
    """Poses of the drone in each sample."""

    reference_base_station: int
    """ID of the base station whose reference frame the poses are expressed in.
    """


def estimate_drone_pose(
    angles: ArrayLike, sensor_positions: ArrayLike = DECK_SENSOR_POSITIONS
) -> list[PoseCandidate]:
    """Estimates the pose of a drone in the reference frame of a base station
    from the sweep angles measured by its sensors.

    Args:
        angles: array of shape ``(n, 2)``, the horizontal and the vertical sweep
            angle of each sensor, in radians
        sensor_positions: array of shape ``(n, 3)``, the positions of the
            sensors in the reference frame of the drone. They must lie in the
            XY plane of the drone.

    Returns:
        the two possible poses, the more likely one first

    Raises:
        ValueError: if the sensors do not lie in the XY plane of the drone, or
            the angles do not determine a pose
    """
    sensors = to_float_array(sensor_positions)
    if sensors.ndim != 2 or sensors.shape[1] != 3:
        raise ValueError("Sensor positions must be an array of shape (n, 3)")
    if (abs(sensors[:, 2]) > 1e-9).any():
        raise ValueError("Sensors must lie in the XY plane of the drone")

    image_points = tan(to_float_array(angles))
    to_base_station = _CAMERA_FROM_BASE_STATION.T
    return [
        PoseCandidate(
            Pose(
                rotation=to_base_station @ candidate.pose.rotation,
                translation=to_base_station @ candidate.pose.translation,
            ),
            candidate.error,
        )
        for candidate in estimate_planar_pose(sensors[:, :2], image_points)
    ]


def estimate_initial_guess(
    samples: Sequence[Mapping[int, ArrayLike]],
    *,
    sensor_positions: ArrayLike = DECK_SENSOR_POSITIONS,
) -> InitialGuess:
    """Estimates the poses of the base stations and the drone in each sample
    from the measured sweep angles.

    Args:
        samples: the sweep angles measured in each sample, keyed by base
            station ID. Each value is an array of shape ``(number of sensors,
            2)``.
        sensor_positions: positions of the sensors in the reference frame of
            the drone, in metres

    Returns:
        the estimated poses, in the reference frame of the base station with
        the most measurements. This is the base station that the solver keeps
        fixed.

    Raises:
        ValueError: if some of the base stations cannot be placed because they
            share too few samples with the others
    """
    if not samples:
        raise ValueError("At least one sample is needed")

    # Pose candidates of the drone in the frame of each base station, per sample
    candidates: list[dict[int, list[PoseCandidate]]] = []
    for index, sample in enumerate(samples):
        if not sample:
            raise ValueError(f"Sample {index} has no measurements")
        candidates.append(
            {
                bs_id: estimate_drone_pose(angles, sensor_positions)
                for bs_id, angles in sample.items()
            }
        )

    base_stations = _place_base_stations(candidates)
    reference = next(iter(base_stations))
    sample_poses = [
        _estimate_sample_pose(sample, base_stations) for sample in candidates
    ]
    return InitialGuess(
        base_stations=dict(sorted(base_stations.items())),
        samples=sample_poses,
        reference_base_station=reference,
    )


def _place_base_stations(
    candidates: Sequence[Mapping[int, Sequence[PoseCandidate]]],
) -> dict[int, Pose]:
    """Places the base stations one by one.

    Returns:
        the poses of the base stations, in the order they were placed. The
        first one is the reference.
    """
    # Position of the drone in the frame of each base station, per sample
    positions = [
        {bs_id: options[0].pose.translation for bs_id, options in sample.items()}
        for sample in candidates
    ]
    counts: dict[int, int] = {}
    for sample in positions:
        for bs_id in sample:
            counts[bs_id] = counts.get(bs_id, 0) + 1

    # Most measurements first; the lowest ID wins ties, like in the solver
    reference = min(counts, key=lambda bs_id: (-counts[bs_id], bs_id))
    placed: dict[int, Pose] = {reference: Pose.create()}
    remaining = set(counts) - {reference}

    while remaining:
        # (base station ID, number of shared samples, pose)
        best: tuple[int, int, Pose] | None = None
        for bs_id in sorted(remaining):
            placement = _try_place(bs_id, positions, placed)
            if placement is not None and (best is None or placement[0] > best[1]):
                best = (bs_id, *placement)

        if best is None:
            raise ValueError(
                "Base stations share too few samples with the others: "
                f"{', '.join(map(str, sorted(remaining)))}"
            )

        bs_id, _, pose = best
        placed[bs_id] = pose
        remaining.remove(bs_id)

    return placed


def _try_place(
    bs_id: int,
    positions: Sequence[Mapping[int, NDArray[float64]]],
    placed: Mapping[int, Pose],
) -> tuple[int, Pose] | None:
    """Tries to find the pose of a base station from the samples that it
    shares with the base stations placed so far.

    Returns:
        the number of shared samples and the pose, or ``None`` if the shared
        samples do not determine the pose
    """
    source = []
    target = []
    for sample in positions:
        if bs_id not in sample:
            continue
        seen_by_placed = [
            placed[other].apply(position)
            for other, position in sample.items()
            if other in placed
        ]
        if seen_by_placed:
            source.append(sample[bs_id])
            target.append(mean(seen_by_placed, axis=0))

    try:
        pose = fit_rigid_transform_robust(
            source, target, min_spread=_MIN_SPREAD, min_distance=_MIN_OUTLIER_DISTANCE
        )
    except ValueError:
        return None

    # The fitted transformation maps positions in the frame of the base
    # station to the reference frame, which is the pose of the base station
    return len(source), pose


def _estimate_sample_pose(
    candidates: Mapping[int, Sequence[PoseCandidate]],
    base_stations: Mapping[int, Pose],
) -> Pose:
    """Estimates the pose of the drone in a sample from the pose candidates
    seen by the individual base stations.
    """
    options: list[list[Pose]] = [
        [base_stations[bs_id].compose(option.pose) for option in bs_options]
        for bs_id, bs_options in sorted(candidates.items())
    ]

    # Try the combinations of candidate orientations and keep the one whose
    # orientations agree the most. The first candidate of each base station is
    # the more likely one; it is used for the base stations beyond the limit.
    num_free = 0
    while num_free < len(options) and 2 ** (num_free + 1) <= (
        _MAX_CANDIDATE_COMBINATIONS
    ):
        num_free += 1

    best_choice: list[Pose] = [bs_options[0] for bs_options in options]
    best_spread = inf
    for choice in product(*options[:num_free]):
        chosen = list(choice) + [bs_options[0] for bs_options in options[num_free:]]
        rotations = to_float_array([pose.rotation for pose in chosen])
        average = mean_rotation(rotations)
        spread = float(((rotations - average) ** 2).sum())
        if spread < best_spread:
            best_choice, best_spread = chosen, spread

    return Pose(
        rotation=mean_rotation([pose.rotation for pose in best_choice]),
        translation=mean([pose.translation for pose in best_choice], axis=0),
    )
