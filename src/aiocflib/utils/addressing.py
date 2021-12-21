from __future__ import annotations

from binascii import hexlify, unhexlify
from collections.abc import Sequence
from enum import IntEnum
from typing import ClassVar, Dict, Union

__all__ = (
    "AddressSpace",
    "BootloaderAddressSpace",
    "CrazyradioAddress",
    "CrazyradioAddressLike",
    "DEFAULT_RADIO_ADDRESS",
    "DEFAULT_RADIO_BROADCAST_ADDRESS",
    "RadioAddressSpace",
    "USBAddressSpace",
    "parse_radio_uri",
)


#: Type alias for Crazyradio addresses
CrazyradioAddress = bytes

#: Type alias for objects that can be converted into Crazyradio addresses
CrazyradioAddressLike = Union[int, bytes, str]

#: The default Crazyradio address
DEFAULT_RADIO_ADDRESS = b"\xe7\xe7\xe7\xe7\xe7"  # type: CrazyradioAddress

#: The default Crazyradio broadcast address
DEFAULT_RADIO_BROADCAST_ADDRESS = b"\xff\xe7\xe7\xe7\xe7"  # type: CrazyradioAddress


class CrazyradioDataRate(IntEnum):
    """Enum representing the data rates supported by the radio."""

    DR_250KPS = 0
    DR_1MPS = 1
    DR_2MPS = 2

    @classmethod
    def from_string(cls, value):
        if isinstance(value, cls):
            return value
        elif isinstance(value, int):
            return cls(value)
        else:
            value = value.upper()
            if value in ("250K", "250KPS", "250KBPS"):
                return cls.DR_250KPS
            elif value in ("1M", "1MPS", "1MBPS"):
                return cls.DR_1MPS
            elif value in ("2M", "2MPS", "2MBPS"):
                return cls.DR_2MPS
            else:
                return cls(value)

    def __str__(self):
        if self is CrazyradioDataRate.DR_2MPS:
            return "2M"
        elif self is CrazyradioDataRate.DR_1MPS:
            return "1M"
        else:
            return "250K"


def parse_radio_uri(
    uri: str,
    allow_prefix: bool = False,
    default_address: CrazyradioAddress = DEFAULT_RADIO_ADDRESS,
) -> Dict:
    """Parses a Crazyflie radio URI and returns a dictionary containing the
    parsed parts.

    The dictionary will have the following keys:

    * ``scheme`` (the URI scheme)

    * ``index`` (the index of the radio if multiple radio devices are present)

    * ``channel`` (the channel index)

    * ``data_rate`` (the data rate)

    * ``address`` (the parsed address)

    Parameters:
        uri: the URI to parse
        allow_prefix: whether to allow the user to specify only a prefix of the
            Crazyradio address (where we will assume that the remaining bytes
            are all zeros)
        default_address: the default address to use when the address is omitted
            from the URI
    """
    scheme, sep, path = uri.partition("://")
    if not sep:
        raise ValueError("URI must have a scheme")

    if not path:
        raise ValueError("path must not be empty")

    if path[0] == "/":
        path = path[1:]

    if not path or path == "/":
        path = []
    else:
        path = path.split("/")
        if path[0] == "":
            path.pop(0)

    # Parse index
    if path:
        index = path.pop(0)
        try:
            index = int(index)
        except ValueError:
            raise ValueError("Invalid radio index: {0!r}".format(index))
    else:
        index = 0

    # Parse channel
    if path:
        channel = path.pop(0)
        try:
            channel = int(channel)
        except ValueError:
            raise ValueError("Invalid channel index: {0!r}".format(channel))
        if channel < 0 or channel > 125:
            raise ValueError("Invalid channel index: {0!r}".format(channel))
    else:
        channel = 2

    # Parse data rate
    if path:
        data_rate = path.pop(0)
        try:
            data_rate = CrazyradioDataRate.from_string(data_rate)
        except ValueError:
            raise ValueError("Invalid data rate: {0!r}".format(data_rate))
    else:
        data_rate = CrazyradioDataRate.DR_2MPS

    # Parse address
    if path:
        address = path.pop(0)
        try:
            address = to_radio_address(address, allow_prefix=allow_prefix)
        except Exception as ex:
            raise ValueError("Invalid address: {0!r}".format(address)) from ex
    else:
        address = default_address

    # Extra parts at the end
    if path:
        raise ValueError("Excess parts at the end of the path")

    return dict(
        address=address,
        channel=channel,
        data_rate=data_rate,
        index=index,
        scheme=scheme,
    )


