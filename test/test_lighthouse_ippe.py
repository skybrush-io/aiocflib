from numpy import (
    allclose,
    append,
    array,
    column_stack,
    diag,
    median,
    ndarray,
    ones,
    zeros,
)
from numpy.linalg import det, norm
from numpy.random import Generator, default_rng
from pytest import mark, raises

from aiocflib.lighthouse.ippe import estimate_planar_pose, fit_homography
from aiocflib.lighthouse.model import Pose
from aiocflib.lighthouse.rotation import rotation_vector_to_matrix

OBJECT_POINTS = array(
    [[-0.015, 0.0075], [-0.015, -0.0075], [0.015, 0.0075], [0.015, -0.0075]]
)


def project(pose: Pose, object_points: ndarray) -> ndarray:
    camera = pose.apply(column_stack((object_points, zeros(len(object_points)))))
    return camera[:, :2] / camera[:, 2:]


def random_view(rng: Generator) -> Pose:
    """Returns a random pose of an object in front of the camera, facing it."""
    rotation = rotation_vector_to_matrix(rng.normal(scale=0.5, size=3))
    # Flip the object around its X axis to make its Z axis face the camera
    rotation = rotation @ diag([1.0, -1.0, -1.0])
    translation = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(2, 4))
    return Pose.create(rotation, translation)


def closest_candidate_distance(candidates, pose: Pose) -> float:
    return min(
        norm(c.pose.rotation - pose.rotation)
        + norm(c.pose.translation - pose.translation)
        for c in candidates
    )


def test_fit_homography_maps_points_exactly():
    rng = default_rng(1)
    expected = rng.normal(size=(3, 3))
    source = rng.normal(size=(6, 2))
    target = column_stack((source, ones(6))) @ expected.T
    target = target[:, :2] / target[:, 2:]

    homography = fit_homography(source, target)

    assert allclose(homography / homography[2, 2], expected / expected[2, 2])


@mark.parametrize("offset", [(0.0, 0.0), (0.004, -0.002)], ids=["centred", "offset"])
def test_one_candidate_is_exact_without_noise(offset):
    rng = default_rng(2)
    object_points = OBJECT_POINTS + offset
    for _ in range(100):
        pose = random_view(rng)
        candidates = estimate_planar_pose(object_points, project(pose, object_points))

        assert len(candidates) == 2
        assert candidates[0].error <= candidates[1].error
        assert closest_candidate_distance(candidates, pose) < 1e-9
        for candidate in candidates:
            assert allclose(det(candidate.pose.rotation), 1.0)


def test_candidates_coincide_for_object_seen_straight_on():
    pose = Pose.create(diag([1.0, -1.0, -1.0]), (0.0, 0.0, 3.0))
    candidates = estimate_planar_pose(OBJECT_POINTS, project(pose, OBJECT_POINTS))

    # The rotation involves the square root of a value that is zero for an
    # object seen straight on; rounding errors of 1e-16 become 1e-8
    for candidate in candidates:
        assert allclose(candidate.pose.rotation, pose.rotation, atol=1e-6)
        assert allclose(candidate.pose.translation, pose.translation)


def test_distance_is_accurate_with_noise():
    rng = default_rng(3)
    errors = []
    for _ in range(200):
        pose = random_view(rng)
        image = project(pose, OBJECT_POINTS) + rng.normal(scale=1e-4, size=(4, 2))
        best = estimate_planar_pose(OBJECT_POINTS, image)[0]
        distance = norm(pose.translation)
        errors.append(abs(norm(best.pose.translation) - distance) / distance)

    assert median(errors) < 0.02


def test_estimate_planar_pose_rejects_invalid_input():
    with raises(ValueError, match="At least four points"):
        estimate_planar_pose(OBJECT_POINTS[:3], OBJECT_POINTS[:3])
    with raises(ValueError, match=r"shape \(n, 2\)"):
        estimate_planar_pose(OBJECT_POINTS, OBJECT_POINTS[:3])
    with raises(ValueError, match=r"shape \(n, 2\)"):
        estimate_planar_pose(zeros((4, 3)), zeros((4, 3)))


def test_object_far_from_its_origin_is_accurate_with_noise():
    # The method works around the centroid of the object; working around a
    # distant origin would amplify the noise
    rng = default_rng(4)
    object_points = OBJECT_POINTS + (0.3, 0.2)
    centroid = append(object_points.mean(axis=0), 0.0)
    errors = []
    for _ in range(200):
        pose = random_view(rng)
        image = project(pose, object_points) + rng.normal(scale=1e-4, size=(4, 2))
        best = estimate_planar_pose(object_points, image)[0]
        expected = pose.apply(centroid)
        errors.append(norm(best.pose.apply(centroid) - expected) / norm(expected))

    assert median(errors) < 0.02
