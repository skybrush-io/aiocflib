"""Interface specificaton for asynchronous CRTP driver implementations."""

from abc import ABCMeta, abstractmethod, abstractproperty
from async_generator import asynccontextmanager, async_generator, yield_
from typing import Callable, Dict, List, Type

from aiocflib.crtp.crtpstack import CRTPPacket

from .exceptions import WrongURIType

__author__ = "CollMot Robotics Ltd"
__all__ = ("CRTPDriver",)


class CRTPDriver(metaclass=ABCMeta):
    """Interface specificaton for asynchronous CRTP driver implementations.

    All asynchronous CRTP driver implementations must inherit from this class.
    """

    @staticmethod
    @asynccontextmanager
    @async_generator
    async def connected_to(uri, *args, **kwds):
        """Creates a CRTPDriver_ instance from a URI specification and connects
        to the Crazyflie located at the given URI. Closes the connection when the
        execution exits the context.

        Parameters:
            uri: the URI to create the driver instance from

        Additional positional and keyword arguments are forwarded to the
        driver factory determined from the URI scheme.
        """
        scheme, sep, rest = uri.partition("://")
        if not sep:
            raise ValueError("CRTP driver URI must contain ://")

        driver_factory = _registry.get(scheme)
        if driver_factory is None:
            raise WrongURIType("Unknown CRTP driver URI: {0!r}".format(scheme))

        driver = driver_factory(*args, **kwds)
        async with driver._connected_to(uri):
            await yield_(driver)

    @abstractmethod
    async def _connected_to(self, uri: str):
        """Connects the driver instance to a specified URI.

        This method is not public; use the `connected_to()` async context manager
        instead., which ensures that the link is closed when the execution leaves
        the context.

        Since the method is not meant for public use, it does _not_ check whether
        the received URI has a scheme that corresponds to the driver; it is
        assumed that the caller already took care of it.

        Parameters:
            uri: the URI to connect the driver to

        Returns:
            an async context manager that connects to the specified URI when
            the context is entered and that closes the connection when the
            context is exited
        """
        # TODO(ntamas): how to replace link quality callbacks?
        raise NotImplementedError

    @abstractmethod
    def get_name(self) -> str:
        """Returns a human-readable name of the interface.

        Returns:
            a human-readable name of the interface
        """
        raise NotImplementedError

    @abstractmethod
    async def get_status(self) -> str:
        """Returns a status string that describes the current state of the
        interface.

        Returns:
            the status string
        """
        raise NotImplementedError

    @abstractproperty
    def is_safe(self) -> bool:
        """Returns whether this link is safe.

        A safe link guarantees that each packet that was sent via the link
        eventually gets delivered to the peer, _or_ that the link gets closed
        if the delivery fails.
        """
        raise NotImplementedError

    @abstractmethod
    async def receive_packet(self) -> CRTPPacket:
        """Receives a single CRTP packet.

        Returns:
            the next CRTP packet that was received
        """
        raise NotImplementedError

    @abstractmethod
    async def send_packet(self, packet: CRTPPacket):
        """Sends a CRTP packet.

        Parameters:
            packet: the packet to send
        """
        raise NotImplementedError

    @classmethod
    async def scan_interface(cls, address=None) -> List[str]:
        """Scans all interfaces of this type for available Crazyflie quadcopters
        and returns a list with appropriate connection URIs that could be used
        to connect to them.

        Returns:
            the list of connection URIs where a Crazyflie was detected; an empty
            list is returned for interfaces that do not support scanning
        """
        return []


#: Mapping that maps URI schemes to the corresponding CRTPDriver classes
_registry = {}  # type: Dict[str, Type[CRTPDriver]]


CRTPDriverFactory = Callable[[], CRTPDriver]


def register(scheme: str) -> Callable[[CRTPDriverFactory], CRTPDriverFactory]:
    """Class decorator factory that returns a decorator that registers a class
    as a CRTP driver with the given URI scheme.

    Parameters:
        scheme: the URI scheme for which the driver will be registered

    Returns:
        an appropriate decorator that can then be applied to a CRTPDriver
        subclass
    """

    def decorator(cls):
        existing_cls = _registry.get(scheme)
        if existing_cls:
            raise ValueError(
                "URI scheme {0!r} is already registered for {1!r}".format(
                    scheme, existing_cls
                )
            )
        _registry[scheme] = cls
        return cls

    return decorator
