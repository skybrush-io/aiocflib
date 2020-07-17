from typing import Callable

from aiocflib.crtp.drivers.base import CRTPDriver
from aiocflib.utils.registry import Registry

__all__ = ("MiddlewareRegistry", "find", "register")

#: Type specification for CRTP driver middleware
Middleware = Callable[[CRTPDriver], CRTPDriver]

#: Mapping that maps names to the corresponding middleware classes
MiddlewareRegistry = Registry()  # type: Registry[Middleware]

find = MiddlewareRegistry.find
"""Returns the registered middleware corresponding to the given name.

Parameters:
    name: the name to which the middleware is registered

Raises:
    KeyError: if there is no registered middleware for the given name

Returns:
    the middleware corresponding to the name
"""

register = MiddlewareRegistry.register
"""Class decorator factory that returns a decorator that registers a class
as a middleware with the given name.

Parameters:
    name: the name for which the driver will be registered

Returns:
    an appropriate decorator that can then be applied to a middleware class
"""
