from collections.abc import Awaitable, Callable
from typing import TypeAlias

__all__ = ("Disposer",)

#: Type alias for disposer functions
Disposer: TypeAlias = Callable[[], Awaitable[None]]
