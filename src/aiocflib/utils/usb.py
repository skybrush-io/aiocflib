"""USB-related low-level utility functions."""

from anyio import Lock
from contextlib import asynccontextmanager
from typing import Any, List
from weakref import WeakValueDictionary

import os

__all__ = (
    "claim_device",
    "find_devices",
    "get_vendor_setup",
    "is_pyusb1",
    "release_device",
    "send_vendor_setup",
    "USBDevice",
    "USBError",
)

#: Type variable to represent a USB device
USBDevice = Any

#: Module-level variable to hold a mapping from unique USB device IDs to their
#: locks that prevent concurrent access to these devices
_locks: "WeakValueDictionary[str, Lock]" = WeakValueDictionary()

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


@asynccontextmanager
async def claim_device(device: USBDevice):
    """Asynchronous context manager that claims a USB device and prevents
    others from claiming the same device as long as the execution stays
    within the context.
    """
    global _locks

    uid = get_device_uid(device)
    lock = _locks.get(uid)
    if lock is None:
        _locks[uid] = lock = Lock()

    async with lock:
        yield


def find_devices(vid: int, pid: int) -> List[USBDevice]:
    """Helper function that finds all USB devices with a given vendor and
    product ID on all available USB buses.
    """
    if is_pyusb1:
        return list(
            usb.core.find(idVendor=vid, idProduct=pid, find_all=1, backend=_backend)  # type: ignore
        )
    else:
        return [
            device
            for bus in usb.busses()  # type: ignore
            for device in bus.devices
            if device.idVendor == vid and device.idProduct == pid
        ]


def get_vendor_setup(handle, request, value, index, length, timeout=1000):
    if is_pyusb1:
        return handle.ctrl_transfer(
            usb.TYPE_VENDOR | 0x80,  # type: ignore
            request,
            wValue=value,
            wIndex=index,
            timeout=timeout,
            data_or_wLength=length,
        )
    else:
        return handle.controlMsg(
            usb.TYPE_VENDOR | 0x80,  # type: ignore
            request,
            length,
            value=value,
            index=index,
            timeout=timeout,
        )


def get_device_uid(device: USBDevice) -> str:
    """Returns a string that can be used to uniquely identify a USB device
    that is currently plugged in.

    Currently the string contains the bus number and the device number of the
    device. This means that the unique identifier may change if the device is
    unplugged and then plugged in again at a different port.
    """
    return "{0.bus}:{0.address}".format(device)


def release_device(device: USBDevice):
    from usb.util import dispose_resources

    dispose_resources(device)


def send_vendor_setup(handle, request, value, index=0, data=(), timeout=1000):
    if is_pyusb1:
        handle.ctrl_transfer(
            usb.TYPE_VENDOR,  # type: ignore
            request,
            wValue=value,
            wIndex=index,
            timeout=timeout,
            data_or_wLength=data,
        )
    else:
        handle.controlMsg(
            usb.TYPE_VENDOR,  # type: ignore
            request,
            data,
            value=value,
            index=index,
            timeout=timeout,
        )


USBError = usb.core.USBError  # type: ignore
