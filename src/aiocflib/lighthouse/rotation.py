"""Conversions between rotation matrices and rotation vectors.

A rotation vector represents a rotation around the axis of the vector, by an
angle equal to the length of the vector, in radians. All functions accept
batches of rotations: the last one or two axes of the input are the vector or
the matrix, and the leading axes are preserved.
"""

from __future__ import annotations

from numpy import (
    arctan2,
    argmax,
    broadcast_to,
    cos,
    diagonal,
    einsum,
    eye,
    float64,
    ones,
    pi,
    sign,
    sin,
    stack,
    swapaxes,
    take_along_axis,
    trace,
    where,
    zeros,
    zeros_like,
)
from numpy.linalg import det, norm, svd
from numpy.typing import ArrayLike, NDArray

from .arrays import normalize, to_float_array

__all__ = (
    "matrix_to_rotation_vector",
    "mean_rotation",
    "orthonormalize",
    "rotation_vector_to_matrix",
    "skew",
)


_SMALL_ANGLE = 1e-6
"""Angles below this limit (in radians) are handled with Taylor series
expansions to avoid dividing by values close to zero.
"""


def skew(vectors: ArrayLike) -> NDArray[float64]:
    """Returns the skew-symmetric matrices of the given vectors.

    The skew-symmetric matrix of a vector ``v`` is the matrix ``S`` for which
    ``S @ w`` equals the cross product of ``v`` and ``w`` for any vector ``w``.

    Args:
        vectors: array of shape ``(..., 3)``

    Returns:
        array of shape ``(..., 3, 3)``
    """
    v = to_float_array(vectors)
    x, y, z = v[..., 0], v[..., 1], v[..., 2]
    zero = zeros_like(x)
    return stack(
        (
            stack((zero, -z, y), axis=-1),
            stack((z, zero, -x), axis=-1),
            stack((-y, x, zero), axis=-1),
        ),
        axis=-2,
    )


def rotation_vector_to_matrix(vectors: ArrayLike) -> NDArray[float64]:
    """Converts rotation vectors to rotation matrices.

    Args:
        vectors: array of shape ``(..., 3)``

    Returns:
        array of shape ``(..., 3, 3)``
    """
    v = to_float_array(vectors)
    angle = norm(v, axis=-1)
    small = angle < _SMALL_ANGLE
    safe_angle = where(small, 1.0, angle)
    angle_sq = angle * angle

    # Coefficients of Rodrigues' formula, R = I + a K + b K^2 where K is the
    # skew-symmetric matrix of the rotation vector
    a = where(small, 1.0 - angle_sq / 6.0, sin(angle) / safe_angle)
    b = where(small, 0.5 - angle_sq / 24.0, (1.0 - cos(angle)) / (safe_angle**2))

    k = skew(v)
    identity = broadcast_to(eye(3), k.shape)
    return identity + a[..., None, None] * k + b[..., None, None] * (k @ k)


def matrix_to_rotation_vector(matrices: ArrayLike) -> NDArray[float64]:
    """Converts rotation matrices to rotation vectors.

    The angle of the returned rotation vectors is between 0 and pi.

    Args:
        matrices: array of shape ``(..., 3, 3)``; the matrices must be proper
            rotation matrices

    Returns:
        array of shape ``(..., 3)``
    """
    r = to_float_array(matrices)

    # Twice the sine of the angle times the unit axis
    axis_sin = stack(
        (
            r[..., 2, 1] - r[..., 1, 2],
            r[..., 0, 2] - r[..., 2, 0],
            r[..., 1, 0] - r[..., 0, 1],
        ),
        axis=-1,
    )

    # arctan2() keeps full precision at all angles, unlike arccos() of the
    # cosine alone, which loses precision close to zero
    cos_angle = (trace(r, axis1=-2, axis2=-1) - 1.0) / 2.0
    sin_angle = norm(axis_sin, axis=-1) / 2.0
    angle = arctan2(sin_angle, cos_angle)

    small = angle < _SMALL_ANGLE
    near_pi = angle > pi - 1e-3
    regular = ~(small | near_pi)

    result = zeros(r.shape[:-1], dtype=float64)

    # Close to zero, sin(angle) is approximately the angle
    result[small] = axis_sin[small] / 2.0

    if regular.any():
        scale = angle[regular] / (2.0 * sin(angle[regular]))
        result[regular] = axis_sin[regular] * scale[..., None]

    if near_pi.any():
        result[near_pi] = _rotation_vector_near_pi(r[near_pi], angle[near_pi])

    return result


def mean_rotation(
    matrices: ArrayLike, weights: ArrayLike | None = None
) -> NDArray[float64]:
    """Returns the average of rotation matrices.

    The average is the rotation matrix closest to the weighted mean of the
    matrices, also known as the chordal L2 mean. It is accurate for rotations
    that are close to each other.

    Args:
        matrices: array of shape ``(n, 3, 3)``
        weights: array of shape ``(n,)``; ``None`` means equal weights

    Returns:
        array of shape ``(3, 3)``
    """
    m = to_float_array(matrices, (-1, 3, 3))
    if len(m) == 0:
        raise ValueError("At least one rotation is needed")
    w = ones(len(m)) if weights is None else to_float_array(weights, (len(m),))
    return orthonormalize(einsum("n,nij->ij", w, m))


def orthonormalize(matrices: ArrayLike) -> NDArray[float64]:
    """Returns the rotation matrices closest to the given matrices.

    Args:
        matrices: array of shape ``(..., 3, 3)``

    Returns:
        array of shape ``(..., 3, 3)`` containing proper rotation matrices
    """
    m = to_float_array(matrices)
    u, _, vt = svd(m)
    determinant = det(u @ vt)
    correction = ones(m.shape[:-1], dtype=float64)
    correction[..., -1] = sign(determinant)
    return (u * correction[..., None, :]) @ vt


def _rotation_vector_near_pi(
    matrices: NDArray[float64], angles: NDArray[float64]
) -> NDArray[float64]:
    """Converts rotation matrices with rotation angles close to pi to rotation
    vectors.

    Near pi, the antisymmetric part of the matrix is close to zero and the axis
    is recovered from the symmetric part instead. For a rotation around unit
    axis ``n`` by angle ``t``, ``R + R^T`` equals
    ``2 cos(t) I + 2 (1 - cos(t)) n n^T``.
    """
    cos_angle = cos(angles)
    outer = (matrices + swapaxes(matrices, -1, -2)) / 2.0
    outer = outer - cos_angle[..., None, None] * eye(3)
    outer = outer / (1.0 - cos_angle)[..., None, None]

    # Use the column with the largest diagonal element for numerical stability
    index = argmax(diagonal(outer, axis1=-2, axis2=-1), axis=-1)
    axis = take_along_axis(outer, index[..., None, None], axis=-1)[..., 0]
    axis = normalize(axis)

    # The symmetric part does not tell the sign of the axis; the antisymmetric
    # part does as long as the angle is not exactly pi
    axis_sin = stack(
        (
            matrices[..., 2, 1] - matrices[..., 1, 2],
            matrices[..., 0, 2] - matrices[..., 2, 0],
            matrices[..., 1, 0] - matrices[..., 0, 1],
        ),
        axis=-1,
    )
    signs = where((axis * axis_sin).sum(axis=-1) < 0, -1.0, 1.0)
    return axis * (signs * angles)[..., None]
