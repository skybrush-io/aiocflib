from numpy import allclose, arange, outer, zeros
from numpy.random import Generator, default_rng
from pytest import raises

from aiocflib.lighthouse.model import Pose
from aiocflib.lighthouse.registration import (
    fit_rigid_transform,
    fit_rigid_transform_robust,
)
from aiocflib.lighthouse.rotation import mean_rotation, rotation_vector_to_matrix


def random_pose(rng: Generator) -> Pose:
    return Pose.create(
        rotation_vector_to_matrix(rng.normal(size=3)), rng.normal(size=3)
    )


def test_fit_rigid_transform_is_exact_without_noise():
    rng = default_rng(1)
    expected = random_pose(rng)
    source = rng.normal(size=(10, 3))

    pose = fit_rigid_transform(source, expected.apply(source))

    assert allclose(pose.rotation, expected.rotation)
    assert allclose(pose.translation, expected.translation)


def test_fit_rigid_transform_rejects_degenerate_input():
    line = outer(arange(5.0), (1.0, 2.0, 0.5))

    with raises(ValueError, match="too close to a line"):
        fit_rigid_transform(line, line)
    with raises(ValueError, match="too close to a line"):
        fit_rigid_transform(
            line + [[0, 0, 0], [0, 0.01, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]],
            line,
            min_spread=0.1,
        )
    with raises(ValueError, match="At least three points"):
        fit_rigid_transform(line[:2], line[:2])
    with raises(ValueError, match="same number of points"):
        fit_rigid_transform(line, line[:4])


def test_fit_rigid_transform_robust_ignores_outliers():
    rng = default_rng(2)
    expected = random_pose(rng)
    source = rng.normal(size=(50, 3))
    target = expected.apply(source) + rng.normal(scale=0.01, size=(50, 3))
    target[:5] += rng.normal(scale=2.0, size=(5, 3))

    plain = fit_rigid_transform(source, target)
    robust = fit_rigid_transform_robust(source, target)

    assert not allclose(plain.translation, expected.translation, atol=0.02)
    assert allclose(robust.rotation, expected.rotation, atol=0.01)
    assert allclose(robust.translation, expected.translation, atol=0.01)


def test_fit_rigid_transform_robust_keeps_points_within_min_distance():
    rng = default_rng(3)
    expected = random_pose(rng)
    source = rng.normal(size=(20, 3))
    target = expected.apply(source)

    # With an almost perfect fit, every point would look like an outlier
    # compared to the median distance; the minimum distance keeps them
    target[0] += 1e-3
    pose = fit_rigid_transform_robust(source, target, min_distance=0.01)
    exact = fit_rigid_transform(source, target)

    assert allclose(pose.translation, exact.translation)


def test_mean_rotation():
    rng = default_rng(4)
    center = rotation_vector_to_matrix(rng.normal(size=3))
    offset = rotation_vector_to_matrix((0.0, 0.0, 0.1))

    rotations = [center @ offset, center @ offset.T]
    assert allclose(mean_rotation(rotations), center)
    assert allclose(mean_rotation(rotations, [1.0, 0.0]), center @ offset)

    with raises(ValueError, match="At least one rotation"):
        mean_rotation(zeros((0, 3, 3)))
