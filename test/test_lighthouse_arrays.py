from numpy import allclose, arange, array_equal, eye, float64, zeros
from pytest import raises

from aiocflib.lighthouse.arrays import (
    normalize,
    to_float_array,
    to_float_array_or_default,
)


def test_to_float_array():
    result = to_float_array([1, 2, 3])
    assert result.dtype == float64
    assert result.shape == (3,)

    assert to_float_array([[1, 2, 3], [4, 5, 6]], (-1, 3)).shape == (2, 3)
    assert to_float_array([1, 2, 3], (-1, 3)).shape == (1, 3)
    assert to_float_array([], (-1, 3)).shape == (0, 3)

    with raises(ValueError):
        to_float_array([1, 2], (-1, 3))


def test_to_float_array_copies_input():
    source = zeros(3)
    result = to_float_array(source)
    result[0] = 1.0
    assert source[0] == 0.0


def test_to_float_array_or_default():
    default = eye(3)
    assert array_equal(to_float_array_or_default(None, default, (3, 3)), default)
    assert to_float_array_or_default(None, default) is not default
    assert array_equal(
        to_float_array_or_default(range(9), default, (3, 3)),
        arange(9.0).reshape(3, 3),
    )


def test_normalize():
    assert allclose(normalize([3.0, 0.0, 4.0]), (0.6, 0.0, 0.8))
    assert allclose(
        normalize([[2.0, 0.0, 0.0], [0.0, 0.0, -5.0]]), [[1, 0, 0], [0, 0, -1]]
    )

    with raises(ValueError, match="too short"):
        normalize([0.0, 0.0, 0.0])
    with raises(ValueError, match="too short"):
        normalize([[1.0, 0.0, 0.0], [0.01, 0.0, 0.0]], min_length=0.1)
