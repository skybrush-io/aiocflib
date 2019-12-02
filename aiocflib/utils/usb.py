"""USB-related low-level utility functions."""

from typing import Any, List

import os

__all__ = (
    "find_devices",
    "get_vendor_setup",
    "is_pyusb1",
    "send_vendor_setup",
    "USBDevice",
    "USBError",
)

#: Type variable to represent a USB device
USBDevice = Any


try:
    import usb.core

    _backend = None
    if os.name == "nt":
        import usb.backend.libusb0 as libusb0

        _backend = libusb0.get_backend()
    is_pyusb1 = True

except Exception:
    _backend = None
    is_pyusb1 = False


def find_devices(vid: int, pid: int) -> List[USBDevice]:
    """Helper function that finds all USB devices with a given vendor and
    product ID on all available USB buses.
    """
    if is_pyusb1:
        return list(
            usb.core.find(idVendor=vid, idProduct=pid, find_all=1, backend=_backend)
        )
    else:
        return [
            device
            for bus in usb.busses()
            for device in bus.devices
            if device.idVendor == vid and device.idProduct == pid
        ]


def get_vendor_setup(handle, request, value, index, length, timeout=1000):
    if is_pyusb1:
        return handle.ctrl_transfer(
            usb.TYPE_VENDOR | 0x80,
            request,
            wValue=value,
            wIndex=index,
            timeout=timeout,
            data_or_wLength=length,
        )
    else:
        return handle.controlMsg(
            usb.TYPE_VENDOR | 0x80,
            request,
            length,
            value=value,
            index=index,
            timeout=timeout,
        )


def send_vendor_setup(handle, request, value, index=0, data=(), timeout=1000):
    if is_pyusb1:
        handle.ctrl_transfer(
            usb.TYPE_VENDOR,
            request,
            wValue=value,
            wIndex=index,
            timeout=timeout,
            data_or_wLength=data,
        )
    else:
        handle.controlMsg(
            usb.TYPE_VENDOR, request, data, value=value, index=index, timeout=timeout
        )


USBError = usb.core.USBError
