"""Interface specificaton for asynchronous CRTP driver implementations."""

from abc import ABCMeta, abstractmethod, abstractproperty
from contextlib import asynccontextmanager
from typing import AsyncContextManager, List, Optional

from aiocflib.crtp.crtpstack import CRTPPacket
from aiocflib.utils.concurrency import ObservableValue

from ..exceptions import WrongURIType

__author__ = "CollMot Robotics Ltd"
__all__ = ("CRTPDriver",)


class CRTPDriver(metaclass=ABCMeta):
    """Interface specificaton for asynchronous CRTP driver implementations.

    All asynchronous CRTP driver implementations must inherit from this class.
    """

    @staticmethod
    @asynccontextmanager
    async def connected_to(uri: str, *args, **kwds):
        """Creates a CRTPDriver_ instance from a URI specification and connects
        to the Crazyflie located at the given URI. Closes the connection when the
        execution exits the context.

        Parameters:
            uri: the URI to create the driver instance from

        Additional positional and keyword arguments are forwarded to the
        driver factory determined from the URI scheme.
        """
        from .registry import find as find_driver
        from ..middleware.registry import find as find_middleware

        scheme, sep, rest = uri.partition("://")
        if not sep:
            raise ValueError("CRTP driver URI must contain ://")

        scheme, *middleware_names = scheme.split("+")

        try:
            driver_factory = find_driver(scheme)
        except KeyError:
            raise WrongURIType("Unknown CRTP driver URI: {0!r}".format(scheme))

        driver = driver_factory(*args, **kwds)
        driver.uri = uri

        for middleware_name in middleware_names:
            try:
                middleware = find_middleware(middleware_name)
            except KeyError:
                raise WrongURIType(
                    "Unknown middleware in URI: {0!r}".format(middleware_name)
                )
            driver = middleware(driver)

        async with driver._connected_to(uri):
            yield driver

    @abstractmethod
    def _connected_to(self, uri: str) -> AsyncContextManager[None]:
        """Connects the driver instance to a specified URI.

        This method is not public; use the `connected_to()` async context manager
        instead, which ensures that the link is closed when the execution leaves
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
        raise NotImplementedError

    @abstractproperty
    def is_safe(self) -> bool:
        """Returns whether this link is safe.

        A safe link guarantees that each packet that was sent via the link
        eventually gets delivered to the peer, _or_ that the link gets closed
        if the delivery fails.
        """
        raise NotImplementedError

    @abstractproperty
    def link_quality(self) -> ObservableValue[float]:
        """Observable measurement of link quality, as a float between 0 (no
        link) and 1 (perfect link).
        """
        raise NotImplementedError

    @abstractproperty
    def name(self) -> str:
        """Returns a human-readable name of the interface."""
        raise NotImplementedError

    async def notify_rebooted(self) -> None:
        """Notifies the driver that the underlying Crazyflie device has been
        rebooted. The drivers may respond to this request by scheduling some
        setup operations to perform on the link again in an attempt to restore
        the link to the state before the reboot.
        """
        pass

    @abstractmethod
    async def receive_packet(self) -> CRTPPacket:
        """Receives a single CRTP packet.

        Returns:
            the next CRTP packet that was received
        """
        raise NotImplementedError

    @classmethod
    async def scan_interfaces(cls) -> List[str]:
        """Scans all interfaces of this type for available Crazyflie quadcopters
        and returns a list with appropriate connection URIs that could be used
        to connect to them.

        Returns:
            the list of connection URIs where a Crazyflie was detected; an empty
            list is returned for interfaces that do not support scanning
        """
        return []

    @abstractmethod
    async def send_packet(self, packet: CRTPPacket) -> None:
        """Sends a CRTP packet.

        Parameters:
            packet: the packet to send
        """
        raise NotImplementedError

    @property
    def uri(self) -> Optional[str]:
        """The URI with which the driver was created, if known."""
        return getattr(self, "_uri", None)

    @uri.setter
    def uri(self, value: str) -> None:
        self._uri = value

    async def use_safe_link(self) -> None:
        """Notifies the driver that the caller wishes to use a safe link where
        it does not have to worry about packet loss.

        When the driver supports safe link mode, it should switch into a mode
        where this can be ensured.
        """
        pass