def to_radio_address(
    address: CrazyradioAddressLike, *, allow_prefix: bool = False
) -> CrazyradioAddress:
    """Converts a Crazyradio address-like object to a valid address.

    When the input is a bytes object of length 5, it is returned intact.

    When the input is an integer between 0 and 255, inclusive, it is
    appended in hexadecimal form to E7E7E7E7 and the extended byte sequence
    is returned.

    When the input is a hexadecimal string of length 10, it is unhexlified
    and returned as a bytes object.

    Parameters:
        address: the object to convert into a Crazyradio address
        allow_prefix: whether to allow address prefixes (i.e. addresses that
            have less than five bytes). In such cases, the remaining bytes
            of the address are assumed to be zero.
    """
    if isinstance(address, int) and address >= 0 and address <= 255:
        return RadioAddressSpace.DEFAULT.get_address_for(address)

    if isinstance(address, bytes):
        if len(address) == 5:
            return address
        elif len(address) < 5 and allow_prefix:
            address += bytes((0x00,)) * (5 - len(address))
            return address

    if isinstance(address, str) and len(address) % 2 == 0:
        try:
            address = unhexlify(address)
            if len(address) == 5:
                return address
            elif len(address) < 5 and allow_prefix:
                address += bytes((0x00,)) * (5 - len(address))
                return address
        except Exception:
            pass

    if isinstance(address, str):
        return to_radio_address(int(address))

    raise TypeError(
        "expected a bytes object of length 5, a hexadecimal string of "
        "length 10 or an integer between 0 and 255, inclusive, "
        "got {0!r}".format(address)
    )


class AddressSpace(Sequence):
    """Crazyflie address space that can take integers and return a corresponding
    Crazyflie URI address according to some preconfigured rule.
    """

    pass


class BootloaderAddressSpace(AddressSpace):
    """Crazyflie address space that returns URIs where the Crazyflie bootloader
    could be listening.
    """

    DEFAULT: ClassVar["BootloaderAddressSpace"]

    def __init__(self, index: int = 0, scheme: str = "radio"):
        """Constructor.

        Parameters:
            index: the index of the Crazyradio to return in the URIs of the
                address space
            scheme: the URI address scheme to return in the URIs of the address
                space
        """
        self._index = int(index)
        self._items = [
            "{0}://{1}/{2}".format(scheme, index, channel) for channel in (0, 110)
        ]

    def __getitem__(self, index):
        return self._items[index]

    def __len__(self):
        return len(self._items)


