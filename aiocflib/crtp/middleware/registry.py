from typing import Callable, Dict

from aiocflib.crtp.drivers.base import CRTPDriver

__all__ = ("register",)


Middleware = Callable[[CRTPDriver], CRTPDriver]

#: Mapping that maps names to the corresponding middleware classes
_registry = {}  # type: Dict[str, Middleware]


def find(name: str) -> Middleware:
    """Returns the registered middleware corresponding to the given name.

    Parameters:
        name: the name to which the middleware is registered

    Raises:
        KeyError: if there is no registered middleware for the given name

    Returns:
        the middleware corresponding to the name
    """
    return _registry[name]


def register(name: str) -> Callable[[Middleware], Middleware]:
    """Class decorator factory that returns a decorator that registers a class
    as a middleware with the given name.

    Parameters:
        name: the name for which the driver will be registered

    Returns:
        an appropriate decorator that can then be applied to a middleware class
    """

    def decorator(cls):
        existing_cls = _registry.get(name)
        if existing_cls:
            raise ValueError(
                "Name {0!r} is already registered for {1!r}".format(name, existing_cls)
            )
        _registry[name] = cls
        return cls

    return decorator
