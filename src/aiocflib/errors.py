from os import strerror
from typing import Optional, Union

__all__ = ("error_to_string",)


class NotFoundError(RuntimeError):
    """Error thrown when a detection / scanning routine failed."""

    pass


class TimeoutError(RuntimeError):
    """Error thrown when a command sent to a CRTP-based device (for instance,
    a Crazyflie) has timed out.
    """

    pass


class CRTPCommandError(RuntimeError):
    """Error thrown when a command sent to a CRTP-based device (for instance,
    a Crazyflie) returned an error code.
    """

    def __init__(self, message: Optional[str] = None, code: int = 0):
        message = message or f"CRTP command returned error {code}"
        super().__init__(message)


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
