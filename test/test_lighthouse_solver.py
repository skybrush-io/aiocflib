from lighthouse_scenes import (
    create_scene,
    initial_guess,
    rotation_angle_between,
    to_solver_frame,
)
from numpy import allclose, argmax, array_equal, eye, radians, tan, zeros
from numpy.linalg import norm
from numpy.random import default_rng
from pytest import mark, raises

from aiocflib.crazyflie.lighthouse import LighthouseBsGeometry
from aiocflib.lighthouse.alignment import find_room_transform
from aiocflib.lighthouse.arrays import to_float_array
from aiocflib.lighthouse.initial_guess import estimate_initial_guess
from aiocflib.lighthouse.model import DECK_SENSOR_POSITIONS, Pose, predict_sweep_angles
from aiocflib.lighthouse.rotation import rotation_vector_to_matrix
from aiocflib.lighthouse.solver import (
    _apply_step,
    _create_problem,
    _linearize,
    _residuals,
    _State,
    refine_geometry,
)


def test_predict_sweep_angles_follows_firmware_convention():
    base_station = Pose.create()
    angles = predict_sweep_angles(base_station, [2.0, 2.0 * tan(0.1), 2.0 * tan(-0.2)])
    assert allclose(angles, (0.1, -0.2))

    # A base station on the X axis that faces the origin sees the origin
    # straight ahead
    facing_back = Pose.create([[-1, 0, 0], [0, -1, 0], [0, 0, 1]], (3.0, 0.0, 0.0))
    assert allclose(predict_sweep_angles(facing_back, [0.0, 0.0, 0.0]), (0.0, 0.0))
    assert predict_sweep_angles(facing_back, [0.0, -1.0, 0.0])[0] > 0


def test_pose_operations():
    rng = default_rng(1)
    pose = Pose.create(
        rotation_vector_to_matrix(rng.normal(size=3)), rng.normal(size=3)
    )
    points = rng.normal(size=(5, 3))

    assert allclose(pose.inverse().apply(pose.apply(points)), points)
    assert allclose(pose.compose(pose.inverse()).rotation, eye(3))
    assert allclose(pose.compose(pose).apply(points), pose.apply(pose.apply(points)))


def test_pose_round_trip_through_bs_geometry():
    pose = Pose.create([[0, -1, 0], [1, 0, 0], [0, 0, 1]], (1.0, 2.0, 3.0))
    geometry = pose.to_bs_geometry()
    assert isinstance(geometry, LighthouseBsGeometry)
    assert geometry.valid
    assert geometry.origin == (1.0, 2.0, 3.0)

    restored = Pose.from_bs_geometry(geometry)
    assert allclose(restored.rotation, pose.rotation)
    assert allclose(restored.translation, pose.translation)


def test_derivatives_match_finite_differences():
    scene = create_scene(num_free_samples=6)
    base_stations, drones = initial_guess(scene)
    problem = _create_problem(
        scene.samples, base_stations, drones, DECK_SENSOR_POSITIONS
    )
    state = _State(
        bs_rotations=to_float_array(
            [base_stations[i].rotation for i in problem.bs_ids]
        ),
        bs_origins=to_float_array(
            [base_stations[i].translation for i in problem.bs_ids]
        ),
        sample_rotations=to_float_array([pose.rotation for pose in drones]),
        sample_translations=to_float_array([pose.translation for pose in drones]),
    )
    bs_jac, sample_jac, _ = _linearize(problem, state)

    num_bs, num_samples = len(problem.bs_ids), problem.num_samples
    eps = 1e-7

    def numeric(bs_step, sample_step):
        plus = _residuals(problem, _apply_step(state, bs_step, sample_step))
        minus = _residuals(problem, _apply_step(state, -bs_step, -sample_step))
        return ((plus - minus) / (2 * eps)).reshape(-1, 2)

    # The fixed base station has zero derivatives by design
    free_rows = problem.bs_index != problem.fixed_bs_index
    assert not bs_jac[~free_rows].any()

    for param in range(6):
        bs_step = zeros((num_bs, 6))
        bs_step[:, param] = eps
        assert allclose(
            numeric(bs_step, zeros((num_samples, 6)))[free_rows],
            bs_jac[free_rows, :, param],
            atol=1e-5,
        )

        sample_step = zeros((num_samples, 6))
        sample_step[:, param] = eps
        assert allclose(
            numeric(zeros((num_bs, 6)), sample_step),
            sample_jac[:, :, param],
            atol=1e-5,
        )


