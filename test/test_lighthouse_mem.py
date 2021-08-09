from pytest import fixture, raises
from aiocflib.crazyflie.lighthouse import (
    LighthouseBsCalibration,
    LighthouseBsGeometry,
    LighthouseCalibrationSweep,
)


@fixture
def geometry():
    return LighthouseBsGeometry(
        origin=(1, 2, 3),
        rotation_matrix=((4, 5, 6), (7, 8, 9), (10, 11, 12)),
        valid=True,
    )


@fixture
def sweep():
    return LighthouseCalibrationSweep(
        phase=1, tilt=2, curve=3, gibmag=4, gibphase=5, ogeemag=6, ogeephase=7
    )


@fixture
def calibration(sweep):
    return LighthouseBsCalibration(sweeps=(sweep, sweep), uid=1234, valid=True)


class TestLighthouseBsGeometry:
    def test_to_from_json(self, geometry):
        geom2 = LighthouseBsGeometry.from_json(geometry.to_json())
        assert geometry == geom2

    def test_to_from_bytes(self, geometry):
        data = geometry.to_bytes()
        geom2 = LighthouseBsGeometry.from_bytes(data)
        assert geometry == geom2

        with raises(ValueError):
            LighthouseBsGeometry.from_bytes(data + b"1234")

    def test_unpack_from_bytes(self, geometry):
        data = b"1234" + geometry.to_bytes() + b"56"
        geom2, new_offset = LighthouseBsGeometry.unpack_from_bytes(data, offset=4)
        assert geometry == geom2
        assert new_offset == len(data) - 2

        with raises(ValueError):
            LighthouseBsGeometry.unpack_from_bytes(data[:-10])


class TestLighthouseCalibrationSweep:
    def test_to_from_json(self, sweep):
        sweep2 = LighthouseCalibrationSweep.from_json(sweep.to_json())
        assert sweep2 == sweep

    def test_to_from_bytes(self, sweep):
        data = sweep.to_bytes()
        sweep2 = LighthouseCalibrationSweep.from_bytes(data)
        assert sweep2 == sweep

        with raises(ValueError):
            LighthouseCalibrationSweep.from_bytes(data + b"1234")

    def test_unpack_from_bytes(self, sweep):
        data = b"1234" + sweep.to_bytes() + b"56"
        sweep2, new_offset = LighthouseCalibrationSweep.unpack_from_bytes(
            data, offset=4
        )
        assert sweep2 == sweep
        assert new_offset == len(data) - 2

        with raises(ValueError):
            LighthouseCalibrationSweep.unpack_from_bytes(data[:-10])


class TestLighthouseBsCalibration:
    def test_to_from_json(self, calibration):
        calib2 = LighthouseBsCalibration.from_json(calibration.to_json())
        assert calib2 == calibration

    def test_to_from_bytes(self, calibration):
        data = calibration.to_bytes()
        calib2 = LighthouseBsCalibration.from_bytes(data)
        assert calib2 == calibration

        with raises(ValueError):
            LighthouseBsCalibration.from_bytes(data + b"1234")

    def test_unpack_from_bytes(self, calibration):
        data = b"1234" + calibration.to_bytes() + b"56"
        calib2, new_offset = LighthouseBsCalibration.unpack_from_bytes(data, offset=4)
        assert calib2 == calibration
        assert new_offset == len(data) - 2

        with raises(ValueError):
            LighthouseBsCalibration.unpack_from_bytes(data[:-10])
