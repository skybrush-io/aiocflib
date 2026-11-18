from lighthouse_scenes import create_scene, rotation_angle_between
from numpy import allclose, radians, zeros
from numpy.linalg import norm
from pytest import mark, raises

from aiocflib.lighthouse.alignment import find_room_transform
from aiocflib.lighthouse.initial_guess import (
    _place_base_stations,
    estimate_drone_pose,
    estimate_initial_guess,
)
from aiocflib.lighthouse.model import DECK_SENSOR_POSITIONS
from aiocflib.lighthouse.simulation import look_at, simulate_sample
from aiocflib.lighthouse.solver import refine_geometry


def test_estimate_drone_pose_candidates_include_true_pose():
    scene = create_scene(num_free_samples=20)
    for drone, sample in zip(scene.drones, scene.samples):
        for bs_id, angles in sample.items():
            expected = scene.base_stations[bs_id].inverse().compose(drone)
            candidates = estimate_drone_pose(angles)

            assert len(candidates) == 2
            assert (
                min(
                    norm(c.pose.rotation - expected.rotation)
                    + norm(c.pose.translation - expected.translation)
                    for c in candidates
                )
                < 1e-8
            )


def test_estimate_drone_pose_needs_planar_sensors():
    sensors = DECK_SENSOR_POSITIONS + [0.0, 0.0, 0.001]
    with raises(ValueError, match="XY plane"):
        estimate_drone_pose(zeros((4, 2)), sensors)
    with raises(ValueError, match=r"shape \(n, 3\)"):
        estimate_drone_pose(zeros((4, 2)), zeros((4, 2)))


def test_initial_guess_is_exact_without_noise():
    scene = create_scene()
    guess = estimate_initial_guess(scene.samples)

    counts = {
        bs_id: sum(bs_id in sample for sample in scene.samples)
        for bs_id in scene.base_stations
    }
    assert guess.reference_base_station == max(sorted(counts), key=counts.__getitem__)
    assert sorted(guess.base_stations) == sorted(scene.base_stations)
    assert len(guess.samples) == len(scene.samples)

    to_reference = scene.base_stations[guess.reference_base_station].inverse()
    for bs_id, pose in scene.base_stations.items():
        expected = to_reference.compose(pose)
        actual = guess.base_stations[bs_id]
        assert allclose(actual.translation, expected.translation, atol=1e-8)
        assert rotation_angle_between(actual.rotation, expected.rotation) < 1e-6

    for drone, actual in zip(scene.drones, guess.samples):
        expected = to_reference.compose(drone)
        assert allclose(actual.translation, expected.translation, atol=1e-8)
        assert rotation_angle_between(actual.rotation, expected.rotation) < 1e-6


@mark.parametrize("seed", [1, 2, 3])
@mark.parametrize("noise", [1e-4, 2.5e-4], ids=["lighthouse 1", "lighthouse 2"])
def test_full_pipeline_with_noisy_measurements(noise, seed):
    scene = create_scene(noise=noise, num_free_samples=100, seed=seed)

    guess = estimate_initial_guess(scene.samples)
    solution = refine_geometry(scene.samples, guess.base_stations, guess.samples)
    transform = find_room_transform(
        solution.samples[0],
        [pose.translation for pose in solution.samples[scene.x_axis]],
        [pose.translation for pose in solution.samples[scene.xy_plane]],
    )

    assert solution.converged
    assert solution.iterations < 20
    assert solution.reference_base_station == guess.reference_base_station
    for bs_id, expected in scene.base_stations.items():
        actual = transform.compose(solution.base_stations[bs_id])
        assert norm(actual.translation - expected.translation) < 0.02
        assert rotation_angle_between(actual.rotation, expected.rotation) < radians(0.5)


@mark.parametrize("seed", [1, 2, 3])
def test_initial_guess_is_close_with_noisy_measurements(seed):
    # Some distance estimates are far off with this much noise; without
    # ignoring them, the base stations would be off by more than a metre
    scene = create_scene(noise=2.5e-4, num_free_samples=100, seed=seed)
    guess = estimate_initial_guess(scene.samples)

    to_reference = scene.base_stations[guess.reference_base_station].inverse()
    for bs_id, pose in scene.base_stations.items():
        expected = to_reference.compose(pose)
        actual = guess.base_stations[bs_id]
        assert norm(actual.translation - expected.translation) < 0.3
        assert rotation_angle_between(actual.rotation, expected.rotation) < radians(5)


def test_base_stations_sharing_most_samples_are_placed_first():
    scene = create_scene(num_free_samples=60)

    # Base station 0 appears only in the last 15 samples
    for sample in scene.samples[:-15]:
        sample.pop(0, None)
    candidates = [
        {bs_id: estimate_drone_pose(angles) for bs_id, angles in sample.items()}
        for sample in scene.samples
    ]

    order = list(_place_base_stations(candidates))

    assert order[-1] == 0
    assert sorted(order) == [0, 1, 2, 3]


def test_initial_guess_reports_base_stations_that_cannot_be_placed():
    scene = create_scene(num_free_samples=20)
    lonely = look_at((0.0, 4.0, 2.0), (0.0, 0.0, 0.0))

    # The extra base station appears in only two samples
    for index in (5, 6):
        scene.samples[index].update(simulate_sample({9: lonely}, scene.drones[index]))
    assert sum(9 in sample for sample in scene.samples) == 2

    with raises(ValueError, match="too few samples with the others: 9$"):
        estimate_initial_guess(scene.samples)


def test_initial_guess_rejects_empty_input():
    with raises(ValueError, match="At least one sample"):
        estimate_initial_guess([])
    with raises(ValueError, match="Sample 0 has no measurements"):
        estimate_initial_guess([{}])
