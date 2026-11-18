"""Pose estimation of a planar object from a single perspective view.

Implements the infinitesimal plane-based pose estimation (IPPE) method of
T. Collins and A. Bartoli: Infinitesimal Plane-Based Pose Estimation.
International Journal of Computer Vision 109(3), pp. 252-286, 2014.

The functions here use the usual pinhole camera conventions: the camera looks
along its Z axis, and a point at ``(x, y, z)`` in the reference frame of the
camera appears at ``(x / z, y / z)`` in normalized image coordinates.

Summary of the method. Let the object points lie in the XY plane of the
reference frame of the object, centred around its origin. A homography maps
the object points to the image. At the origin of the object, the homography
behaves like a linear map, given by its Jacobian ``J``, which is related to the
pose by ``J = [I | -v] R[:, :2] / t_z``. Here ``v`` is the image of the origin,
``R`` is the rotation of the object and ``t_z`` is the depth of its origin.
After rotating the camera to make it look straight at ``v``, the top left 2x2
block of the rotated object rotation is a known matrix scaled by ``t_z``. The
top left 2x2 block of a rotation matrix has a largest singular value of 1,
which yields the depth. The third row of the rotation is determined up to its
sign, which yields two candidate rotations.
"""

from __future__ import annotations

from typing import NamedTuple

from numpy import (
    append,
    arctan2,
    array,
    column_stack,
    cross,
    einsum,
    eye,
    float64,
    hstack,
    mean,
    ones_like,
    outer,
    sqrt,
    vstack,
    zeros,
    zeros_like,
)
from numpy.linalg import lstsq, norm, solve, svd
from numpy.typing import ArrayLike, NDArray

from .arrays import normalize, to_float_array
from .model import Pose
from .rotation import rotation_vector_to_matrix

__all__ = ("PoseCandidate", "estimate_planar_pose", "fit_homography")


class PoseCandidate(NamedTuple):
    """A possible pose of an object, as seen from the camera."""

    pose: Pose
    """The pose of the object in the reference frame of the camera."""

    error: float
    """Root mean square distance between the observed image points and the
    projections of the object points, in normalized image coordinates.
    """


def estimate_planar_pose(
    object_points: ArrayLike, image_points: ArrayLike
) -> list[PoseCandidate]:
    """Estimates the pose of a planar object from the images of its points.

    Args:
        object_points: array of shape ``(n, 2)``, the positions of the points
            of the object in the XY plane of the reference frame of the object
        image_points: array of shape ``(n, 2)``, the normalized image
            coordinates of the points

    Returns:
        the two possible poses of the object, the one with the smaller error
        first. The two poses are the same if the camera sees the object
        straight on.

    Raises:
        ValueError: if there are fewer than four points, or the points do not
            determine a pose
    """
    obj = to_float_array(object_points)
    img = to_float_array(image_points)
    if obj.ndim != 2 or obj.shape[1] != 2 or img.shape != obj.shape:
        raise ValueError("Object and image points must be arrays of shape (n, 2)")
    if len(obj) < 4:
        raise ValueError("At least four points are needed")

    # Work around the centroid of the object, where the method is the most
    # accurate
    centroid = obj.mean(axis=0)
    homography = fit_homography(obj - centroid, img)
    h = homography / homography[2, 2]

    v = h[:2, 2]
    jacobian = h[:2, :2] - outer(v, h[2, :2])

    # Rotation that turns the Z axis towards the image of the origin
    view_rotation = _rotation_from_z_axis_to(append(v, 1.0))
    b = (hstack((eye(2), -v[:, None])) @ view_rotation)[:, :2]
    a = solve(b, jacobian)

    largest_singular_value = svd(a, compute_uv=False)[0]
    if largest_singular_value <= 0:
        raise ValueError("Points do not determine a pose")
    top_left = a / largest_singular_value

    candidates = []
    for third_row in _third_row_candidates(top_left):
        first_columns = vstack((top_left, third_row))
        rotation = view_rotation @ column_stack(
            (first_columns, cross(first_columns[:, 0], first_columns[:, 1]))
        )
        object_points_3d = column_stack((obj - centroid, zeros(len(obj))))
        translation = _fit_translation(rotation, object_points_3d, img)

        # Express the pose relative to the origin of the object, not its
        # centroid
        translation = translation - rotation @ append(centroid, 0.0)
        pose = Pose(rotation=rotation, translation=translation)
        error = _reprojection_error(pose, column_stack((obj, zeros(len(obj)))), img)
        candidates.append(PoseCandidate(pose, error))

    candidates.sort(key=lambda candidate: candidate.error)
    return candidates


