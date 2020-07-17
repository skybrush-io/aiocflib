from typing import Callable

from aiocflib.utils.registry import Registry

from .base import CRTPDriver

__all__ = ("DriverRegistry", "find", "register")


#: Type specification for CRTP driver factories
CRTPDriverFactory = Callable[[], CRTPDriver]

#: Mapping that maps names to the corresponding CRTPDriver classes
DriverRegistry = Registry()  # type: Registry[CRTPDriverFactory]

find = DriverRegistry.find
"""Returns the registered CRTP driver factory corresponding to the given
name.

Parameters:
    name: the name to which the driver factory is registered

Raises:
    KeyError: if there is no registered CRTP driver factory for the given
        name

Returns:
    the driver factory corresponding to the name
"""

register = DriverRegistry.register
"""Class decorator factory that returns a decorator that registers a class
as a CRTP driver with the given name.

Parameters:
    name: the name to which the driver factory is registered

Returns:
    an appropriate decorator that can then be applied to a CRTPDriver
    subclass
"""
