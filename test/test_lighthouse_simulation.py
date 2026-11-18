from numpy import allclose, array, array_equal, diag, pi, radians, std
from numpy.linalg import det
from numpy.random import default_rng
from pytest import raises

from aiocflib.lighthouse.model import DECK_SENSOR_POSITIONS, Pose, predict_sweep_angles
from aiocflib.lighthouse.simulation import look_at, simulate_sample, tilted_pose


def test_look_at_points_x_axis_to_target():
    pose = look_at((3.0, 0.0, 2.0), (0.0, 0.0, 2.0))
    assert allclose(pose.rotation[:, 0], (-1.0, 0.0, 0.0))
    assert allclose(pose.rotation[:, 2], (0.0, 0.0, 1.0))
    assert allclose(det(pose.rotation), 1.0)
    assert allclose(predict_sweep_angles(pose, (0.0, 0.0, 2.0)), (0.0, 0.0))

    with raises(ValueError, match="straight up or down"):
        look_at((0.0, 0.0, 2.0), (0.0, 0.0, 0.0))


def test_tilted_pose():
    pose = tilted_pose((1.0, 2.0, 3.0), yaw=pi / 2)
    assert allclose(pose.translation, (1.0, 2.0, 3.0))
    assert allclose(pose.rotation[:, 0], (0.0, 1.0, 0.0))
    assert allclose(pose.rotation[:, 2], (0.0, 0.0, 1.0))


def test_simulate_sample_without_noise():
    base_station = look_at((2.0, 0.0, 2.0), (0.0, 0.0, 0.0))
    drone = tilted_pose((0.0, 0.0, 0.0), yaw=0.4)

    sample = simulate_sample({3: base_station}, drone)

    assert list(sample) == [3]
    expected = predict_sweep_angles(base_station, drone.apply(DECK_SENSOR_POSITIONS))
    assert array_equal(sample[3], expected)


def test_simulate_sample_with_noise():
    base_station = look_at((2.0, 0.0, 2.0), (0.0, 0.0, 0.0))
    drone = tilted_pose((0.0, 0.0, 0.0))
    exact = simulate_sample({0: base_station}, drone)[0]

    rng = default_rng(1)
    noisy = array(
        [
            simulate_sample({0: base_station}, drone, noise=0.01, rng=rng)[0]
            for _ in range(2000)
        ]
    )
    assert abs(std(noisy - exact) - 0.01) < 0.001


def test_simulate_sample_skips_base_stations_that_cannot_see_the_deck():
    above = look_at((2.0, 0.0, 2.0), (0.0, 0.0, 0.0))
    behind = look_at((-2.0, 0.0, 2.0), (-4.0, 0.0, 2.0))
    # Drone is in front of this one, but 80 degrees to the side
    off_to_the_side = look_at((2.0, 0.0, 2.0), (1.5, 3.0, 2.0))
    below_the_deck = look_at((2.0, 0.0, -1.0), (0.0, 0.0, 0.0))
    base_stations = {0: above, 1: behind, 2: off_to_the_side, 3: below_the_deck}

    assert list(simulate_sample(base_stations, tilted_pose((0.0, 0.0, 0.0)))) == [0]

    # A drone lying upside down sees only the base station below the floor
    upside_down = Pose.create(diag([1.0, -1.0, -1.0]))
    assert list(simulate_sample(base_stations, upside_down)) == [3]


def test_simulate_sample_checks_each_field_of_view_separately():
    # The drone is 80 degrees to the side and 80 degrees below
    base_station = {0: look_at((2.0, 0.0, 2.0), (1.5, 3.0, 2.0))}
    drone = tilted_pose((0.0, 0.0, 0.0))
    narrow, wide = radians(150.0), radians(170.0)

    assert list(simulate_sample(base_station, drone)) == []
    assert list(
        simulate_sample(
            base_station,
            drone,
            horizontal_field_of_view=wide,
            vertical_field_of_view=wide,
        )
    ) == [0]
    assert (
        list(
            simulate_sample(
                base_station,
                drone,
                horizontal_field_of_view=narrow,
                vertical_field_of_view=wide,
            )
        )
        == []
    )
    assert (
        list(
            simulate_sample(
                base_station,
                drone,
                horizontal_field_of_view=wide,
                vertical_field_of_view=narrow,
            )
        )
        == []
    )


def test_simulate_sample_skips_base_stations_facing_away_with_any_field_of_view():
    behind = {0: look_at((-2.0, 0.0, 2.0), (-4.0, 0.0, 2.0))}
    drone = tilted_pose((0.0, 0.0, 0.0))

    sample = simulate_sample(
        behind,
        drone,
        horizontal_field_of_view=2 * pi,
        vertical_field_of_view=2 * pi,
    )

    assert sample == {}
