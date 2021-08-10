"""Statistics-related utility classes and functions."""

from collections import deque
from typing import Optional

__all__ = ("SlidingWindowMean",)


class SlidingWindowMean:
    """Sliding window mean calculator."""

    def __init__(self, window_size: int, fill: Optional[float] = None):
        """Constructor."""
        self._fill = fill
        self._window_size = window_size
        self.reset()

    def add(self, value: float) -> float:
        if len(self._data) == self._window_size:
            self._sum += value - self._data[0]
        else:
            self._sum += value
        self._data.append(value)
        return self._sum

    @property
    def mean(self) -> float:
        return self._sum / len(self._data)

    def reset(self) -> None:
        """Resets the sliding window mean calculator to its base state."""
        n = self._window_size
        if self._fill is not None:
            self._data = deque([self._fill] * n, n)
        else:
            self._data = deque([], n)
        self._sum = float(sum(self._data))
