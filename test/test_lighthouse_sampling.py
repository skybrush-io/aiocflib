"""Tests for the sweep angle streams of the Lighthouse subsystem and the
collection of samples from them.
"""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from struct import Struct
from types import SimpleNamespace
from typing import Any

from anyio import (
    create_memory_object_stream,
    create_task_group,
    fail_after,
    move_on_after,
    sleep,
)
from anyio.streams.memory import MemoryObjectSendStream
from lighthouse_scenes import create_scene, rotation_angle_between
from numpy import allclose, array, float32, ndarray, radians
from numpy.linalg import norm
from numpy.random import default_rng
from numpy.typing import ArrayLike
from pytest import fixture, mark, raises
from trio.testing import MockClock

from aiocflib.crazyflie.lighthouse import (
    Lighthouse,
    LighthouseSweepAngles,
    group_matched_sweep_angles,
)
from aiocflib.crtp import CRTPPort
from aiocflib.lighthouse.alignment import find_room_transform
from aiocflib.lighthouse.arrays import to_float_array
from aiocflib.lighthouse.initial_guess import estimate_initial_guess
from aiocflib.lighthouse.sampling import (
    average_sweep_angles,
    collect_sample,
    stream_matched_samples,
)
from aiocflib.lighthouse.simulation import simulate_sample
from aiocflib.lighthouse.solver import refine_geometry

pytestmark = mark.anyio


@fixture
def anyio_backend():
    return ("trio", {"clock": MockClock(autojump_threshold=0)})


GENERIC_CHANNEL = 1
REGULAR = Struct("<BBfeeefeee")
MATCHED = Struct("<BBfeeefeeeB")


def encode(
    base_station: int,
    angles: ArrayLike,
    *,
    group: int | None = None,
    group_size: int = 1,
) -> bytes:
    """Encodes sweep angles the same way as the firmware."""
    values = to_float_array(angles, (-1, 2))
    h0, v0 = float(values[0, 0]), float(values[0, 1])
    h_diffs = (h0 - values[1:, 0]).tolist()
    v_diffs = (v0 - values[1:, 1]).tolist()
    if group is None:
        return REGULAR.pack(10, base_station, h0, *h_diffs, v0, *v_diffs)
    return MATCHED.pack(
        12, base_station, h0, *h_diffs, v0, *v_diffs, (group << 4) | group_size
    )


def packet(data: bytes, channel: int = GENERIC_CHANNEL) -> Any:
    return SimpleNamespace(channel=channel, data=data)


class FakeCrazyflie:
    """Fake Crazyflie that records the parameters set on it and lets the test
    send packets on the localization port while a queue is open.
    """

    def __init__(self):
        self.events: list[Any] = []
        self.sender: MemoryObjectSendStream[Any] | None = None
        self.param = SimpleNamespace(set=self._set_param)
        self.dispatcher = SimpleNamespace(create_packet_queue=self._create_queue)

    async def _set_param(self, name: str, value: int) -> None:
        # Setting a parameter involves communication with the drone
        await sleep(0)
        self.events.append((name, value))

    @asynccontextmanager
    async def _create_queue(self, port, queue_size: int = 0) -> AsyncGenerator[Any]:
        assert port == CRTPPort.LOCALIZATION
        assert queue_size > 0
        tx, rx = create_memory_object_stream[Any](queue_size)
        self.events.append("queue opened")
        self.sender = tx
        async with tx, rx:
            yield rx
        self.sender = None
        self.events.append("queue closed")

    async def send(self, data: bytes, channel: int = GENERIC_CHANNEL) -> None:
        assert self.sender is not None
        await self.sender.send(packet(data, channel))


def to_angle_tuples(angles: ndarray) -> tuple[tuple[float, float], ...]:
    return tuple((float(h), float(v)) for h, v in angles.tolist())


def angles_of(value: float, num_sensors: int = 4) -> list[tuple[float, float]]:
    return [(value + 0.01 * i, -value - 0.01 * i) for i in range(num_sensors)]


def test_parse_regular_packet():
    angles = [(0.5, -0.2), (0.49, -0.19), (0.48, -0.2), (0.47, -0.21)]
    parsed = LighthouseSweepAngles.from_packet_data(encode(3, angles))

    assert parsed.base_station == 3
    assert parsed.group is None
    assert parsed.group_size == 1
    # The differences are sent in half precision
    assert allclose(parsed.angles, angles, atol=1e-5)
    assert parsed.angles[0] == (0.5, float32(-0.2))


