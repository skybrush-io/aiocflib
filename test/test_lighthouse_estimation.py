from lighthouse_scenes import create_scene, rotation_angle_between
from numpy import allclose
from pytest import raises

from aiocflib.lighthouse import (
    BaseStationPlacementError,
    CalibrationSamples,
    RoomAlignmentError,
    estimate_geometry,
)
from aiocflib.lighthouse.model import Pose


def scene_samples(**kwds) -> tuple[dict[int, Pose], CalibrationSamples]:
    scene = create_scene(**kwds)
    samples = CalibrationSamples(
        list(scene.samples),
        num_x_axis_samples=scene.x_axis.stop - scene.x_axis.start,
        num_xy_plane_samples=scene.xy_plane.stop - scene.xy_plane.start,
    )
    return scene.base_stations, samples


def test_estimate_geometry_in_room_coordinates():
    expected, samples = scene_samples()

    estimate = estimate_geometry(samples)

    assert estimate.converged
    assert estimate.error < 1e-8
    assert sorted(estimate.geometries) == sorted(expected)
    for bs_id, geometry in estimate.geometries.items():
        pose = Pose.from_bs_geometry(geometry)
        assert geometry.valid
        assert allclose(pose.translation, expected[bs_id].translation, atol=1e-6)
        assert rotation_angle_between(pose.rotation, expected[bs_id].rotation) < 1e-6


def test_estimate_geometry_with_noise():
    expected, samples = scene_samples(noise=1e-4, num_free_samples=100)

    estimate = estimate_geometry(samples)

    for bs_id, geometry in estimate.geometries.items():
        pose = Pose.from_bs_geometry(geometry)
        assert allclose(pose.translation, expected[bs_id].translation, atol=0.02)
        assert rotation_angle_between(pose.rotation, expected[bs_id].rotation) < 0.01


def test_estimate_geometry_without_xy_plane_samples():
    expected, samples = scene_samples()
    del samples.samples[3:5]
    samples.num_xy_plane_samples = 0

    estimate = estimate_geometry(samples)

    pose = Pose.from_bs_geometry(estimate.geometries[0])
    assert allclose(pose.translation, expected[0].translation, atol=1e-6)


def test_estimate_geometry_rejects_inconsistent_counts():
    _, samples = scene_samples(num_free_samples=0)

    samples.num_x_axis_samples = 0
    with raises(ValueError, match="X axis"):
        estimate_geometry(samples)

    samples.num_x_axis_samples = 2
    samples.num_xy_plane_samples = 3
    with raises(ValueError, match="Expected at least 6 samples, got 5"):
        estimate_geometry(samples)


def test_estimate_geometry_reports_unplaced_base_stations():
    _, samples = scene_samples()
    # Base station 3 appears in a single sample only
    samples.samples[1:] = [
        {bs_id: angles for bs_id, angles in sample.items() if bs_id != 3}
        for sample in samples.samples[1:]
    ]

    with raises(BaseStationPlacementError) as info:
        estimate_geometry(samples)
    assert info.value.base_stations == [3]
    assert isinstance(info.value, ValueError)


def test_estimate_geometry_reports_alignment_failure():
    _, samples = scene_samples()
    # The sample "on the X axis" is taken at the origin
    samples.samples[1] = samples.samples[0]
    samples.samples[2] = samples.samples[0]

    with raises(RoomAlignmentError, match="must differ from the origin"):
        estimate_geometry(samples)
