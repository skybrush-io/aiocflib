"""Synthetic Lighthouse setups for the tests of the geometry estimation."""

from dataclasses import dataclass

from numpy import arccos, clip, ndarray, pi, trace
from numpy.random import Generator, default_rng

from aiocflib.lighthouse.arrays import normalize
from aiocflib.lighthouse.model import Pose
from aiocflib.lighthouse.rotation import rotation_vector_to_matrix
from aiocflib.lighthouse.simulation import (
    get_pose_from_position_and_angles,
    get_pose_from_position_and_target,
    simulate_sample,
)


@dataclass
class Scene:
    """Base stations and drone poses in the coordinate system of the room,
    together with the simulated measurements.

    The first sample is taken at the origin of the room, followed by the
    samples on the X axis, the samples in the XY plane and the samples taken
    elsewhere in the room.
    """

    base_stations: dict[int, Pose]
    drones: list[Pose]
    samples: list[dict[int, ndarray]]
    x_axis: slice
    xy_plane: slice


def create_scene(
    *,
    noise: float = 0.0,
    num_free_samples: int = 40,
    base_station_ids: tuple[int, ...] = (0, 1, 2, 3),
    seed: int = 1,
    tilt: float = 0.1,
) -> Scene:
    """Creates a room with base stations in its upper corners, all facing the
    middle of the room, and simulates the samples of a calibration.

    The drone stands level on the floor in the samples that define the
    coordinate system of the room. Elsewhere it is tilted by a random angle
    around the X and Y axes, with the given standard deviation in radians. The
    deck is mounted on the top of the drone.
    """
    rng = default_rng(seed)
    corners = [(-2.0, -2.5), (2.5, -2.0), (2.0, 2.5), (-2.5, 2.0)]
    base_stations = {
        bs_id: get_pose_from_position_and_target((x, y, 2.6), (0.0, 0.0, 0.3))
        for bs_id, (x, y) in zip(base_station_ids, corners)
    }

    drones = [
        get_pose_from_position_and_angles((0.0, 0.0, 0.0), yaw=0.3),
        get_pose_from_position_and_angles((0.6, 0.0, 0.0), yaw=-0.2),
        get_pose_from_position_and_angles((1.2, 0.0, 0.0), yaw=1.0),
        get_pose_from_position_and_angles((0.0, 1.0, 0.0), yaw=2.0),
        get_pose_from_position_and_angles((1.0, 1.0, 0.0), yaw=-1.5),
    ]
    x_axis = slice(1, 3)
    xy_plane = slice(3, 5)

    while len(drones) < 5 + num_free_samples:
        drones.append(
            get_pose_from_position_and_angles(
                rng.uniform((-1.5, -1.5, 0.2), (1.5, 1.5, 1.8)),
                yaw=rng.uniform(-pi, pi),
                tilt=rng.normal(scale=tilt, size=2),
            )
        )

    samples = [
        simulate_sample(base_stations, drone, noise=noise, rng=rng) for drone in drones
    ]
    return Scene(base_stations, drones, samples, x_axis, xy_plane)


def perturb(pose: Pose, rng: Generator, *, angle: float, distance: float) -> Pose:
    """Moves and rotates a pose by random amounts."""
    axis = normalize(rng.normal(size=3))
    rotation = rotation_vector_to_matrix(axis * angle)
    return Pose(
        rotation=pose.rotation @ rotation,
        translation=pose.translation + rng.normal(scale=distance, size=3),
    )


def initial_guess(
    scene: Scene, *, seed: int = 2, angle: float = 0.1, distance: float = 0.2
) -> tuple[dict[int, Pose], list[Pose]]:
    """Returns a perturbed copy of the true poses."""
    rng = default_rng(seed)
    base_stations = {
        bs_id: perturb(pose, rng, angle=angle, distance=distance)
        for bs_id, pose in scene.base_stations.items()
    }
    drones = [
        perturb(pose, rng, angle=angle, distance=distance / 2) for pose in scene.drones
    ]
    return base_stations, drones


def to_solver_frame(
    scene: Scene, initial_base_stations: dict[int, Pose], reference: int
) -> Pose:
    """Returns the transformation from the coordinate system of the room to the
    reference frame of the solver, which is anchored to the initial pose of
    the reference base station.
    """
    return initial_base_stations[reference].compose(
        scene.base_stations[reference].inverse()
    )


def rotation_angle_between(a: ndarray, b: ndarray) -> float:
    """Returns the angle of the rotation between two rotation matrices, in
    radians.
    """
    cos_angle = (trace(a.T @ b) - 1.0) / 2.0
    return float(arccos(clip(cos_angle, -1.0, 1.0)))
