"""Collection of samples of sweep angles from a Crazyflie drone for the
estimation of the geometry of the base stations.

There are two ways to collect samples:

- `collect_sample()` collects the measurements of a stationary drone for a
  while and averages them. Use it for the samples that define the coordinate
  system of the room: at the origin, on the X axis and in the XY plane.

- `stream_matched_samples()` yields a sample for every group of measurements
  that several base stations made at the same time. Use it while moving the
  drone around the room, to cover the space seen by the base stations.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Iterable
from contextlib import asynccontextmanager

from anyio import move_on_after
from numpy import count_nonzero, float64, maximum, median
from numpy.typing import NDArray

from aiocflib.crazyflie.lighthouse import (
    Lighthouse,
    LighthouseSweepAngles,
    group_matched_sweep_angles,
)

from .arrays import to_float_array

__all__ = (
    "average_sweep_angles",
    "collect_sample",
    "stream_matched_samples",
)


_MIN_DEVIATION = 1e-5
"""Lower limit of the estimated standard deviation of the sweep angles, in
radians. Prevents rejecting measurements when the angles barely change.
"""


def average_sweep_angles(
    measurements: Iterable[LighthouseSweepAngles],
    *,
    min_measurements: int = 1,
    threshold: float = 4.0,
) -> dict[int, NDArray[float64]]:
    """Averages the sweep angles measured for each base station, ignoring
    outliers.

    The median and the median absolute deviation of each angle give a robust
    estimate of its value and standard deviation. A measurement is an outlier
    if any of its angles deviates from the median by more than the given
    number of standard deviations. Outliers come from reflections, and from
    sensors that missed a sweep: the firmware repeats their previous angle.

    Parameters:
        measurements: the measurements to average
        min_measurements: base stations with fewer measurements than this,
            after removing the outliers, are left out from the result
        threshold: measurements farther from the median than this many
            standard deviations are outliers

    Returns:
        the average sweep angles of each sensor for each base station, as
        arrays of shape ``(number of sensors, 2)``, keyed by base station index
    """
    by_base_station: dict[int, list[tuple[tuple[float, float], ...]]] = {}
    for measurement in measurements:
        by_base_station.setdefault(measurement.base_station, []).append(
            measurement.angles
        )

    result: dict[int, NDArray[float64]] = {}
    for bs_id, angles in sorted(by_base_station.items()):
        values = to_float_array(angles)
        medians = median(values, axis=0)
        deviation = maximum(
            1.4826 * median(abs(values - medians), axis=0), _MIN_DEVIATION
        )
        inliers = (abs(values - medians) <= threshold * deviation).all(axis=(1, 2))
        if count_nonzero(inliers) >= min_measurements:
            result[bs_id] = values[inliers].mean(axis=0)

    return result


async def collect_sample(
    lighthouse: Lighthouse,
    *,
    duration: float = 1.0,
    min_measurements: int = 10,
) -> dict[int, NDArray[float64]]:
    """Collects the sweep angles measured by a stationary drone for a given
    time and averages them.

    Parameters:
        lighthouse: the Lighthouse subsystem of the drone
        duration: how long to collect the measurements, in seconds
        min_measurements: base stations with fewer measurements than this are
            left out from the sample

    Returns:
        the average sweep angles of each sensor for each base station, as
        arrays of shape ``(number of sensors, 2)``, keyed by base station index.
        The result is empty if the drone sees no base station.
    """
    measurements: list[LighthouseSweepAngles] = []
    async with lighthouse.stream_sweep_angles() as stream:
        with move_on_after(duration):
            async for measurement in stream:
                measurements.append(measurement)

    return average_sweep_angles(measurements, min_measurements=min_measurements)


@asynccontextmanager
async def stream_matched_samples(
    lighthouse: Lighthouse,
    *,
    min_base_stations: int = 2,
    max_time_ms: int = 25,
) -> AsyncGenerator[AsyncIterator[dict[int, NDArray[float64]]]]:
    """Asynchronous context manager that yields a sample for every group of
    measurements that several base stations made at the same time, while the
    context is active.

    The samples are single measurements, not averages. They are noisier than
    the samples of `collect_sample()`, but they can be taken while the drone
    is moving.

    Parameters:
        lighthouse: the Lighthouse subsystem of the drone
        min_base_stations: minimum number of base stations in a sample; at
            most 4 base stations fit in a sample
        max_time_ms: maximum time between the first and the last measurement
            of a sample, in milliseconds

    Yields:
        an asynchronous iterator of samples. Each sample contains the sweep
        angles of each sensor for each base station, as arrays of shape
        ``(number of sensors, 2)``, keyed by base station index.
    """
    async with lighthouse.stream_sweep_angles(
        matched=True, min_base_stations=min_base_stations, max_time_ms=max_time_ms
    ) as stream:
        yield _to_samples(group_matched_sweep_angles(stream))


async def _to_samples(
    groups: AsyncIterable[dict[int, LighthouseSweepAngles]],
) -> AsyncIterator[dict[int, NDArray[float64]]]:
    async for group in groups:
        yield {
            bs_id: to_float_array(measurement.angles)
            for bs_id, measurement in sorted(group.items())
        }