def test_parse_matched_packet():
    parsed = LighthouseSweepAngles.from_packet_data(
        encode(1, angles_of(0.1), group=13, group_size=3)
    )
    assert parsed.base_station == 1
    assert parsed.group == 13
    assert parsed.group_size == 3


def test_parse_invalid_packets():
    with raises(ValueError, match="empty"):
        LighthouseSweepAngles.from_packet_data(b"")
    with raises(ValueError, match="type 11"):
        LighthouseSweepAngles.from_packet_data(bytes([11]) + bytes(21))
    with raises(ValueError, match="expected 22 bytes"):
        LighthouseSweepAngles.from_packet_data(encode(0, angles_of(0.1))[:-1])
    with raises(ValueError, match="expected 23 bytes"):
        LighthouseSweepAngles.from_packet_data(
            encode(0, angles_of(0.1), group=0, group_size=2)[:-1]
        )


async def test_stream_sweep_angles():
    cf = FakeCrazyflie()
    lighthouse = Lighthouse(cf)  # type: ignore
    received = []

    async with lighthouse.stream_sweep_angles() as stream:
        await cf.send(encode(0, angles_of(0.1)))
        await cf.send(encode(0, angles_of(0.2)), channel=0)  # wrong channel
        await cf.send(bytes([11, 0]))  # other packet type
        await cf.send(encode(0, angles_of(0.3))[:-2])  # malformed
        await cf.send(encode(1, angles_of(0.4)))
        with fail_after(10):
            async for measurement in stream:
                received.append(measurement)
                if len(received) == 2:
                    break

    assert [m.base_station for m in received] == [0, 1]
    assert allclose(received[1].angles[0], (0.4, -0.4))
    assert cf.events == [
        "queue opened",
        ("locSrv.enLhAngleStream", 1),
        ("locSrv.enLhAngleStream", 0),
        "queue closed",
    ]


async def test_stream_matched_sweep_angles_sets_parameters():
    cf = FakeCrazyflie()
    lighthouse = Lighthouse(cf)  # type: ignore

    async with lighthouse.stream_sweep_angles(
        matched=True, min_base_stations=3, max_time_ms=40
    ):
        pass

    assert cf.events == [
        "queue opened",
        ("locSrv.minBsLhMtchStm", 3),
        ("locSrv.maxTimeLhMtchStm", 40),
        ("locSrv.enLhMtchStm", 255),
        ("locSrv.enLhMtchStm", 0),
        "queue closed",
    ]


async def test_stream_is_turned_off_when_cancelled():
    cf = FakeCrazyflie()
    lighthouse = Lighthouse(cf)  # type: ignore

    with move_on_after(1):
        async with lighthouse.stream_sweep_angles() as stream:
            async for _ in stream:
                pass

    assert ("locSrv.enLhAngleStream", 0) in cf.events


async def test_group_matched_sweep_angles():
    def measurement(bs: int, group: int | None, size: int = 2):
        return LighthouseSweepAngles(
            base_station=bs, angles=((0.0, 0.0),), group=group, group_size=size
        )

    async def source():
        for item in [
            measurement(0, 1),
            measurement(1, 1),  # complete group 1
            measurement(2, 2),  # group 2 loses a packet
            measurement(2, None),  # regular stream, ignored
            measurement(0, 3, 3),
            measurement(1, 3, 3),
            measurement(3, 3, 3),  # complete group 3
            measurement(1, 4),  # incomplete at the end
        ]:
            yield item

    groups = [group async for group in group_matched_sweep_angles(source())]

    assert [sorted(group) for group in groups] == [[0, 1], [0, 1, 3]]
    assert all(m.group == 3 for m in groups[1].values())


def test_average_sweep_angles_ignores_outliers():
    rng = default_rng(1)
    true_angles = array(angles_of(0.3))
    measurements = [
        LighthouseSweepAngles(
            base_station=2,
            angles=to_angle_tuples(true_angles + rng.normal(scale=1e-3, size=(4, 2))),
        )
        for _ in range(50)
    ]

    # A sensor that missed the sweep reports a stale angle
    stale = true_angles.copy()
    stale[3, 0] += 0.05
    measurements.append(
        LighthouseSweepAngles(base_station=2, angles=to_angle_tuples(stale))
    )
    measurements.append(
        LighthouseSweepAngles(base_station=5, angles=to_angle_tuples(true_angles))
    )

    result = average_sweep_angles(measurements, min_measurements=2)

    assert list(result) == [2]
    assert result[2].shape == (4, 2)
    assert allclose(result[2], true_angles, atol=5e-4)


