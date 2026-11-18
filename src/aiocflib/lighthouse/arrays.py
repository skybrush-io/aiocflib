"""Helper functions for working with NumPy arrays."""

from __future__ import annotations

from numpy import array, float64
from numpy.linalg import norm
from numpy.typing import ArrayLike, NDArray

__all__ = ("normalize", "to_float_array", "to_float_array_or_default")


def to_float_array(
    value: ArrayLike, shape: tuple[int, ...] | None = None
) -> NDArray[float64]:
    """Converts a value to a new array of 64-bit floats.

    Args:
        value: the value to convert, for instance a sequence of vectors
        shape: the shape of the result, as accepted by `numpy.reshape()`;
            ``None`` keeps the shape of the value. For instance, ``(-1, 3)``
            turns a single vector or a possibly empty sequence of vectors into
            an array with one vector in each row.

    Returns:
        the converted value. It never shares memory with the input.

    Raises:
        ValueError: if the value cannot be reshaped to the given shape
    """
    result = array(value, dtype=float64)
    return result if shape is None else result.reshape(shape)


def to_float_array_or_default(
    value: ArrayLike | None,
    default: ArrayLike,
    shape: tuple[int, ...] | None = None,
) -> NDArray[float64]:
    """Converts a value to a new array of 64-bit floats, using a default if the
    value is ``None``.

    Args:
        value: the value to convert
        default: the value to convert instead if the value is ``None``
        shape: the shape of the result; see `to_float_array()`

    Raises:
        ValueError: if the value cannot be reshaped to the given shape
    """
    return to_float_array(default if value is None else value, shape)


def normalize(vectors: ArrayLike, *, min_length: float = 1e-12) -> NDArray[float64]:
    """Scales vectors to unit length.

    Args:
        vectors: array of shape ``(..., n)``
        min_length: vectors shorter than this cannot be normalized

    Returns:
        array of the same shape as the input, containing unit vectors

    Raises:
        ValueError: if any of the vectors is shorter than the given length
    """
    v = to_float_array(vectors)
    lengths = norm(v, axis=-1, keepdims=True)
    if (lengths < min_length).any():
        raise ValueError("Vector is too short to normalize")
    return v / lengths
