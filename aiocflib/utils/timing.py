from contextlib import contextmanager
from typing import Callable
from time import time

__all__ = ("timing",)


@contextmanager
def timing(description: str = "", timer: Callable[[], float] = time):
    """Context manager that allows us to measure the execution time of a
    code block.

    Parameters:
        description: textual description of what we are measuring
        timer: function that must be called with no arguments to get a
            reading of the clock we are using for measurements
    """
    start = timer()
    format_str = "{0}: {1:.3}s" if description else "{1:.3}s"
    yield
    print(format_str.format(description, timer() - start))
