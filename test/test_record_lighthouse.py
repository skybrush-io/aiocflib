"""Tests for the Lighthouse recording script, using simulated recordings."""

import re
from pathlib import Path

from lighthouse_scenes import create_scene
from numpy import array, ndarray
from numpy.random import default_rng
from pytest import CaptureFixture

from aiocflib.benchmarks.record_lighthouse import Recording, main
from aiocflib.crazyflie.lighthouse import LighthouseSweepAngles
from aiocflib.lighthouse.simulation import simulate_sample


def to_measurement(
    bs_id: int, angles: ndarray, group: int | None = None, group_size: int = 1
) -> LighthouseSweepAngles:
    return LighthouseSweepAngles(
        base_station=bs_id,
        angles=tuple((float(h), float(v)) for h, v in angles.tolist()),
        group=group,
        group_size=group_size,
    )


def write_session(path: Path) -> None:
    """Writes a simulated calibration session with the noise of Lighthouse V2."""
    scene = create_scene(num_free_samples=0, seed=7)
    rng = default_rng(1)
    recording = Recording(path)
    recording.set_metadata(
        "geometries",
        {
            str(bs_id): pose.to_bs_geometry().to_json()
            for bs_id, pose in scene.base_stations.items()
        },
    )
    recording.set_metadata("system_type", "V2")

    phases = ["origin", "x_axis_0", "x_axis_1", "xy_plane_0", "xy_plane_1"]
    for phase, drone in zip(phases, scene.drones):
        for _ in range(50):
            sample = simulate_sample(scene.base_stations, drone, noise=1e-3, rng=rng)
            for bs_id, angles in sample.items():
                recording.add(phase, to_measurement(bs_id, angles))

    free = create_scene(noise=1e-3, num_free_samples=200, seed=7).samples[5:]
    group = 0
    for sample in free:
        if not 2 <= len(sample) <= 4:
            continue
        for bs_id, angles in sample.items():
            recording.add("free", to_measurement(bs_id, angles, group, len(sample)))
        group = (group + 1) % 16

    recording.close()


def test_analyze_simulated_session(tmp_path: Path, capsys: CaptureFixture[str]):
    path = tmp_path / "session.jsonl"
    write_session(path)

    assert main(["analyze", str(path)]) == 0
    output = capsys.readouterr().out

    assert "Lighthouse system type: V2" in output
    assert "Noise of single measurements (origin" in output
    assert re.search(r"Matched stream \(free.*\n  \d+ complete groups", output)
    assert "converged: True" in output

    # Every base station is compared with the stored geometry
    distances = [float(d) for d in re.findall(r"([\d.]+) cm", output)]
    angles = [float(a) for a in re.findall(r"([\d.]+) deg", output)]
    assert len(distances) == len(angles) == 4
    assert max(distances) < 5.0
    assert max(angles) < 0.5
    assert '"geos"' in output


def test_recording_round_trip(tmp_path: Path):
    path = tmp_path / "recording.jsonl"
    recording = Recording(path)
    recording.set_metadata("system_type", "V1")
    measurement = to_measurement(3, array([[0.1, 0.2]] * 4), group=5, group_size=2)
    recording.add("free", measurement)
    recording.close()

    loaded = Recording.load(path)

    assert loaded.metadata == {"system_type": "V1"}
    assert loaded.phases() == ["free"]
    assert loaded.measurements("free") == [measurement]
