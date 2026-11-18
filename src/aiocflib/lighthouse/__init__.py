"""Estimation of the geometry of Lighthouse base stations from the sweep angles
measured by Crazyflie drones.

The modules in this package need numpy. Install aiocflib with the
``lighthouse`` extra to use them.

Typical use:

1. Collect the samples of a calibration with `collect_sample()` at the origin,
   on the X axis and in the XY plane, and with `stream_matched_samples()`
   while the drone is moving around.

2. Store them in a `CalibrationSamples` object and pass it to
   `estimate_geometry()`.

Internally, the estimation works in the following steps:

1. The drones measure the sweep angles of their Lighthouse sensors at several
   positions. Each set of angles measured at the same time is a *sample*.

2. The functions in `aiocflib.lighthouse.initial_guess` estimate the poses of
   the base stations and the drone in each sample from the angles alone.

3. The solver in `aiocflib.lighthouse.solver` refines the initial guess by
   minimizing the difference between the measured angles and the angles
   predicted from the estimated poses. The result is expressed in the
   reference frame of the initial guess, anchored to one of the base stations.

4. The functions in `aiocflib.lighthouse.alignment` transform the result into
   the coordinate system of the room, using samples taken at the origin, on
   the X axis and in the XY plane.
"""

from .errors import (
    BaseStationPlacementError,
    GeometryEstimationError,
    RoomAlignmentError,
)
from .estimation import CalibrationSamples, GeometryEstimate, estimate_geometry
from .sampling import collect_sample, stream_matched_samples
from .simulation import (
    get_pose_from_position_and_angles,
    get_pose_from_position_and_target,
    simulate_sample,
)
from .solver import Sample

__all__ = (
    "BaseStationPlacementError",
    "CalibrationSamples",
    "GeometryEstimate",
    "GeometryEstimationError",
    "RoomAlignmentError",
    "Sample",
    "collect_sample",
    "estimate_geometry",
    "get_pose_from_position_and_angles",
    "get_pose_from_position_and_target",
    "simulate_sample",
    "stream_matched_samples",
)
