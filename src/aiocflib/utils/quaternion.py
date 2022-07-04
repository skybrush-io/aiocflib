"""Functions related to rotations and quaternions."""

from math import sqrt
from typing import List, NamedTuple

QuaternionXYZW = NamedTuple(
    "QuaternionXYZW", [("x", float), ("y", float), ("z", float), ("w", float)]
)

SQRT1_2 = 1.0 / sqrt(2)


def normalize_quaternion(quat: QuaternionXYZW) -> QuaternionXYZW:
    """Normalizes a quaternion to unit length."""
    length = sqrt(sum(x**2 for x in quat))
    x, y, z, w = quat
    return QuaternionXYZW(x / length, y / length, z / length, w / length)


def compress_unit_quaternion(quat: QuaternionXYZW, *, normalize: bool = False) -> int:
    """Compresses a generic XYZW quaternion of unit length into a 32-bit
    unsigned integer representation.

    In the compressed representation, we make use of the following:

    - we only send the smallest three components as the fourth component can be
      inferred for unit quaternions

    - the second-largest element can be at most 1 / sqrt(2) so we can normalize
      with that again to increase resolution

    - we assume that the sign of the largest component is positive so we don't
      need to encode that

    - we use 9 bits plus 1 sign bit per component

    Parameters:
        quat: the quaternion to compress
        normalize: whether to normalize the quaternion before compressing it.
            Setting this variable to ``False`` means that the caller _knows_
            that the quaternion is already normalized, and the function will
            fail if it is not normalized.

    Returns:
        the compressed representation of the quaternion
    """
    if normalize:
        quat = normalize_quaternion(quat)

    largest_index = 0
    for index in range(4):
        if abs(quat[index]) > abs(quat[largest_index]):
            largest_index = index

    negate = quat[largest_index] < 0
    comp = largest_index
    for index in range(4):
        if index != largest_index:
            negbit = (quat[index] < 0) ^ negate
            mag = int(round(((1 << 9) - 1) * (abs(quat[index]) / SQRT1_2)))
            comp = (comp << 10) | (negbit << 9) | mag

    return comp


def decompress_unit_quaternion(quat_compressed: int) -> QuaternionXYZW:
    """Decompresses a generic XYZW quaternion of unit length from its 32-bit
    unsigned integer representation.

    See `compress_unit_quaternion()` for more details.

    Parameters:
        quat_compressed: the compressed quaternion to decompress

    Returns:
        the standard representation of the quaternion
    """
    mask: int = (1 << 9) - 1
    largest_index: int = (quat_compressed >> 30) & 0x3
    sum_squares: float = 0.0
    result: List[float] = [0.0] * 4

    for index in range(3, -1, -1):
        if index != largest_index:
            mag = quat_compressed & mask
            negate = (quat_compressed >> 9) & 1
            quat_compressed >>= 10
            result[index] = (SQRT1_2 * mag) / mask
            if negate:
                result[index] *= -1
            sum_squares += result[index] * result[index]

    result[largest_index] = sqrt(1.0 - sum_squares)
    return QuaternionXYZW(*result)
