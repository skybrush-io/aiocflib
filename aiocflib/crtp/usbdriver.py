from async_generator import asynccontextmanager, async_generator, yield_
from typing import List
from urllib.parse import urlparse

from aiocflib.crtp.crtpstack import CRTPPacket
from aiocflib.drivers.cfusb import CfUsb

from .crtpdriver import CRTPDriver, register
from .exceptions import WrongURIType


@register("usb")
class USBDriver(CRTPDriver):
    """CRTP driver that allows us to communicate with a Crazyflie that is
    connected directly via a USB cable.
    """

    @asynccontextmanager
    @async_generator
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
                await yield_(self)
        finally:
            self._device = None

    def get_name(self) -> str:
        return "USBCDC"

    async def get_status(self):
        return "No information available"

    def is_safe(self) -> bool:
        return True

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
    async def scan_interface(cls, address=None) -> List[str]:
        """Scans all interfaces of this type for available Crazyflie quadcopters
        and returns a list with appropriate connection URIs that could be used
        to connect to them.

        Returns:
            the list of connection URIs where a Crazyflie was detected; an empty
            list is returned for interfaces that do not support scanning
        """
        devices = await CfUsb.detect_all()
        return ["usb://{0}" for index in range(len(devices))]
