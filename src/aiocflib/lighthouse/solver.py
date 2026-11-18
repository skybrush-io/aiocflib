"""Refinement of the poses of Lighthouse base stations and drones from measured
sweep angles.

The solver minimizes the sum of squared differences between the measured sweep
angles and the angles predicted from the estimated poses, using the
Levenberg-Marquardt method. It needs an initial guess that is reasonably close
to the solution.

Each measured angle depends on the pose of a single base station and on the
pose of the drone in a single sample. The parameters of different samples are
never coupled directly, and the normal equations of the problem are sparse.
The solver eliminates the parameters of the samples first, and solves a dense
system whose size depends only on the number of base stations. The parameters
of the samples are then recovered one sample at a time.

The measurements do not change if all the poses are moved or rotated together.
The pose of one base station, the one with the most measurements, is kept
fixed to remove this freedom. Its initial pose defines the reference frame of
the solution. A base station is a much better anchor than the pose of a drone
in a single sample: the four sensors of the deck are only a few centimetres
apart, and the orientation of the deck in a single sample is uncertain. With a
drone as the anchor, the rest of the system can rotate around it at almost no
cost, and the solver converges slowly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from numpy import (
    add,
    amax,
    arange,
    arctan2,
    argmax,
    array,
    bincount,
    broadcast_to,
    concatenate,
    diagonal,
    einsum,
    eye,
    float64,
    intp,
    maximum,
    mean,
    sqrt,
    stack,
    swapaxes,
    tile,
    zeros,
    zeros_like,
)
from numpy.linalg import inv, solve
from numpy.typing import ArrayLike, NDArray

from .arrays import to_float_array
from .model import DECK_SENSOR_POSITIONS, Pose
from .rotation import rotation_vector_to_matrix, skew

__all__ = ("GeometrySolution", "refine_geometry")


Sample = Mapping[int, ArrayLike]
"""Sweep angles measured by a drone at a single position, keyed by base station
ID. Each value is an array of shape ``(number of sensors, 2)`` that contains
the horizontal and the vertical sweep angle of each sensor, in radians.
"""


@dataclass(frozen=True)
class GeometrySolution:
    """Result of the refinement of the poses of base stations and drones."""

    base_stations: dict[int, Pose]
    """Estimated poses of the base stations, keyed by base station ID."""

    samples: list[Pose]
    """Estimated poses of the drone in each sample."""

    sample_errors: NDArray[float64]
    """Root mean square difference between the measured and the predicted sweep
    angles of each sample, in radians.
    """

    error: float
    """Root mean square difference between all the measured and predicted sweep
    angles, in radians.
    """

    reference_base_station: int
    """ID of the base station whose pose was kept fixed."""

    iterations: int
    """Number of iterations that the solver performed."""

    converged: bool
    """Whether the solver stopped because the solution stopped improving. The
    solution may still be usable if this is ``False``; check the errors.
    """


@dataclass
class _Problem:
    """Measurements arranged in flat arrays with one row for each sensor of
    each base station in each sample.
    """

    bs_ids: list[int]
    """Base station IDs in the order of their indices."""

    bs_index: NDArray[intp]
    """Index of the base station of each row."""

    sample_index: NDArray[intp]
    """Index of the sample of each row."""

    fixed_bs_index: int
    """Index of the base station whose pose is kept fixed."""

    sensor_positions: NDArray[float64]
    """Position of the sensor of each row, in the reference frame of the
    drone.
    """

    angles: NDArray[float64]
    """Measured horizontal and vertical sweep angles of each row."""

    num_samples: int


@dataclass
class _State:
    """Estimated poses of the base stations and the samples."""

    bs_rotations: NDArray[float64]
    bs_origins: NDArray[float64]
    sample_rotations: NDArray[float64]
    sample_translations: NDArray[float64]


def refine_geometry(
    samples: Sequence[Sample],
    base_stations: Mapping[int, Pose],
    sample_poses: Sequence[Pose],
    *,
    sensor_positions: ArrayLike = DECK_SENSOR_POSITIONS,
    max_iterations: int = 100,
    tolerance: float = 1e-10,
    step_tolerance: float = 1e-6,
) -> GeometrySolution:
    """Refines the poses of base stations and drones to make the predicted
    sweep angles match the measured ones as closely as possible.

    Args:
        samples: the sweep angles measured in each sample
        base_stations: initial guess for the pose of each base station that
            appears in the samples, keyed by base station ID
        sample_poses: initial guess for the pose of the drone in each sample
        sensor_positions: positions of the sensors in the reference frame of
            the drone, in metres
        max_iterations: maximum number of iterations
        tolerance: the solver stops when an iteration decreases the sum of
            squared errors by less than this fraction
        step_tolerance: the solver stops when an iteration moves no base
            station by more than this many metres and rotates none by more
            than this many radians. With noisy measurements, the last
            iterations make tiny changes that would take long to fall below
            the other tolerance. The poses of the samples are not checked: the
            orientation of the deck in a noisy sample is barely constrained,
            and it may keep changing slowly without affecting the base
            stations.

    Returns:
        the refined poses, in the reference frame defined by the initial pose
        of the base station with the most measurements

    Raises:
        ValueError: if the input is inconsistent, for instance if a sample
            refers to a base station without an initial guess
    """
    problem = _create_problem(samples, base_stations, sample_poses, sensor_positions)
    state = _State(
        bs_rotations=to_float_array(
            [base_stations[i].rotation for i in problem.bs_ids]
        ),
        bs_origins=to_float_array(
            [base_stations[i].translation for i in problem.bs_ids]
        ),
        sample_rotations=to_float_array([pose.rotation for pose in sample_poses]),
        sample_translations=to_float_array([pose.translation for pose in sample_poses]),
    )

    residuals = _residuals(problem, state)
    cost = float(residuals @ residuals)
    damping = 1e-3
    growth = 2.0
    converged = False
    iterations = 0

    # Damping is adjusted with the method of H. B. Nielsen (1999), based on how
    # well the linearized model predicted the decrease of the cost
    while iterations < max_iterations and not converged:
        iterations += 1
        bs_jac, sample_jac, residuals = _linearize(problem, state)

        while True:
            bs_step, sample_step = _solve_damped(
                problem, bs_jac, sample_jac, residuals, damping
            )
            largest_change = max(amax(abs(bs_step)), amax(abs(sample_step)))
            if largest_change < 1e-12:
                # Steps are too small to change anything
                converged = True
                break

            candidate = _apply_step(state, bs_step, sample_step)
            candidate_residuals = _residuals(problem, candidate)
            candidate_cost = float(candidate_residuals @ candidate_residuals)

            actual_decrease = cost - candidate_cost
            predicted_decrease = cost - _linearized_cost(
                problem, bs_jac, sample_jac, residuals, bs_step, sample_step
            )
            if actual_decrease > 0 and predicted_decrease > 0:
                gain = actual_decrease / predicted_decrease
                state, cost = candidate, candidate_cost
                damping *= max(1.0 / 3.0, 1.0 - (2.0 * gain - 1.0) ** 3)
                damping = max(damping, 1e-15)
                growth = 2.0
                converged = (
                    actual_decrease <= tolerance * cost
                    or float(amax(abs(bs_step))) < step_tolerance
                )
                break

            damping *= growth
            growth *= 2.0
            if damping > 1e12:
                # No step decreases the cost any more; we are at a minimum
                converged = True
                break

    return _create_solution(problem, state, iterations, converged)


def _create_problem(
    samples: Sequence[Sample],
    base_stations: Mapping[int, Pose],
    sample_poses: Sequence[Pose],
    sensor_positions: ArrayLike,
) -> _Problem:
    sensors = to_float_array(sensor_positions)
    if sensors.ndim != 2 or sensors.shape[1] != 3:
        raise ValueError("Sensor positions must be an array of shape (n, 3)")

    num_sensors = sensors.shape[0]
    if len(samples) != len(sample_poses):
        raise ValueError("There must be one initial pose for each sample")
    if not samples:
        raise ValueError("At least one sample is needed")

    bs_ids = sorted({bs_id for sample in samples for bs_id in sample})
    missing = [bs_id for bs_id in bs_ids if bs_id not in base_stations]
    if missing:
        raise ValueError(
            f"No initial guess for base stations: {', '.join(map(str, missing))}"
        )
    bs_index_by_id = {bs_id: index for index, bs_id in enumerate(bs_ids)}

    bs_index: list[int] = []
    sample_index: list[int] = []
    angles: list[NDArray[float64]] = []
    for index, sample in enumerate(samples):
        if not sample:
            raise ValueError(f"Sample {index} has no measurements")
        for bs_id, measured in sample.items():
            measured = to_float_array(measured)
            if measured.shape != (num_sensors, 2):
                raise ValueError(
                    f"Measurement of base station {bs_id} in sample {index} "
                    f"must have shape ({num_sensors}, 2)"
                )
            bs_index.extend([bs_index_by_id[bs_id]] * num_sensors)
            sample_index.extend([index] * num_sensors)
            angles.append(measured)

    num_rows = len(bs_index)
    bs_index_array = array(bs_index, dtype=intp)
    return _Problem(
        bs_ids=bs_ids,
        bs_index=bs_index_array,
        sample_index=array(sample_index, dtype=intp),
        # argmax picks the lowest index, and the lowest ID, in case of ties
        fixed_bs_index=int(argmax(bincount(bs_index_array))),
        sensor_positions=tile(sensors, (num_rows // num_sensors, 1)),
        angles=concatenate(angles),
        num_samples=len(samples),
    )


def _local_points(
    problem: _Problem, state: _State
) -> tuple[NDArray[float64], NDArray[float64]]:
    """Returns the positions of the sensors of each row in the world and in
    the reference frame of the base station of the row.
    """
    sample_rotations = state.sample_rotations[problem.sample_index]
    world = (
        einsum("nij,nj->ni", sample_rotations, problem.sensor_positions)
        + state.sample_translations[problem.sample_index]
    )
    bs_rotations = state.bs_rotations[problem.bs_index]
    local = einsum(
        "nji,nj->ni", bs_rotations, world - state.bs_origins[problem.bs_index]
    )
    return world, local


def _predict(local: NDArray[float64]) -> NDArray[float64]:
    return stack(
        (
            arctan2(local[:, 1], local[:, 0]),
            arctan2(local[:, 2], local[:, 0]),
        ),
        axis=-1,
    )


def _residuals(problem: _Problem, state: _State) -> NDArray[float64]:
    _, local = _local_points(problem, state)
    return (_predict(local) - problem.angles).ravel()


def _linearize(
    problem: _Problem, state: _State
) -> tuple[NDArray[float64], NDArray[float64], NDArray[float64]]:
    """Returns the derivatives of the residuals of each row with respect to the
    parameters of its base station and its sample, and the residuals
    themselves.

    The parameters of a pose are a small rotation vector, applied in the
    reference frame of the body after its current rotation, followed by a
    change of position in the world.

    Returns:
        the derivatives with respect to the base station parameters, of shape
        ``(rows, 2, 6)``; the derivatives with respect to the sample
        parameters, of the same shape; and the residuals of shape
        ``(2 * rows,)``
    """
    _, local = _local_points(problem, state)
    residuals = (_predict(local) - problem.angles).ravel()

    x, y, z = local[:, 0], local[:, 1], local[:, 2]
    zero = zeros_like(x)
    horizontal = stack((-y, x, zero), axis=-1) / (x * x + y * y)[:, None]
    vertical = stack((-z, zero, x), axis=-1) / (x * x + z * z)[:, None]
    angles_by_local = stack((horizontal, vertical), axis=1)

    bs_rotations_t = swapaxes(state.bs_rotations[problem.bs_index], -1, -2)
    sample_rotations = state.sample_rotations[problem.sample_index]

    # Rotating the base station by a small rotation vector e changes the local
    # point by local x e; moving it by d changes the local point by -R^T d
    local_by_bs = concatenate((skew(local), -bs_rotations_t), axis=-1)

    # Rotating the drone by a small rotation vector e moves the sensor by
    # -R_s [s]x e in the world; moving the drone by d moves the sensor by d
    world_by_sample = concatenate(
        (
            -sample_rotations @ skew(problem.sensor_positions),
            broadcast_to(eye(3), sample_rotations.shape),
        ),
        axis=-1,
    )
    local_by_sample = bs_rotations_t @ world_by_sample

    bs_jac = angles_by_local @ local_by_bs
    sample_jac = angles_by_local @ local_by_sample

    # The fixed base station defines the reference frame and is not optimized
    bs_jac[problem.bs_index == problem.fixed_bs_index] = 0.0

    return bs_jac, sample_jac, residuals


def _solve_damped(
    problem: _Problem,
    bs_jac: NDArray[float64],
    sample_jac: NDArray[float64],
    residuals: NDArray[float64],
    damping: float,
) -> tuple[NDArray[float64], NDArray[float64]]:
    """Solves the damped normal equations of one Levenberg-Marquardt step.

    Returns:
        the parameter changes of the base stations, of shape ``(B, 6)``, and
        of the samples, of shape ``(S, 6)``
    """
    num_bs = len(problem.bs_ids)
    num_samples = problem.num_samples
    r = residuals.reshape(-1, 2)

    bs_block = zeros((num_bs, 6, 6))
    sample_block = zeros((num_samples, 6, 6))
    cross_block = zeros((num_bs, num_samples, 6, 6))
    bs_gradient = zeros((num_bs, 6))
    sample_gradient = zeros((num_samples, 6))

    add.at(bs_block, problem.bs_index, einsum("nki,nkj->nij", bs_jac, bs_jac))
    add.at(
        sample_block,
        problem.sample_index,
        einsum("nki,nkj->nij", sample_jac, sample_jac),
    )
    add.at(
        cross_block,
        (problem.bs_index, problem.sample_index),
        einsum("nki,nkj->nij", bs_jac, sample_jac),
    )
    add.at(bs_gradient, problem.bs_index, einsum("nki,nk->ni", bs_jac, r))
    add.at(sample_gradient, problem.sample_index, einsum("nki,nk->ni", sample_jac, r))

    bs_block = _add_damping(bs_block, damping)
    sample_block = _add_damping(sample_block, damping)

    # The fixed base station has no free parameters; an identity block keeps
    # the system solvable and yields a zero step because its gradient and its
    # coupling to the samples are zero
    bs_block[problem.fixed_bs_index] = eye(6)

    # Eliminate the sample parameters (Schur complement)
    sample_block_inv = inv(sample_block)
    weighted_cross = einsum("bsij,sjk->bsik", cross_block, sample_block_inv)
    reduced = einsum("bsik,cslk->bcil", weighted_cross, cross_block)
    reduced = -reduced
    reduced[arange(num_bs), arange(num_bs)] += bs_block
    reduced_rhs = -bs_gradient + einsum("bsik,sk->bi", weighted_cross, sample_gradient)

    size = 6 * num_bs
    bs_step = solve(
        reduced.transpose(0, 2, 1, 3).reshape(size, size), reduced_rhs.ravel()
    ).reshape(num_bs, 6)

    sample_rhs = -sample_gradient - einsum("bsji,bj->si", cross_block, bs_step)
    sample_step = einsum("sij,sj->si", sample_block_inv, sample_rhs)
    return bs_step, sample_step


def _add_damping(blocks: NDArray[float64], damping: float) -> NDArray[float64]:
    """Adds Levenberg-Marquardt damping to the diagonal of each block, scaled
    by the diagonal itself to make the damping independent of the units of
    the parameters.
    """
    diagonals = diagonal(blocks, axis1=-2, axis2=-1)
    scale = maximum(diagonals, 1e-9 * amax(diagonals, initial=1.0))
    result = blocks.copy()
    index = arange(6)
    result[..., index, index] += damping * scale
    return result


def _linearized_cost(
    problem: _Problem,
    bs_jac: NDArray[float64],
    sample_jac: NDArray[float64],
    residuals: NDArray[float64],
    bs_step: NDArray[float64],
    sample_step: NDArray[float64],
) -> float:
    """Returns the cost after the given step, as predicted by the linearized
    model.
    """
    predicted = (
        residuals.reshape(-1, 2)
        + einsum("nki,ni->nk", bs_jac, bs_step[problem.bs_index])
        + einsum("nki,ni->nk", sample_jac, sample_step[problem.sample_index])
    )
    return float((predicted * predicted).sum())


def _apply_step(
    state: _State, bs_step: NDArray[float64], sample_step: NDArray[float64]
) -> _State:
    return _State(
        bs_rotations=state.bs_rotations @ rotation_vector_to_matrix(bs_step[:, :3]),
        bs_origins=state.bs_origins + bs_step[:, 3:],
        sample_rotations=state.sample_rotations
        @ rotation_vector_to_matrix(sample_step[:, :3]),
        sample_translations=state.sample_translations + sample_step[:, 3:],
    )


def _create_solution(
    problem: _Problem, state: _State, iterations: int, converged: bool
) -> GeometrySolution:
    residuals = _residuals(problem, state).reshape(-1, 2)
    squared = (residuals * residuals).sum(axis=-1)
    sample_sum = bincount(
        problem.sample_index, weights=squared, minlength=problem.num_samples
    )
    sample_count = bincount(problem.sample_index, minlength=problem.num_samples)
    sample_errors = sqrt(sample_sum / (2 * sample_count))

    return GeometrySolution(
        base_stations={
            bs_id: Pose(
                rotation=state.bs_rotations[index], translation=state.bs_origins[index]
            )
            for index, bs_id in enumerate(problem.bs_ids)
        },
        samples=[
            Pose(
                rotation=state.sample_rotations[index],
                translation=state.sample_translations[index],
            )
            for index in range(problem.num_samples)
        ],
        sample_errors=sample_errors,
        error=float(sqrt(mean(residuals * residuals))),
        reference_base_station=problem.bs_ids[problem.fixed_bs_index],
        iterations=iterations,
        converged=converged,
    )
