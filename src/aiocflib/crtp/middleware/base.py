"""Base class for middlewares that forward each call to an underlying CRTP
driver instance before / after performing some operations on the call
arguments.
"""

from contextlib import asynccontextmanager
from typing import Optional

from aiocflib.crtp.crtpstack import CRTPPacket
from aiocflib.crtp.drivers.base import CRTPDriver
from aiocflib.utils.concurrency import ObservableValue


class MiddlewareBase(CRTPDriver):
    """Base class for middlewares that forward each call to an underlying CRTP
    driver instance before / after performing some operations on the call
    arguments.
    """

    def __init__(self, wrapped: CRTPDriver):
        """Constructor.

        Parameters:
            wrapped: the CRTP driver that the middleware wraps
        """
        self._wrapped = wrapped
        self._init()

    @asynccontextmanager
    async def _connected_to(self, uri: str):
        async with self._wrapped._connected_to(uri):
            yield

    def _init(self) -> None:
        pass

    @property
    def is_safe(self) -> bool:
        return self._wrapped.is_safe

    @property
    def link_quality(self) -> ObservableValue[float]:
        return self._wrapped.link_quality

    @property
    def name(self) -> str:
        return self._wrapped.name

    @property
    def uri(self) -> Optional[str]:
        return self._wrapped.uri

    @property
    def wrapped(self) -> CRTPDriver:
        """Returns the CRTP driver wrapped by the middleware."""
        return self._wrapped

    async def notify_rebooted(self) -> None:
        return await self._wrapped.notify_rebooted()

    async def receive_packet(self) -> CRTPPacket:
        return await self._wrapped.receive_packet()

    async def send_packet(self, packet: CRTPPacket):
        return await self._wrapped.send_packet(packet)

    async def use_safe_link(self):
        return await self._wrapped.use_safe_link()