def test_average_sweep_angles_keeps_nearly_identical_measurements():
    # Most measurements are the same; the median absolute deviation is zero
    # and must not make the slightly different ones look like outliers
    angles = array(angles_of(0.2))
    measurements = [
        LighthouseSweepAngles(base_station=0, angles=to_angle_tuples(angles))
    ] * 5 + [
        LighthouseSweepAngles(base_station=0, angles=to_angle_tuples(angles + 7e-6))
    ] * 2

    result = average_sweep_angles(measurements)

    assert allclose(result[0], angles + 2e-6, rtol=0, atol=1e-9)


async def stream_scene_measurements(
    cf: FakeCrazyflie, samples: Sequence[dict[int, ndarray]], matched: bool
) -> None:
    """Sends the angles of the given samples to the stream of the fake
    Crazyflie, the same way as the firmware: one packet per base station every
    20 milliseconds.
    """
    while cf.sender is None:
        await sleep(0.001)
    for index, sample in enumerate(samples):
        for bs_id, angles in sample.items():
            if matched:
                data = encode(bs_id, angles, group=index % 16, group_size=len(sample))
            else:
                data = encode(bs_id, angles)
            await cf.send(data)
        await sleep(0.02)


async def test_collect_sample():
    scene = create_scene(noise=1e-3, num_free_samples=0, seed=3)
    expected = scene.samples[0]
    rng = default_rng(2)
    noisy = [
        {
            bs_id: angles + rng.normal(scale=1e-3, size=angles.shape)
            for bs_id, angles in expected.items()
        }
        for _ in range(200)
    ]
    cf = FakeCrazyflie()
    lighthouse = Lighthouse(cf)  # type: ignore

    async with create_task_group() as tg:
        tg.start_soon(stream_scene_measurements, cf, noisy, False)
        sample = await collect_sample(lighthouse, duration=1.0)
        tg.cancel_scope.cancel()

    assert sorted(sample) == sorted(expected)
    for bs_id, angles in expected.items():
        # Averaging 50 measurements reduces the noise to about 1.4e-4
        assert allclose(sample[bs_id], angles, atol=6e-4)


async def test_collect_sample_without_base_stations():
    cf = FakeCrazyflie()
    lighthouse = Lighthouse(cf)  # type: ignore

    assert await collect_sample(lighthouse, duration=0.5) == {}


async def test_full_pipeline_from_streamed_measurements():
    # Free samples come from the matched stream as single measurements with
    # the noise of Lighthouse V2; the samples that define the coordinate
    # system of the room are averages of a stationary drone
    scene = create_scene(noise=1e-3, num_free_samples=300, seed=4)
    cf = FakeCrazyflie()
    lighthouse = Lighthouse(cf)  # type: ignore
    reference_count = scene.xy_plane.stop
    samples: list[dict[int, ndarray]] = []

    rng = default_rng(5)
    for drone in scene.drones[:reference_count]:
        repeated = [
            simulate_sample(scene.base_stations, drone, noise=1e-3, rng=rng)
            for _ in range(60)
        ]
        async with create_task_group() as tg:
            tg.start_soon(stream_scene_measurements, cf, repeated, False)
            samples.append(await collect_sample(lighthouse, duration=1.0))
            tg.cancel_scope.cancel()

    free = [
        sample for sample in scene.samples[reference_count:] if 2 <= len(sample) <= 4
    ]
    async with create_task_group() as tg:
        tg.start_soon(stream_scene_measurements, cf, free, True)
        async with stream_matched_samples(lighthouse) as stream:
            # Fail instead of waiting forever if samples are lost
            with fail_after(60):
                async for sample in stream:
                    samples.append(sample)
                    if len(samples) == reference_count + len(free):
                        break
        tg.cancel_scope.cancel()

    guess = estimate_initial_guess(samples)
    solution = refine_geometry(samples, guess.base_stations, guess.samples)
    transform = find_room_transform(
        solution.samples[0],
        [pose.translation for pose in solution.samples[scene.x_axis]],
        [pose.translation for pose in solution.samples[scene.xy_plane]],
    )

    assert solution.converged
    for bs_id, expected in scene.base_stations.items():
        actual = transform.compose(solution.base_stations[bs_id])
        assert norm(actual.translation - expected.translation) < 0.03
        assert rotation_angle_between(actual.rotation, expected.rotation) < radians(0.5)
