from binascii import hexlify
from collections.abc import Sequence
from typing import Union

from aiocflib.drivers.crazyradio import Crazyradio, CrazyradioAddress, CrazyradioDataRate

__all__ = ("AddressSpace", "RadioAddressSpace", "USBAddressSpace")


class AddressSpace(Sequence):
    """Crazyflie address space that can take integers and return a corresponding
    Crazyflie URI address according to some preconfigured rule.
    """

    pass


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

    def __init__(self, index: int = 0, channel: int = 80, data_rate: Union[CrazyradioDataRate, str] = CrazyradioDataRate.DR_2MPS, prefix: Union[bytes, str] = "E7E7E7E7", length: int = 256):
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
        """
        self._index = int(index)
        self._channel = int(channel)
        self._data_rate = CrazyradioDataRate.from_string(data_rate)
        self._prefix = Crazyradio.to_address(prefix, allow_prefix=True)
        self._length = max(0, int(length))

        self._base_address = int.from_bytes(self._prefix, byteorder="big")
        self._uri_prefix = "radio://{0}/{1}/{2}".format(self._index, self._channel, str(self._data_rate))
        self._uri_format = self._uri_prefix + "/{0}"

    def get_address_for(self, index: int) -> CrazyradioAddress:
        """Returns a Crazyradio address for the drone with the given index in
        this address space.

        This function returns the address only; if you need the full URI, use
        the address space as if it was a Python list.
        """
        address = (self._base_address + index)
        return address.to_bytes(5, byteorder="big")

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

    def __init__(self, length: int = 32):
        """Constructor.

        Parameters:
            length: the length of the address space
        """
        self._length = max(0, int(length))

    def __getitem__(self, index: int) -> str:
        return "usb://{0}".format(index)

    def __len__(self) -> int:
        return self._length

    async def refresh(self) -> None:
        """Re-scans the USB bus for connected Crazyflie drones."""
        from aiocflib.drivers.cfusb import CfUsb
        self._length = len(await CfUsb.detect_all())


#: Default radio address space that is a sensible choice for drone swarms with
#: less than 256 drones
RadioAddressSpace.DEFAULT = RadioAddressSpace()
