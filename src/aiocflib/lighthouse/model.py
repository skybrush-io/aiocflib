"""Measurement model of the Lighthouse positioning system.

The conventions follow the Crazyflie firmware. The X axis of a base station
points forward, out of its front panel. A point at ``(x, y, z)`` in the
reference frame of the base station is seen at a horizontal sweep angle of
``atan2(y, x)`` and a vertical sweep angle of ``atan2(z, x)``. These are the
angles that the firmware reports for both Lighthouse V1 and V2 base stations,
after applying the calibration data of the base station.
"""

from __future__ import annotations

from dataclasses import dataclass

from numpy import arctan2, array, eye, float64, stack, zeros
from numpy.typing import ArrayLike, NDArray

from aiocflib.crazyflie.lighthouse import LighthouseBsGeometry

from .arrays import to_float_array, to_float_array_or_default
from .rotation import orthonormalize

__all__ = (
    "DECK_SENSOR_POSITIONS",
    "Pose",
    "predict_sweep_angles",
)


DECK_SENSOR_POSITIONS: NDArray[float64] = array(
    [
        [-0.015, 0.0075, 0.0],
        [-0.015, -0.0075, 0.0],
        [0.015, 0.0075, 0.0],
        [0.015, -0.0075, 0.0],
    ]
)
"""Positions of the four sensors of the Lighthouse deck, in metres, in the
reference frame of the drone. The order matches the order of the sensors in
the sweep angle measurements of the firmware.
"""


@dataclass(frozen=True)
class Pose:
    """Position and orientation of a rigid body: a base station or a drone.

    The rotation matrix maps directions from the reference frame of the body to
    the reference frame of the world, and the translation is the position of
    the body in the world. A point ``p`` in the reference frame of the body is
    at ``rotation @ p + translation`` in the world.
    """

    rotation: NDArray[float64]
    """Rotation matrix of shape ``(3, 3)``."""

    translation: NDArray[float64]
    """Translation vector of shape ``(3,)``."""

    @classmethod
    def create(
        cls, rotation: ArrayLike | None = None, translation: ArrayLike | None = None
    ) -> Pose:
        """Creates a pose from a rotation matrix and a translation vector.

        Args:
            rotation: the rotation matrix; ``None`` means no rotation
            translation: the translation vector; ``None`` means no translation
        """
        return cls(
            rotation=to_float_array_or_default(rotation, eye(3), (3, 3)),
            translation=to_float_array_or_default(translation, zeros(3), (3,)),
        )

    @classmethod
    def from_bs_geometry(cls, geometry: LighthouseBsGeometry) -> Pose:
        """Creates a pose from the geometry of a base station, as stored by
        the Crazyflie firmware.
        """
        return cls.create(geometry.rotation_matrix, geometry.origin)

    def apply(self, points: ArrayLike) -> NDArray[float64]:
        """Transforms points from the reference frame of the body to the
        reference frame of the world.

        Args:
            points: array of shape ``(..., 3)``
        """
        return to_float_array(points) @ self.rotation.T + self.translation

    def compose(self, other: Pose) -> Pose:
        """Returns the pose that first applies the other pose and then this
        one.
        """
        return Pose(
            rotation=self.rotation @ other.rotation,
            translation=self.rotation @ other.translation + self.translation,
        )

    def inverse(self) -> Pose:
        """Returns the inverse of this pose."""
        rotation_t = self.rotation.T
        return Pose(rotation=rotation_t, translation=-(rotation_t @ self.translation))

    def to_bs_geometry(self) -> LighthouseBsGeometry:
        """Converts the pose of a base station to a geometry that can be
        stored on a Crazyflie drone.
        """
        rotation = orthonormalize(self.rotation)
        return LighthouseBsGeometry(
            origin=(
                float(self.translation[0]),
                float(self.translation[1]),
                float(self.translation[2]),
            ),
            rotation_matrix=(
                (float(rotation[0, 0]), float(rotation[0, 1]), float(rotation[0, 2])),
                (float(rotation[1, 0]), float(rotation[1, 1]), float(rotation[1, 2])),
                (float(rotation[2, 0]), float(rotation[2, 1]), float(rotation[2, 2])),
            ),
            valid=True,
        )


def predict_sweep_angles(base_station: Pose, points: ArrayLike) -> NDArray[float64]:
    """Returns the sweep angles at which a base station sees the given points.

    Args:
        base_station: the pose of the base station
        points: array of shape ``(..., 3)``, the positions of the points in the
            world

    Returns:
        array of shape ``(..., 2)`` containing the horizontal and the vertical
        sweep angle of each point, in radians
    """
    local = (to_float_array(points) - base_station.translation) @ (
        base_station.rotation
    )
    return stack(
        (
            arctan2(local[..., 1], local[..., 0]),
            arctan2(local[..., 2], local[..., 0]),
        ),
        axis=-1,
    )
