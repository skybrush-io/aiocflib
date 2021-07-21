from __future__ import annotations

from anyio import to_thread

from contextlib import asynccontextmanager
from typing import Callable, List, Optional, TYPE_CHECKING

from aiocflib.crtp.crtpstack import CRTPPacket
from aiocflib.utils.addressing import CrazyradioAddress, parse_radio_uri
from aiocflib.utils.concurrency import ObservableValue

from .base import CRTPDriver
from .registry import register

if TYPE_CHECKING:
    import cflinkcpp


__all__ = ("CppRadioDriver",)


def import_cflinkcpp():
    """Lazily imports the `cflinkcpp` module that provides the native C++
    extension to communicate with a Crazyflie over the radio or USB.
    """
    try:
        import cflinkcpp

        return cflinkcpp
    except ImportError:
        raise RuntimeError(
            "You need to install the 'cflinkcpp' module to use the native C++-based radio driver"
        )


@register("cppradio")
class CppRadioDriver(CRTPDriver):
    """CRTP driver that allows us to communicate with a Crazyflie via a
    Crazyradio dongle using a native C++-based driver.

    Attributes:
        polling_strategy: a callable that decides how often we should poll the
            downlink if there are no packets that we want to send to the
            Crazyflie
        resending_strategy: a callable that decides whether we should resend the
            last packet if it failed or whether we should drop the connection
    """

    _connection: "cflinkcpp.Connection"
    _packet_factory: Callable[[], "cflinkcpp.Packet"]

    @asynccontextmanager
    async def _connected_to(self, uri: str):
        # strip middleware and stuff from the URI
        scheme, sep, rest = uri.partition("://")
        if scheme.startswith("usb"):
            uri = "usb://" + rest
        else:
            uri = "radio://" + rest

        cflinkcpp = import_cflinkcpp()
        connection = await to_thread.run_sync(cflinkcpp.Connection, uri)

        try:
            self._connection = connection
            self._packet_factory = cflinkcpp.Packet
            yield self
        finally:
            self._packet_factory = None  # type: ignore
            self._connection = None  # type: ignore
            # TODO(ntamas): why does connection.close() never return when
            # called on a worker thread???
            # await to_thread.run_sync(connection.close)
            connection.close()

    def __init__(self):
        """Constructor."""
        self._connection = None  # type: ignore
        self._link_quality = ObservableValue(0.0)
        self._packet_factory = None  # type: ignore

    @property
    def address(self) -> Optional[CrazyradioAddress]:
        """The address that the driver will be configured for, or ``None`` if
        the driver has no URI.
        """
        if not self._uri:
            return None

        try:
            config = parse_radio_uri(self._uri)
        except Exception:
            return None

        return config["address"] if config else None

    @property
    def is_safe(self) -> bool:
        # TODO(ntamas): make this configurable
        return True

    @property
    def link_quality(self) -> ObservableValue[float]:
        return self._link_quality

    @property
    def name(self) -> str:
        return "cppradio"

    async def notify_rebooted(self) -> None:
        # TODO(ntamas): does the C++ driver restore safe link mode after a
        # reboot?
        pass

    async def receive_packet(self) -> CRTPPacket:
        """Receives a single CRTP packet.

        Returns:
            the next CRTP packet that was received
        """
        while True:
            native_packet = await to_thread.run_sync(self._connection.recv, 100)
            if native_packet.valid:
                packet = CRTPPacket()
                packet.port = native_packet.port
                packet.channel = native_packet.channel
                packet.data = native_packet.payload
                return packet

    async def send_packet(self, packet: CRTPPacket) -> None:
        """Sends a CRTP packet.

        Parameters:
            packet: the packet to send
        """
        native_packet = self._packet_factory()
        native_packet.port = packet.port
        native_packet.channel = packet.channel
        native_packet.payload = bytes(packet.data)
        await to_thread.run_sync(self._connection.send, native_packet)

    @classmethod
    async def scan_interfaces(
        cls, address: Optional[CrazyradioAddress] = None
    ) -> List[str]:
        """Scans all interfaces of this type for available Crazyflie quadcopters
        and returns a list with appropriate connection URIs that could be used
        to connect to them.

        Parameters:
            address: the address of the Crazyflie to look for; `None` means to
                use the default address

        Returns:
            the list of connection URIs where a Crazyflie was detected; an empty
            list is returned for interfaces that do not support scanning
        """
        if isinstance(address, bytes):
            address_as_int = int.from_bytes(address, "big", signed=False)
        else:
            address_as_int = None

        scan = import_cflinkcpp().Connection.scan
        uris = scan(address_as_int) if address_as_int is not None else scan()
        result = []
        for uri in uris:
            if uri.startswith("radio://"):
                uri = "cppradio://" + uri[len("radio://") :]
            result.append(uri)

        return result

    async def use_safe_link(self) -> None:
        """Instructs the driver to start using safe-link mode to ensure
        guaranteed packet delivery to the remote peer.
        """
        # TODO(ntamas): implement this!
        pass
