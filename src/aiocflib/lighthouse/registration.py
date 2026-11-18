"""Rigid registration of point sets."""

from __future__ import annotations

from numpy import array_equal, median, ones, sqrt
from numpy.linalg import norm, svd
from numpy.typing import ArrayLike

from .arrays import to_float_array
from .model import Pose
from .rotation import orthonormalize

__all__ = ("fit_rigid_transform", "fit_rigid_transform_robust")


def fit_rigid_transform(
    source: ArrayLike,
    target: ArrayLike,
    *,
    min_spread: float = 0.0,
) -> Pose:
    """Finds the rotation and translation that map the source points onto the
    target points with the smallest sum of squared distances.

    Uses the singular value decomposition of the cross-covariance matrix of the
    points (the Kabsch algorithm).

    Args:
        source: array of shape ``(n, 3)``
        target: array of shape ``(n, 3)``
        min_spread: the source points must extend at least this far, measured
            as their standard deviation, in two independent directions. Points
            close to a line do not determine the rotation around the line.

    Returns:
        the transformation that maps the source points to the target points

    Raises:
        ValueError: if there are fewer than three points, or they are too
            close to a line
    """
    src = to_float_array(source, (-1, 3))
    dst = to_float_array(target, (-1, 3))
    if src.shape != dst.shape:
        raise ValueError("Source and target must contain the same number of points")
    if len(src) < 3:
        raise ValueError("At least three points are needed")

    src_centroid = src.mean(axis=0)
    dst_centroid = dst.mean(axis=0)
    src_centered = src - src_centroid
    dst_centered = dst - dst_centroid

    spread = svd(src_centered, compute_uv=False) / sqrt(len(src))
    if spread[1] <= max(min_spread, 1e-12):
        raise ValueError("Points are too close to a line")

    rotation = orthonormalize(dst_centered.T @ src_centered)
    return Pose(rotation=rotation, translation=dst_centroid - rotation @ src_centroid)


def fit_rigid_transform_robust(
    source: ArrayLike,
    target: ArrayLike,
    *,
    min_spread: float = 0.0,
    threshold: float = 3.0,
    min_distance: float = 0.0,
    iterations: int = 5,
) -> Pose:
    """Finds the rotation and translation that map the source points onto the
    target points, ignoring the pairs of points that do not fit.

    The transformation is fitted to all the points first. Then the points
    whose distance after the transformation is more than the given multiple of
    the median distance are dropped, and the transformation is fitted again to
    the remaining points. This is repeated until no more points are dropped.

    Args:
        source: array of shape ``(n, 3)``
        target: array of shape ``(n, 3)``
        min_spread: see `fit_rigid_transform()`
        threshold: points farther than this multiple of the median distance
            are dropped
        min_distance: points closer than this are never dropped, which
            prevents dropping points when the fit is nearly perfect
        iterations: maximum number of times to fit the transformation

    Returns:
        the transformation that maps the source points to the target points

    Raises:
        ValueError: if there are fewer than three points, or the points that
            are kept are too close to a line
    """
    src = to_float_array(source, (-1, 3))
    dst = to_float_array(target, (-1, 3))
    keep = ones(len(src), dtype=bool)

    pose = fit_rigid_transform(src, dst, min_spread=min_spread)
    for _ in range(iterations - 1):
        distances = norm(pose.apply(src) - dst, axis=-1)
        limit = max(threshold * float(median(distances)), min_distance)
        new_keep = distances <= limit
        if array_equal(new_keep, keep):
            break
        keep = new_keep
        pose = fit_rigid_transform(src[keep], dst[keep], min_spread=min_spread)

    return pose
