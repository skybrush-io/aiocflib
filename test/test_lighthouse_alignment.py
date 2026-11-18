from numpy import allclose, diag, eye
from numpy.random import default_rng
from pytest import mark, raises

from aiocflib.lighthouse.alignment import find_room_transform
from aiocflib.lighthouse.model import Pose
from aiocflib.lighthouse.rotation import rotation_vector_to_matrix
from aiocflib.lighthouse.simulation import get_pose_from_position_and_angles


def random_transform(seed: int) -> Pose:
    rng = default_rng(seed)
    return Pose.create(
        rotation_vector_to_matrix(rng.normal(size=3)), rng.normal(size=3)
    )


def assert_poses_close(actual: Pose, expected: Pose) -> None:
    assert allclose(actual.rotation, expected.rotation, atol=1e-9)
    assert allclose(actual.translation, expected.translation, atol=1e-9)


@mark.parametrize("seed", range(5))
def test_find_room_transform_with_xy_plane(seed: int):
    # Maps the room to the arbitrary reference frame of a solution
    to_solver = random_transform(seed)

    # The drone at the origin is tilted; the plane of the points wins
    origin = to_solver.compose(
        get_pose_from_position_and_angles((0, 0, 0), yaw=0.7, tilt=(0.05, -0.03))
    )
    x_axis = to_solver.apply([[0.5, 0, 0], [1.5, 0, 0]])
    xy_plane = to_solver.apply([[0, 1, 0], [1, 2, 0], [-1, 0.5, 0]])

    transform = find_room_transform(origin, list(x_axis), list(xy_plane))

    assert_poses_close(transform, to_solver.inverse())


@mark.parametrize("seed", range(5))
def test_find_room_transform_without_xy_plane_uses_drone_attitude(seed: int):
    to_solver = random_transform(seed)
    origin = to_solver.compose(get_pose_from_position_and_angles((0, 0, 0), yaw=-2.0))
    x_axis = to_solver.apply([[1.0, 0, 0]])

    transform = find_room_transform(origin, list(x_axis))

    assert_poses_close(transform, to_solver.inverse())


def test_find_room_transform_ignores_points_close_to_x_axis():
    origin = get_pose_from_position_and_angles((0, 0, 0))
    x_axis = [[1.0, 0, 0]]

    # Points almost on the X axis do not define a plane; a plane fitted to
    # them would be tilted by a lot
    xy_plane = [[2.0, 0.01, 0.03]]

    transform = find_room_transform(origin, x_axis, xy_plane)

    assert_poses_close(transform, Pose.create())


def test_find_room_transform_points_z_axis_to_drone_side():
    # Drone standing upside down defines the opposite side of the floor
    origin = Pose.create(diag([1.0, -1.0, -1.0]))
    transform = find_room_transform(origin, [[1.0, 0, 0]], [[0, 1.0, 0]])

    assert allclose(transform.apply([0, 0, -1.0]), (0, 0, 1.0))
    assert allclose(transform.apply([0, 1.0, 0]), (0, -1.0, 0))


def test_find_room_transform_averages_x_axis_directions():
    origin = get_pose_from_position_and_angles((0, 0, 0))
    transform = find_room_transform(origin, [[1.0, 0.01, 0], [2.0, -0.02, 0]])

    # Unit directions are averaged and the slight errors cancel out
    assert allclose(transform.rotation, eye(3), atol=1e-4)


def test_find_room_transform_rejects_invalid_input():
    origin = get_pose_from_position_and_angles((0, 0, 0))

    with raises(ValueError, match="At least one position"):
        find_room_transform(origin, [])
    with raises(ValueError, match="must differ from the origin"):
        find_room_transform(origin, [[0, 0, 0]])
    with raises(ValueError, match="must not point up"):
        find_room_transform(origin, [[0, 0, 1.0]])
