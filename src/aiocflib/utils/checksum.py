"""Checksum-related utility functions."""

from binascii import crc32 as crc32_uint32
from struct import Struct

__all__ = ("crc32", "ensure_not_all_zero_bytes")


_uint32_struct = Struct("<I")


def crc32(data: bytes) -> bytes:
    """Calculates the CRC32 checksum of the given bytes object.

    Parameters:
        data: the data to calculate the checksum for

    Returns:
        the CRC32 checksum, in little endian order
    """
    return _uint32_struct.pack(crc32_uint32(data) & 0xFFFFFFFF)


_sentinel = b"\x0b\xad\xca\xfe" * 64


def ensure_not_all_zero_bytes(data: bytes) -> bytes:
    """Examines the given bytes object and replaces it with a sentinel value
    if it has at least one byte and all the bytes in the object are zeros.

    This is used internally by MemoryHandler.write_with_checksum(); you probably
    don't need it on your own.

    Parameters:
        data: the input data

    Returns:
        the input data if it contains at least one non-zero byte or if it is
        empty, otherwise a sentinel value
    """
    if data and not any(x for x in data):
        if len(data) <= len(_sentinel):
            return _sentinel[: len(data)]
        else:
            result = _sentinel * (len(data) // len(_sentinel))
            result += _sentinel[: len(data) - len(result)]
            return result
    else:
        return data