def test_solver_recovers_exact_geometry_without_noise():
    scene = create_scene()
    base_stations, drones = initial_guess(scene)

    solution = refine_geometry(scene.samples, base_stations, drones)

    assert solution.converged
    assert solution.iterations < 20
    assert solution.error < 1e-9
    assert (solution.sample_errors < 1e-9).all()

    transform = to_solver_frame(scene, base_stations, solution.reference_base_station)
    for bs_id, pose in scene.base_stations.items():
        expected = transform.compose(pose)
        actual = solution.base_stations[bs_id]
        assert allclose(actual.translation, expected.translation, atol=1e-6)
        assert rotation_angle_between(actual.rotation, expected.rotation) < 1e-6

    for expected_world, actual in zip(scene.drones, solution.samples):
        expected = transform.compose(expected_world)
        assert allclose(actual.translation, expected.translation, atol=1e-6)


# The firmware assumes a noise of 4e-4 rad for Lighthouse V1 and 1e-3 rad for
# Lighthouse V2 in single readings. Samples are averages of many readings with
# less noise.
@mark.parametrize("noise", [1e-4, 2.5e-4], ids=["lighthouse 1", "lighthouse 2"])
def test_solver_and_alignment_with_noisy_measurements(noise):
    scene = create_scene(noise=noise, num_free_samples=100)
    base_stations, drones = initial_guess(scene)

    solution = refine_geometry(scene.samples, base_stations, drones)
    assert solution.converged
    assert solution.iterations < 30
    assert solution.error < 2 * noise

    transform = find_room_transform(
        solution.samples[0],
        [pose.translation for pose in solution.samples[scene.x_axis]],
        [pose.translation for pose in solution.samples[scene.xy_plane]],
    )

    for bs_id, expected in scene.base_stations.items():
        actual = transform.compose(solution.base_stations[bs_id])
        assert norm(actual.translation - expected.translation) < 0.02
        assert rotation_angle_between(actual.rotation, expected.rotation) < radians(0.5)


def test_solver_converges_from_rough_initial_guess():
    scene = create_scene()
    base_stations, drones = initial_guess(scene, angle=0.3, distance=0.5)

    solution = refine_geometry(scene.samples, base_stations, drones)

    assert solution.converged
    assert solution.error < 1e-9


def test_solver_keeps_most_observed_base_station_fixed():
    scene = create_scene(num_free_samples=30)
    counts = {
        bs_id: sum(bs_id in sample for sample in scene.samples)
        for bs_id in scene.base_stations
    }
    expected = max(sorted(counts), key=counts.__getitem__)
    base_stations, drones = initial_guess(scene)

    solution = refine_geometry(scene.samples, base_stations, drones)

    assert solution.reference_base_station == expected
    actual = solution.base_stations[expected]
    assert array_equal(actual.rotation, base_stations[expected].rotation)
    assert array_equal(actual.translation, base_stations[expected].translation)


def test_solver_reports_samples_with_large_errors():
    scene = create_scene(num_free_samples=30)
    base_stations, drones = initial_guess(scene)
    bad = 7
    scene.samples[bad] = {
        bs_id: angles + 0.05 for bs_id, angles in scene.samples[bad].items()
    }

    solution = refine_geometry(scene.samples, base_stations, drones)

    assert int(argmax(solution.sample_errors)) == bad


def test_solver_rejects_invalid_input():
    scene = create_scene(num_free_samples=2)
    base_stations, drones = initial_guess(scene)

    with raises(ValueError, match="No initial guess for base stations: 2"):
        refine_geometry(
            scene.samples,
            {k: v for k, v in base_stations.items() if k != 2},
            drones,
        )
    with raises(ValueError, match="one initial pose for each sample"):
        refine_geometry(scene.samples, base_stations, drones[:-1])
    with raises(ValueError, match="At least one sample"):
        refine_geometry([], base_stations, [])
    with raises(ValueError, match="Sample 1 has no measurements"):
        refine_geometry([scene.samples[0], {}], base_stations, drones[:2])
    with raises(ValueError, match=r"must have shape \(4, 2\)"):
        refine_geometry([{0: zeros((3, 2))}], base_stations, drones[:1])
    with raises(ValueError, match="shape \\(n, 3\\)"):
        refine_geometry(scene.samples, base_stations, drones, sensor_positions=zeros(3))


def test_solver_stops_when_base_stations_stop_moving():
    # Single measurements with the noise of Lighthouse V2 barely constrain the
    # orientation of the deck in some samples. The orientation keeps changing
    # slowly while the base stations have long settled.
    scene = create_scene(noise=1e-3, num_free_samples=300, seed=2, tilt=radians(3))
    guess = estimate_initial_guess(scene.samples)

    solution = refine_geometry(scene.samples, guess.base_stations, guess.samples)
    exhaustive = refine_geometry(
        scene.samples, guess.base_stations, guess.samples, step_tolerance=0.0
    )

    assert solution.converged
    assert solution.iterations < 60 < exhaustive.iterations
    for bs_id, pose in solution.base_stations.items():
        other = exhaustive.base_stations[bs_id]
        assert allclose(pose.translation, other.translation, atol=1e-5)
