from typing import Awaitable, Callable


__all__ = ("Disposer",)

#: Type alias for disposer functions
Disposer = Callable[[], Awaitable[None]]
