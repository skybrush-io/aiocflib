from os import strerror
from typing import Union

__all__ = ("error_to_string",)


def error_to_string(value: Union[int, bytes]) -> str:
    """Converts a Crazyflie error code returned in some CRTP packets to its
    human-readable description.

    Parameters:
        value: the error code, either as an integer or as a bytes object of
            length 1
    """
    if isinstance(value, bytes):
        if len(value) == 1:
            value = value[0]
        else:
            return "not an error code"
    try:
        # Crazyflie uses standard POSIX error codes
        return strerror(value)
    except ValueError:
        return "unknown error"
