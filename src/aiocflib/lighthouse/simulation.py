"""Simulation of the sweep angles measured by a Crazyflie drone in a room with
Lighthouse base stations.

Useful for testing the geometry estimation without hardware.
"""

from __future__ import annotations

from collections.abc import Mapping

from numpy import cross, float64, radians, stack
from numpy.random import Generator, default_rng
from numpy.typing import ArrayLike, NDArray

from .arrays import normalize, to_float_array
from .model import DECK_SENSOR_POSITIONS, Pose, predict_sweep_angles
from .rotation import rotation_vector_to_matrix

__all__ = ("look_at", "simulate_sample", "tilted_pose")


DEFAULT_HORIZONTAL_FIELD_OF_VIEW = radians(150.0)
"""Horizontal field of view of a Lighthouse V2 base station, in radians."""

DEFAULT_VERTICAL_FIELD_OF_VIEW = radians(110.0)
"""Vertical field of view of a Lighthouse V2 base station, in radians."""


def look_at(position: ArrayLike, target: ArrayLike) -> Pose:
    """Returns the pose of a base station at the given position that faces the
    given target, with no roll.

    Args:
        position: the position of the base station
        target: the point that the X axis of the base station points to

    Raises:
        ValueError: if the target is directly above or below the position
    """
    position = to_float_array(position)
    forward = normalize(to_float_array(target) - position)
    try:
        left = normalize(cross((0.0, 0.0, 1.0), forward), min_length=1e-9)
    except ValueError:
        raise ValueError("Base station must not face straight up or down") from None
    up = cross(forward, left)

    # Columns are the axes of the base station in the world
    return Pose.create(stack((forward, left, up), axis=-1), position)


def tilted_pose(
    position: ArrayLike, yaw: float = 0.0, tilt: ArrayLike = (0.0, 0.0)
) -> Pose:
    """Returns the pose of a drone at the given position.

    Args:
        position: the position of the drone
        yaw: the heading of the drone, in radians
        tilt: rotation around the X and Y axes of the world, in radians, applied
            after the heading
    """
    roll, pitch = to_float_array(tilt, (2,))
    rotation = (
        rotation_vector_to_matrix((roll, 0.0, 0.0))
        @ rotation_vector_to_matrix((0.0, pitch, 0.0))
        @ rotation_vector_to_matrix((0.0, 0.0, yaw))
    )
    return Pose.create(rotation, position)


def simulate_sample(
    base_stations: Mapping[int, Pose],
    drone: Pose,
    *,
    noise: float = 0.0,
    rng: Generator | None = None,
    sensor_positions: ArrayLike = DECK_SENSOR_POSITIONS,
    horizontal_field_of_view: float = DEFAULT_HORIZONTAL_FIELD_OF_VIEW,
    vertical_field_of_view: float = DEFAULT_VERTICAL_FIELD_OF_VIEW,
) -> dict[int, NDArray[float64]]:
    """Simulates the sweep angles that a drone measures at the given pose.

    A base station is included only if all the sensors are in its field of
    view and on the side of the deck that faces the base station.

    Args:
        base_stations: the poses of the base stations, keyed by ID
        drone: the pose of the drone
        noise: standard deviation of the normally distributed noise added to
            each angle, in radians
        rng: random number generator for the noise
        sensor_positions: positions of the sensors in the reference frame of
            the drone
        horizontal_field_of_view: horizontal field of view of the base
            stations, in radians
        vertical_field_of_view: vertical field of view of the base stations, in
            radians

    Returns:
        the measured sweep angles of each sensor, keyed by base station ID
    """
    if rng is None:
        rng = default_rng()

    sensors = drone.apply(sensor_positions)
    deck_normal = drone.rotation[:, 2]
    result: dict[int, NDArray[float64]] = {}

    for bs_id, base_station in sorted(base_stations.items()):
        local = (sensors - base_station.translation) @ base_station.rotation
        if (local[:, 0] <= 0).any():
            continue

        # The sensors face up from the deck
        if ((base_station.translation - sensors) @ deck_normal <= 0).any():
            continue

        angles = predict_sweep_angles(base_station, sensors)
        if (abs(angles[:, 0]) > horizontal_field_of_view / 2).any() or (
            abs(angles[:, 1]) > vertical_field_of_view / 2
        ).any():
            continue

        if noise > 0:
            angles = angles + rng.normal(scale=noise, size=angles.shape)
        result[bs_id] = angles

    return result