class RadioAddressSpace(AddressSpace):
    """Crazyflie radio address space that returns radio addresses using a
    specified radio index, channel, data rate and address prefix.

    For instance, to assign numbers from 0 to 255 to Crazyflies with
    addresses E7E7E7E700 to E7E7E7E7FF using 2MPS data rate and channel 80:

        addresses = RadioAddressSpace(
            index=0,
            channel=80,
            data_rate="2M",
            prefix="E7E7E7E7",
            length=256
        )

    Alternatively, you can use URI notation:

        addresses = RadioAddressSpace.from_uri("radio://0/80/2M/E7E7E7E7")
    """

    DEFAULT: ClassVar["RadioAddressSpace"]

    @classmethod
    def from_uri(cls, uri: str, length: int = 256):
        """Constructs a RadioAddressSpace from its URI representation.

        Parameters:
            uri: the URI representation of the radio address space
            length: the number of addresses in the constructed address space
        """
        parts = parse_radio_uri(uri, allow_prefix=True)
        parts["prefix"] = parts.pop("address", "E7E7E7E7")
        parts["length"] = int(length)
        return cls(**parts)

    def __init__(
        self,
        index: int = 0,
        channel: int = 80,
        data_rate: Union[CrazyradioDataRate, str] = CrazyradioDataRate.DR_2MPS,
        prefix: Union[bytes, str] = "E7E7E7E7",
        length: int = 256,
        scheme: str = "radio",
    ):
        """Constructor.

        Parameters:
            index: the index of the Crazyradio to return in the URIs of the
                address space
            channel: the channel of the Crazyradio to return in the URIs of the
                address space
            data_rate: the data rate of the Crazyradio to return in the URIs
                of the address space
            prefix: the leading non-zero bytes of the addresses to return in the
                URIs of the address space
            length: the size of the address space; the first address will
                correspond to the address obtained from the prefix
            scheme: the URI address scheme to return in the URIs of the address
                space
        """
        self._index = int(index)
        self._channel = int(channel)
        self._data_rate = CrazyradioDataRate.from_string(data_rate)
        self._scheme = str(scheme)
        self._prefix = to_radio_address(prefix, allow_prefix=True)
        self._length = max(0, int(length))

        self._base_address = int.from_bytes(self._prefix, byteorder="big")
        self._uri_prefix = "{0}://{1}/{2}/{3}".format(
            self._scheme, self._index, self._channel, str(self._data_rate)
        )
        self._uri_format = self._uri_prefix + "/{0}"

    def get_address_for(self, index: int) -> CrazyradioAddress:
        """Returns a Crazyradio address for the drone with the given index in
        this address space.

        This function returns the address only; if you need the full URI, use
        the address space as if it was a Python list.
        """
        if index >= 0 and index < self._length:
            address = self._base_address + index
            return address.to_bytes(5, byteorder="big")
        else:
            raise IndexError

    @property
    def uri_prefix(self) -> str:
        """Returns the URI prefix of this address space, without the section
        reserved for the address.
        """
        return self._uri_prefix

    def __getitem__(self, index: int) -> str:
        address = self.get_address_for(index)
        return self._uri_format.format(hexlify(address).decode("ascii").upper())

    def __len__(self) -> int:
        return self._length


class USBAddressSpace(AddressSpace):
    """Crazyflie address space that returns USB addresses.

    Address zero will belong to the first Crazyflie attached directly to the
    computer; address one will belong to the second Crazyflie and so on.

    This address space has a fixed size of 32 by default. If you want an
    accurate length, you need to construct a USBAddressSpace_ asynchronously
    with the `create()` class method; this will retrieve the number of connected
    Crazyflies at construction time. Alternatively, you may use the `refresh()`
    method of the address space to re-scan the USB bus.
    """

    @classmethod
    async def create(cls):
        result = cls()
        await result.refresh()
        return result

    def __init__(self, length: int = 32, scheme: str = "usb"):
        """Constructor.

        Parameters:
            length: the length of the address space
            scheme: the URI address scheme to return in the URIs of the address
                space
        """
        self._length = max(0, int(length))
        self._format_str = "{0}://".format(scheme) + "{0}"

    def __getitem__(self, index: int) -> str:
        if index >= 0 and index < self._length:
            return self._format_str.format(index)
        else:
            raise IndexError

    def __len__(self) -> int:
        return self._length

    async def refresh(self) -> None:
        """Re-scans the USB bus for connected Crazyflie drones."""
        from aiocflib.drivers.cfusb import CfUsb

        self._length = len(await CfUsb.detect_all())


#: Default bootloader address space for the first connected Crazyradio
BootloaderAddressSpace.DEFAULT = BootloaderAddressSpace()


#: Default radio address space that is a sensible choice for drone swarms with
#: less than 256 drones
RadioAddressSpace.DEFAULT = RadioAddressSpace()
