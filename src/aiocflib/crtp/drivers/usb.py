from contextlib import asynccontextmanager
from typing import Any, List, Optional
from urllib.parse import urlparse

from aiocflib.crtp.crtpstack import CRTPPacket
from aiocflib.drivers.cfusb import CfUsb
from aiocflib.utils.concurrency import ObservableValue

from ..exceptions import WrongURIType

from .base import CRTPDriver
from .registry import register


_link_quality = None  # type: Optional[ObservableValue[float]]


@register("usb")
class USBDriver(CRTPDriver):
    """CRTP driver that allows us to communicate with a Crazyflie that is
    connected directly via a USB cable.
    """

    _device: Any

    @asynccontextmanager
    async def _connected_to(self, uri: str):
        parts = urlparse(uri)

        try:
            index = int(parts.netloc)
        except ValueError:
            raise WrongURIType("Invalid USB URI: {0!r}".format(uri))

        if index < 0:
            raise WrongURIType("USB port index must be non-negative")

        device = await CfUsb.detect_one(index=index)
        device.use_crtp_to_usb = True

        try:
            async with device as self._device:
                yield self
        finally:
            self._device = None

    @property
    def index(self) -> Optional[int]:
        """The index of the USB device on the USB hub, or `None` if not known."""
        if not self.uri:
            return None

        parts = urlparse(self.uri)
        try:
            return int(parts.netloc)
        except ValueError:
            return None

    @property
    def is_safe(self) -> bool:
        return True

    @property
    def link_quality(self) -> ObservableValue[float]:
        global _link_quality
        if _link_quality is None:
            _link_quality = ObservableValue.constant(1.0)
        return _link_quality

    @property
    def name(self) -> str:
        return "USBCDC"

    async def receive_packet(self) -> CRTPPacket:
        """Receives a single CRTP packet.

        Returns:
            the next CRTP packet that was received
        """
        data = await self._device.receive_bytes()
        return CRTPPacket.from_bytes(data)

    async def send_packet(self, packet: CRTPPacket):
        """Sends a CRTP packet.

        Parameters:
            packet: the packet to send
        """
        await self._device.send_bytes(packet.to_bytes())

    @classmethod
    async def scan_interfaces(cls) -> List[str]:
        """Scans all interfaces of this type for available Crazyflie quadcopters
        and returns a list with appropriate connection URIs that could be used
        to connect to them.

        Returns:
            the list of connection URIs where a Crazyflie was detected; an empty
            list is returned for interfaces that do not support scanning
        """
        devices = await CfUsb.detect_all()
        return ["usb://{0}" for index in range(len(devices))]