def fit_homography(source: ArrayLike, target: ArrayLike) -> NDArray[float64]:
    """Finds the homography that maps the source points to the target points,
    with the direct linear transformation method.

    Args:
        source: array of shape ``(n, 2)``
        target: array of shape ``(n, 2)``; at least four points are needed

    Returns:
        the homography as a 3x3 matrix, defined up to scale
    """
    src = to_float_array(source)
    dst = to_float_array(target)

    x, y = src[:, 0], src[:, 1]
    u, w = dst[:, 0], dst[:, 1]
    zero, one = zeros_like(x), ones_like(x)
    rows = vstack(
        (
            column_stack((x, y, one, zero, zero, zero, -u * x, -u * y, -u)),
            column_stack((zero, zero, zero, x, y, one, -w * x, -w * y, -w)),
        )
    )
    _, _, vt = svd(rows)
    return vt[-1].reshape(3, 3)


def _rotation_from_z_axis_to(direction: NDArray[float64]) -> NDArray[float64]:
    """Returns the smallest rotation that turns the Z axis to the given
    direction.
    """
    target = normalize(direction)
    axis = cross((0.0, 0.0, 1.0), target)
    sin_angle = norm(axis)
    if sin_angle < 1e-12:
        return eye(3)
    angle = arctan2(sin_angle, target[2])
    return rotation_vector_to_matrix(axis / sin_angle * angle)


def _third_row_candidates(
    top_left: NDArray[float64],
) -> list[NDArray[float64]]:
    """Returns the possible values of the first two elements of the third row
    of a rotation matrix with the given top left 2x2 block.

    The first two columns of the rotation matrix are orthonormal. With ``M``
    being the top left block and ``b`` the third row, ``b b^T`` must equal
    ``I - M^T M``, which determines ``b`` up to its sign.
    """
    rest = eye(2) - top_left.T @ top_left
    if rest[0, 0] >= rest[1, 1]:
        first = sqrt(max(rest[0, 0], 0.0))
        second = rest[0, 1] / first if first > 1e-12 else 0.0
    else:
        second = sqrt(max(rest[1, 1], 0.0))
        first = rest[0, 1] / second if second > 1e-12 else 0.0

    b = array((first, second))
    return [b, -b]


def _fit_translation(
    rotation: NDArray[float64],
    object_points: NDArray[float64],
    image_points: NDArray[float64],
) -> NDArray[float64]:
    """Returns the translation that minimizes the algebraic reprojection error
    of the object points for the given rotation.

    A point ``p`` of the object is seen at ``(u, w)`` if ``x - u z = 0`` and
    ``y - w z = 0``, where ``(x, y, z) = R p + t``. These equations are linear
    in ``t``.
    """
    rotated = object_points @ rotation.T
    coefficients = zeros((len(image_points), 2, 3))
    coefficients[:, 0, 0] = 1.0
    coefficients[:, 1, 1] = 1.0
    coefficients[:, :, 2] = -image_points
    rhs = -einsum("nij,nj->ni", coefficients, rotated)
    translation, *_ = lstsq(coefficients.reshape(-1, 3), rhs.ravel(), rcond=None)
    return translation


def _reprojection_error(
    pose: Pose, object_points: NDArray[float64], image_points: NDArray[float64]
) -> float:
    camera = pose.apply(object_points)
    projected = camera[:, :2] / camera[:, 2:]
    return float(sqrt(mean(((projected - image_points) ** 2).sum(axis=-1))))
