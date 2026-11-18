"""Script that records Lighthouse sweep angles from a Crazyflie drone and
evaluates the geometry estimation on them.

Needs the ``lighthouse`` extra of aiocflib. The recordings are JSON Lines
files: one measurement per line, with a timestamp, the phase of the recording
and the parsed sweep angles. The ``analyze`` command evaluates a recording
again without a drone.

Commands:

- ``noise``: the drone must stay still in view of the base stations. Records
  the regular sweep angle stream and reports the noise of single measurements.

- ``matched``: move the drone around while it records the matched sweep angle
  stream. Reports how many groups arrive and how many are lost.

- ``session``: a full calibration session. Asks you to place the drone at the
  origin, on the X axis and in the XY plane, then to move it around. Estimates
  the geometry of the base stations and compares it with the geometry stored
  on the drone.

- ``analyze``: evaluates a recording made by one of the commands above.

Example::

    python -m aiocflib.benchmarks.record_lighthouse radio://0/80/2M/E7E7E7E701 \\
        noise --duration 10 --output noise.jsonl
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any, TextIO

from anyio import move_on_after, run, to_thread
from numpy import amax, arccos, clip, degrees, median, std, trace
from numpy.linalg import norm

from aiocflib.crazyflie import Crazyflie
from aiocflib.crazyflie.lighthouse import (
    LighthouseBsGeometry,
    LighthouseSweepAngles,
    group_matched_sweep_angles,
)
from aiocflib.crtp.drivers import init_drivers
from aiocflib.lighthouse.alignment import find_room_transform
from aiocflib.lighthouse.arrays import to_float_array
from aiocflib.lighthouse.initial_guess import estimate_initial_guess
from aiocflib.lighthouse.model import Pose
from aiocflib.lighthouse.sampling import average_sweep_angles
from aiocflib.lighthouse.solver import refine_geometry

MAX_BASE_STATIONS = 16
"""Number of base station slots to read from the drone."""


@dataclass(frozen=True)
class Record:
    """A single recorded measurement."""

    timestamp: float
    phase: str
    measurement: LighthouseSweepAngles

    def to_json(self) -> dict[str, Any]:
        m = self.measurement
        return {
            "t": self.timestamp,
            "phase": self.phase,
            "bs": m.base_station,
            "angles": [list(pair) for pair in m.angles],
            "group": m.group,
            "group_size": m.group_size,
        }

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> Record:
        return cls(
            timestamp=obj["t"],
            phase=obj["phase"],
            measurement=LighthouseSweepAngles(
                base_station=obj["bs"],
                angles=tuple((float(h), float(v)) for h, v in obj["angles"]),
                group=obj["group"],
                group_size=obj["group_size"],
            ),
        )


class Recording:
    """Measurements and metadata recorded from a drone, written to a JSON Lines
    file as they arrive.
    """

    def __init__(self, path: Path | None):
        self.records: list[Record] = []
        self.metadata: dict[str, Any] = {}
        self._file: TextIO | None = path.open("w") if path else None

    def add(self, phase: str, measurement: LighthouseSweepAngles) -> None:
        record = Record(time(), phase, measurement)
        self.records.append(record)
        self._write(record.to_json())

    def set_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value
        self._write({"metadata": {key: value}})

    def close(self) -> None:
        if self._file:
            self._file.close()

    def _write(self, obj: dict[str, Any]) -> None:
        if self._file:
            self._file.write(json.dumps(obj) + "\n")
            self._file.flush()

    @classmethod
    def load(cls, path: Path) -> Recording:
        recording = cls(None)
        with path.open() as fp:
            for line in fp:
                obj = json.loads(line)
                if "metadata" in obj:
                    recording.metadata.update(obj["metadata"])
                else:
                    recording.records.append(Record.from_json(obj))
        return recording

    def phases(self) -> list[str]:
        """Returns the phases of the recording, in the order of appearance."""
        return list(dict.fromkeys(record.phase for record in self.records))

    def measurements(self, phase: str) -> list[LighthouseSweepAngles]:
        return [r.measurement for r in self.records if r.phase == phase]

    def duration(self, phase: str) -> float:
        timestamps = [r.timestamp for r in self.records if r.phase == phase]
        return max(timestamps) - min(timestamps) if len(timestamps) > 1 else 0.0


# Recording ------------------------------------------------------------------


async def record_stream(
    cf: Crazyflie,
    recording: Recording,
    phase: str,
    duration: float,
    *,
    matched: bool = False,
    min_base_stations: int = 2,
) -> None:
    """Records the regular or the matched sweep angle stream for the given
    number of seconds.
    """
    async with cf.lighthouse.stream_sweep_angles(
        matched=matched, min_base_stations=min_base_stations
    ) as stream:
        with move_on_after(duration):
            async for measurement in stream:
                recording.add(phase, measurement)


async def record_drone_info(cf: Crazyflie, recording: Recording) -> None:
    """Saves the geometry stored on the drone and the Lighthouse system type
    into the recording.
    """
    cf.lighthouse.number_of_base_stations = MAX_BASE_STATIONS
    geometries = await cf.lighthouse.get_geometries()
    recording.set_metadata(
        "geometries",
        {
            str(bs_id): geometry.to_json()
            for bs_id, geometry in geometries.items()
            if geometry.valid
        },
    )
    system_type = await cf.lighthouse.get_system_type()
    recording.set_metadata("system_type", system_type.name)


async def prompt(message: str) -> None:
    await to_thread.run_sync(input, f"\n{message} Press Enter when ready... ")


async def run_noise(cf: Crazyflie, recording: Recording, args: Namespace) -> None:
    await record_drone_info(cf, recording)
    print(f"Recording the regular stream for {args.duration} s; keep the drone still")
    await record_stream(cf, recording, "noise", args.duration)


async def run_matched(cf: Crazyflie, recording: Recording, args: Namespace) -> None:
    await record_drone_info(cf, recording)
    print(f"Recording the matched stream for {args.duration} s; move the drone around")
    await record_stream(
        cf,
        recording,
        "free",
        args.duration,
        matched=True,
        min_base_stations=args.min_base_stations,
    )


async def run_session(cf: Crazyflie, recording: Recording, args: Namespace) -> None:
    await record_drone_info(cf, recording)

    steps = [("origin", "Place the drone at the origin, standing on the floor.")]
    steps += [
        (f"x_axis_{i}", f"Place the drone on the positive X axis (point {i + 1}).")
        for i in range(args.x_axis)
    ]
    steps += [
        (
            f"xy_plane_{i}",
            f"Place the drone on the floor, away from the X axis (point {i + 1}).",
        )
        for i in range(args.xy_plane)
    ]

    for phase, message in steps:
        await prompt(message)
        await record_stream(cf, recording, phase, args.static_duration)
        report_static_sample(phase, recording.measurements(phase))

    await prompt(
        f"Move the drone around the room for {args.duration} s, "
        "covering as much of the space as possible."
    )
    await record_stream(
        cf,
        recording,
        "free",
        args.duration,
        matched=True,
        min_base_stations=args.min_base_stations,
    )


# Analysis -------------------------------------------------------------------


def report_static_sample(
    phase: str, measurements: Sequence[LighthouseSweepAngles]
) -> None:
    counts: dict[int, int] = {}
    for m in measurements:
        counts[m.base_station] = counts.get(m.base_station, 0) + 1
    summary = ", ".join(f"BS {bs}: {n}" for bs, n in sorted(counts.items()))
    print(f"  {phase}: {summary or 'no base stations seen!'}")


def analyze_noise(recording: Recording, phase: str) -> None:
    measurements = recording.measurements(phase)
    duration = recording.duration(phase)
    print(f"\nNoise of single measurements ({phase}, {duration:.1f} s)")
    if not measurements:
        print("  no measurements")
        return

    by_bs: dict[int, list[Any]] = {}
    for m in measurements:
        by_bs.setdefault(m.base_station, []).append(m.angles)

    for bs_id, angles in sorted(by_bs.items()):
        values = to_float_array(angles)
        medians = median(values, axis=0)
        stds = std(values, axis=0)
        robust_std = 1.4826 * median(abs(values - medians), axis=0)
        rate = len(values) / duration if duration > 0 else float("nan")
        print(
            f"  BS {bs_id}: {len(values)} measurements ({rate:.1f}/s), "
            f"std {median(stds):.2e} rad, robust std {median(robust_std):.2e} "
            f"rad, worst sensor robust std {amax(robust_std):.2e} rad"
        )

    average = average_sweep_angles(measurements)
    print("  base stations in the averaged sample:", sorted(average))


def matched_groups(
    measurements: Iterable[LighthouseSweepAngles],
) -> list[dict[int, LighthouseSweepAngles]]:
    """Collects the measurements of the matched stream into complete groups."""

    async def source() -> AsyncIterator[LighthouseSweepAngles]:
        for measurement in measurements:
            yield measurement

    async def collect() -> list[dict[int, LighthouseSweepAngles]]:
        return [group async for group in group_matched_sweep_angles(source())]

    return run(collect)


def analyze_matched(recording: Recording, phase: str) -> None:
    measurements = recording.measurements(phase)
    duration = recording.duration(phase)
    print(f"\nMatched stream ({phase}, {duration:.1f} s)")
    if not measurements:
        print("  no measurements")
        return

    groups = matched_groups(measurements)
    started = 1
    for previous, current in zip(measurements, measurements[1:]):
        if current.group != previous.group:
            started += 1
    sizes: dict[int, int] = {}
    for group in groups:
        sizes[len(group)] = sizes.get(len(group), 0) + 1

    rate = len(groups) / duration if duration > 0 else float("nan")
    print(
        f"  {len(groups)} complete groups ({rate:.1f}/s), "
        f"{started - len(groups)} incomplete"
    )
    print(
        "  base stations per group: "
        + ", ".join(f"{size}: {count}" for size, count in sorted(sizes.items()))
    )


def analyze_session(recording: Recording) -> None:
    phases = recording.phases()
    x_axis = [p for p in phases if p.startswith("x_axis_")]
    xy_plane = [p for p in phases if p.startswith("xy_plane_")]
    if "origin" not in phases or not x_axis or "free" not in phases:
        print("\nNot a calibration session; skipping the geometry estimation")
        return

    static_phases = ["origin", *x_axis, *xy_plane]
    samples: list[dict[int, Any]] = []
    for phase in static_phases:
        sample = average_sweep_angles(
            recording.measurements(phase), min_measurements=10
        )
        if not sample:
            print(f"\nNo base station seen in phase {phase}; cannot continue")
            return
        samples.append(sample)

    groups = matched_groups(recording.measurements("free"))
    samples += [
        {bs_id: to_float_array(m.angles) for bs_id, m in group.items()}
        for group in groups
    ]
    print(
        f"\nEstimating the geometry from {len(static_phases)} static and "
        f"{len(groups)} matched samples"
    )

    guess = estimate_initial_guess(samples)
    solution = refine_geometry(samples, guess.base_stations, guess.samples)
    num_x = len(x_axis)
    transform = find_room_transform(
        solution.samples[0],
        [pose.translation for pose in solution.samples[1 : 1 + num_x]],
        [pose.translation for pose in solution.samples[1 + num_x : len(static_phases)]],
    )
    print(
        f"  solver: {solution.iterations} iterations, converged: "
        f"{solution.converged}, RMS error {solution.error:.2e} rad, "
        f"worst sample {amax(solution.sample_errors):.2e} rad"
    )

    estimated = {
        bs_id: transform.compose(pose) for bs_id, pose in solution.base_stations.items()
    }
    stored = {
        int(bs_id): Pose.from_bs_geometry(
            LighthouseBsGeometry(
                origin=tuple(geo["origin"]),  # type: ignore[arg-type]
                rotation_matrix=tuple(map(tuple, geo["rotation"])),  # type: ignore[arg-type]
                valid=True,
            )
        )
        for bs_id, geo in recording.metadata.get("geometries", {}).items()
    }

    print(
        f"\n  {'Base station':>12}   {'estimated position (m)':<24}   "
        f"{'stored position (m)':<24}   {'distance':>8}   {'angle':>9}"
    )
    for bs_id, pose in sorted(estimated.items()):
        line = f"  {bs_id:12d}   {format_vector(pose.translation)}"
        if bs_id in stored:
            other = stored[bs_id]
            distance = norm(pose.translation - other.translation)
            angle = rotation_angle(pose.rotation, other.rotation)
            line += (
                f"   {format_vector(other.translation)}   {100 * distance:5.1f} cm"
                f"   {degrees(angle):5.2f} deg"
            )
        print(line)

    geometry = {
        str(bs_id): pose.to_bs_geometry().to_json()
        for bs_id, pose in sorted(estimated.items())
    }
    print("\n  Estimated geometry in the format of the Crazyflie client:")
    print(json.dumps({"geos": geometry}, indent=2))


def format_vector(vector: Any) -> str:
    return "(" + ", ".join(f"{value:6.3f}" for value in vector) + ")"


def rotation_angle(a: Any, b: Any) -> float:
    cos_angle = (trace(a.T @ b) - 1.0) / 2.0
    return float(arccos(clip(cos_angle, -1.0, 1.0)))


def analyze(recording: Recording) -> None:
    if system_type := recording.metadata.get("system_type"):
        print(f"Lighthouse system type: {system_type}")
    phases = recording.phases()
    for phase in phases:
        if phase == "free":
            analyze_matched(recording, phase)
        else:
            analyze_noise(recording, phase)
    analyze_session(recording)


# Command line ---------------------------------------------------------------


def create_parser() -> ArgumentParser:
    parser = ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def add_drone_arguments(subparser: ArgumentParser) -> None:
        subparser.add_argument("uri", help="URI of the Crazyflie")
        subparser.add_argument(
            "--output",
            "-o",
            type=Path,
            help="file to save the recording to (JSON Lines)",
        )
        subparser.add_argument(
            "--cache",
            default="/tmp/cfcache",
            help="folder of the parameter and log TOC cache",
        )

    noise = commands.add_parser("noise", help="measure the noise of a still drone")
    add_drone_arguments(noise)
    noise.add_argument("--duration", type=float, default=10.0)

    matched = commands.add_parser(
        "matched", help="record the matched stream of a moving drone"
    )
    add_drone_arguments(matched)
    matched.add_argument("--duration", type=float, default=30.0)
    matched.add_argument("--min-base-stations", type=int, default=2)

    session = commands.add_parser("session", help="record a calibration session")
    add_drone_arguments(session)
    session.add_argument("--x-axis", type=int, default=2, help="points on the X axis")
    session.add_argument(
        "--xy-plane", type=int, default=2, help="points in the XY plane"
    )
    session.add_argument(
        "--static-duration",
        type=float,
        default=2.0,
        help="seconds to record at each static point",
    )
    session.add_argument(
        "--duration", type=float, default=90.0, help="seconds of free movement"
    )
    session.add_argument("--min-base-stations", type=int, default=2)

    analyze_parser = commands.add_parser("analyze", help="evaluate a recording")
    analyze_parser.add_argument("file", type=Path)

    return parser


async def record(args: Namespace) -> Recording:
    init_drivers()
    recording = Recording(args.output)
    try:
        async with Crazyflie(args.uri, cache=args.cache) as cf:
            if args.command == "noise":
                await run_noise(cf, recording, args)
            elif args.command == "matched":
                await run_matched(cf, recording, args)
            else:
                await run_session(cf, recording, args)
    finally:
        recording.close()
    return recording


def main(argv: Sequence[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    if args.command == "analyze":
        recording = Recording.load(args.file)
    else:
        recording = run(record, args)
        if args.output:
            print(f"\nRecording saved to {args.output}")
    analyze(recording)
    return 0


if __name__ == "__main__":
    sys.exit(main())
