"""Various utilities that is needed by the asynchronous Crazyflie library."""

from .chunks import chunkify
from .timing import timing

__all__ = ("anop", "chunkify", "nop", "timing")


def nop(*args, **kwds) -> None:
    """Dummy function that can be invoked with arbitrary arguments and that
    does nothing.
    """
    pass


async def anop(*args, **kwds) -> None:
    """Dummy async function that can be invoked with arbitrary arguments and that
    does nothing.
    """
    pass
