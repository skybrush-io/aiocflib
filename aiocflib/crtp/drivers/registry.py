from typing import Callable, Dict, Type

from .base import CRTPDriver

__all__ = ("register",)


#: Mapping that maps names to the corresponding CRTPDriver classes
_registry = {}  # type: Dict[str, Type[CRTPDriver]]


CRTPDriverFactory = Callable[[], CRTPDriver]


def find(name: str) -> CRTPDriverFactory:
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
    return _registry[name]


def register(name: str) -> Callable[[CRTPDriverFactory], CRTPDriverFactory]:
    """Class decorator factory that returns a decorator that registers a class
    as a CRTP driver with the given name.

    Parameters:
        name: the name to which the driver factory is registered

    Returns:
        an appropriate decorator that can then be applied to a CRTPDriver
        subclass
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
