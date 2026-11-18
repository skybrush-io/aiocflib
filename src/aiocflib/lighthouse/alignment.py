"""Alignment of an estimated Lighthouse geometry with the coordinate system of
the room.

The solver expresses its result in an arbitrary reference frame. The user
defines the coordinate system of the room by placing the drone at a few
positions on the floor:

- at the origin, facing any direction;
- at one or more points on the positive X axis;
- optionally, at one or more points elsewhere in the XY plane.

The Z axis points up, away from the floor.
"""

from __future__ import annotations

from collections.abc import Sequence

from numpy import amax, concatenate, cross, float64, outer, stack
from numpy.linalg import norm, svd
from numpy.typing import ArrayLike, NDArray

from .arrays import normalize, to_float_array
from .model import Pose

__all__ = ("find_room_transform",)


_MIN_PLANE_SPREAD = 0.05
"""Minimum spread of the points in the XY plane, in metres, perpendicular to
the X axis. The plane is not fitted to the points if they are spread less than
this.
"""


def find_room_transform(
    origin: Pose,
    x_axis: Sequence[ArrayLike],
    xy_plane: Sequence[ArrayLike] = (),
) -> Pose:
    """Finds the transformation from the reference frame of an estimated
    geometry to the coordinate system of the room.

    The direction of the Z axis is estimated by fitting a plane to all the
    given positions if they are spread out enough. Otherwise it is the
    direction of the Z axis of the drone at the origin, assuming that the drone
    is standing level on the floor. Either way, the Z axis points to the same
    side of the floor as the Z axis of the drone at the origin.

    Args:
        origin: pose of the drone at the origin of the room
        x_axis: positions of the drone on the positive X axis
        xy_plane: positions of the drone elsewhere in the XY plane

    Returns:
        the transformation that maps points from the reference frame of the
        estimated geometry to the coordinate system of the room

    Raises:
        ValueError: if the X axis cannot be determined from the positions
    """
    origin_position = origin.translation
    up = origin.rotation[:, 2]

    x_points = to_float_array(x_axis, (-1, 3))
    if len(x_points) == 0:
        raise ValueError("At least one position on the X axis is needed")

    try:
        x_direction = normalize(x_points - origin_position, min_length=1e-3).sum(axis=0)
    except ValueError:
        raise ValueError(
            "Positions on the X axis must differ from the origin"
        ) from None

    plane_points = concatenate(
        (origin_position[None], x_points, to_float_array(xy_plane, (-1, 3)))
    )
    z_direction = _fit_plane_normal(plane_points, x_direction)
    if z_direction is None:
        z_direction = up
    elif z_direction @ up < 0:
        z_direction = -z_direction

    z_axis = normalize(z_direction)
    try:
        x_axis_dir = normalize(
            x_direction - (x_direction @ z_axis) * z_axis, min_length=1e-6
        )
    except ValueError:
        raise ValueError("The X axis must not point up") from None
    y_axis = cross(z_axis, x_axis_dir)

    # Rows are the axes of the room expressed in the estimated reference frame
    rotation = stack((x_axis_dir, y_axis, z_axis))
    return Pose(rotation=rotation, translation=-(rotation @ origin_position))


def _fit_plane_normal(
    points: NDArray[float64], x_direction: NDArray[float64]
) -> NDArray[float64] | None:
    """Returns the normal vector of the plane that fits the given points best,
    or ``None`` if the points are too close to a line.
    """
    if len(points) < 3:
        return None

    centered = points - points.mean(axis=0)

    # The points span a plane only if some of them are far enough from the
    # line of the X axis
    x_unit = normalize(x_direction)
    off_axis = centered - outer(centered @ x_unit, x_unit)
    if amax(norm(off_axis, axis=-1)) < _MIN_PLANE_SPREAD:
        return None

    _, _, vt = svd(centered)
    return vt[-1]
