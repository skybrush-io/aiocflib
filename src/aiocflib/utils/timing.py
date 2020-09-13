from contextlib import contextmanager
from typing import Callable
from time import time

__all__ = ("timing",)


class TimerContext:
    """Object returned from the `timing` context manager."""

    def __init__(self, timer: Callable[[], float]):
        """Constructor.

        Parameters:
            timer: a function that returns a (preferably monotonic) timestamp in
                seconds when called with no arguments
        """
        self._timer = timer
        self._start = timer()

    @property
    def elapsed(self) -> float:
        """Returns the number of seconds elapsed since entering the context."""
        return self._timer() - self._start


@contextmanager
def timing(description: str = "", timer: Callable[[], float] = time):
    """Context manager that allows us to measure the execution time of a
    code block.

    Parameters:
        description: textual description of what we are measuring
        timer: function that must be called with no arguments to get a
            reading of the clock we are using for measurements
    """
    context = TimerContext(timer)
    yield context
    if description:
        format_str = "{0}: {1:.3f}s"
        print(format_str.format(description, context.elapsed))
