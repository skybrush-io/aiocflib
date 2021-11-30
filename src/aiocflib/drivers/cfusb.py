"""Asynchronous USB driver for the Crazyflie."""

from anyio import create_memory_object_stream, to_thread
from anyio.streams.stapled import StapledObjectStream
from contextlib import AsyncExitStack
from functools import partial
from typing import List, Optional

import usb

from aiocflib.utils.concurrency import Full, ThreadContext
from aiocflib.errors import NotFoundError
from aiocflib.utils.usb import (
    claim_device,
    find_devices,
    is_pyusb1,
    release_device,
    send_vendor_setup,
    USBDevice,
    USBError,
)


__author__ = "CollMot Robotics Ltd"
__all__ = ("CfUsb",)


USB_VID = 0x0483
USB_PID = 0x5740


def _find_devices() -> List[USBDevice]:
    """Returns a list of Crazyflie drones currently connected to the computer.

    This function uses `pyusb` functions directly, hence it will block the
    calling thread. You must call it in a separate worker thread when you use
    it in conjunction with an asyncio framework.
    """
    candidates = find_devices(vid=USB_VID, pid=USB_PID)
    if is_pyusb1:
        return [device for device in candidates if device.manufacturer == "Bitcraze AB"]
    else:
        return candidates


class CfUsb:
    """Low-level driver object that is used for communication with the
    Crazyflie via a USB cable.

    This object is intended to be used as an asynchronous context manager as
    follows::

        device = await CfUsb.detect_one()
        async with device as usb:
            await usb.send_bytes(b"1234")
            await usb.receive_bytes()

    In the context, a background thread is running and managing the low-level
    communication with the device; you may call `usb.send_bytes()` and
    `usb.receive_bytes()` to send raw bytes to and receive raw bytes from the
    device. The thread is terminated when the execution exits the context.

    If there is another thread that is already using the same device, the
    context blocks upon entering until the device becomes available.
    """

    _handle: Optional[USBDevice]

    @classmethod
    async def detect_all(cls):
        """Creates a list of low-level driver objects by scanning the USB buses
        for a suitable USB dongle.
        """
        return await to_thread.run_sync(_find_devices)

    @classmethod
    async def detect_one(cls, *, index: int = 0):
        """Creates a low-level driver object by scanning the USB buses for a
        suitable USB dongle, and selecting one based on the provided device
        index.

        Parameters:
            index: the index of the USB device to select if multiple dongles
                are connected

        Raises:
            IndexError: if there is no such device with the given index
        """
        devices = await cls.detect_all()
        try:
            device = devices[index]
        except IndexError:
            raise NotFoundError() from None
        return cls(device)

    def __init__(self, device: USBDevice, crtp_to_usb: bool = False):
        """Constructor.

        Creates a low-level driver object that uses the specified USB device.

        Parameters:
            device: the USB device to use
        """
        self._device = device
        self._in_queue = StapledObjectStream(*create_memory_object_stream(256))
        self._use_crtp_to_usb = bool(crtp_to_usb)

        self._receiver_thread_context = ThreadContext.create_reader(
            self._receive_bytes,
            self._in_queue,
            setup=self._configure_device,
            teardown=self._teardown_device,
        )
        self._sender_thread_context = ThreadContext.create_worker()

        self._exit_stack = None  # type: Optional[AsyncExitStack]
        self._version = None

    async def get_serial(self) -> str:
        """Returns the serial number of the associated device."""
        return await to_thread.run_sync(self._get_serial_sync)

    @property
    def use_crtp_to_usb(self):
        return self._use_crtp_to_usb

    @use_crtp_to_usb.setter
    def use_crtp_to_usb(self, value):
        self._use_crtp_to_usb = bool(value)

    @property
    def version(self) -> Optional[float]:
        """Returns the version number of the associated device.

        This property returns a valid value only after you have connected to
        the USB device.
        """
        return self._version

    async def __aenter__(self) -> "_CfUsbCommunicator":
        """Opens the driver object. This function must be called before you
        start using the driver.

        Starts an OS-level thread that will be responsible for managing
        communication over the given low-level device. Returns when the
        thread has been started.
        """
        self._version = None
        self._exit_stack = AsyncExitStack()

        stack = await self._exit_stack.__aenter__()
        await stack.enter_async_context(claim_device(self._device))
        receiver = await stack.enter_async_context(self._receiver_thread_context)
        sender = await stack.enter_async_context(self._sender_thread_context)

        sender = partial(sender, self._send_bytes)

        return _CfUsbCommunicator(sender, receiver)

    async def __aexit__(self, exc_type, exc_value, tb) -> bool:
        assert self._exit_stack is not None
        try:
            return await self._exit_stack.__aexit__(exc_type, exc_value, tb)
        finally:
            self._exit_stack = None

    def _configure_device(self):
        """Configures the USB device when the worker thread starts.

        This function is executed in the worker thread.
        """
        device = self._device

        if is_pyusb1:
            try:
                cfg = device.get_active_configuration()
            except USBError:
                cfg = None
            if cfg is None or cfg.bConfigurationValue != 1:
                device.set_configuration(1)
            handle = device
            version = float(
                "{0:x}.{1:x}".format(device.bcdDevice >> 8, device.bcdDevice & 0x0FF)
            )
        else:
            handle = device.open()
            handle.setConfiguration(1)
            handle.claimInterface(0)
            version = float(device.deviceVersion)

        self._handle, self._version = handle, version

        if self._use_crtp_to_usb:
            self._set_crtp_to_usb(True)

    def _get_serial_sync(self) -> str:
        """Retrieves the serial number of the given device in a synchronous manner."""
        # The signature for get_string has changed between versions to 1.0.0b1,
        # 1.0.0b2 and 1.0.0. Try the old signature first, if that fails try
        # the newer one.
        try:
            return usb.util.get_string(self._device, 255, self._device.iSerialNumber)
        except (USBError, ValueError):
            return usb.util.get_string(self._device, self._device.iSerialNumber)

    def _receive_bytes(self) -> Optional[bytes]:
        """Receives some data from the USB connection in a synchronous manner.

        Returns:
            the data that was received or `None` if no data was received during
            a short amount of time.

        Raises:
            IOError: when the Crazyflie was disconnected

        This function is executed in the worker thread.
        """
        assert self._handle is not None
        try:
            if is_pyusb1:
                return self._handle.read(0x81, 64, timeout=20)
            else:
                return self._handle.bulkRead(0x81, 64, 20)
        except USBError as e:
            try:
                if e.backend_error_code == -7 or e.backend_error_code == -116:
                    # Normal, the read was empty
                    pass
                else:
                    raise IOError("Crazyflie disconnected")
            except AttributeError:
                # pyusb < 1.0 doesn't implement getting the underlying error
                # number and it seems as if it's not possible to detect
                # if the cable is disconnected. So this detection is not
                # supported, but the "normal" case will work.
                pass

        return None

    def _send_bytes(self, data: bytes) -> None:
        """Sends some data via the USB connection in a synchronous manner.

        This function is executed in the worker thread.

        Raises:
            IOError: when the Crazyflie was disconnected
        """
        assert self._handle is not None
        try:
            if is_pyusb1:
                self._handle.write(endpoint=1, data=data, timeout=20)
            else:
                self._handle.bulkWrite(1, data, 20)
        except USBError:
            raise IOError("Error while sending packet")

    def _teardown_device(self, exc_type, exc_value, tb):
        """Tears down the connection to the USB device when the worker thread
        exits.

        This function is executed in the worker thread.
        """
        try:
            if self._use_crtp_to_usb:
                self._set_crtp_to_usb(False)
        except Exception:
            # maybe the device was disconnected already?
            pass

        try:
            release_device(self._handle)
        finally:
            self._handle = None
            self._version = None

    def _set_crtp_to_usb(self, value):
        send_vendor_setup(self._handle, 0x01, 0x01, int(bool(value)))


class _CfUsbCommunicator:
    """Object that is returned when entering a CfUsb context and that allows
    us to send packets to and receive packets from the USB connection.

    This is an internal class; you do not need to construct it yourself.
    """

    def __init__(self, sender, receiver):
        """Constructor.

        Parameters:
            sender: an async function that can be used to send a request to the
                outbound worker thread to send some data and wait for its result
            receiver: an async function that can be used to send a request to the
                inbound worker thread to receive the next packet
        """
        self._sender = sender
        self._receiver = receiver

    async def send_bytes(self, data: bytes) -> None:
        """Sends some bytes to the connected Crazyflie via the USB port.

        Parameters:
            data: the data to send to the Crazyflie

        Raises:
            IOError: if the request queue to the USB outbound thread is full or
                when the Crazyflie was disconnected
        """
        try:
            return await self._sender(data)
        except Full:
            raise IOError("Request queue to USB outbound thread is full")

    async def receive_bytes(self) -> bytes:
        """Receives some bytes from the connected Crazyflie via the USB port.

        Returns:
            the data that was received. It is guaranteed to have at least one
            byte in it.

        Raises:
            IOError: when the Crazyflie was disconnected
        """
        while True:
            data = await self._receiver()
            if data:
                return data


async def test():
    device = await CfUsb.detect_one()
    device.use_crtp_to_usb = True
    async with device as usb:
        # \xfd\x01 sends a "get version" command to the link control port
        await usb.send_bytes(b"\xfd\x01")
        data = await usb.receive_bytes()
        print("Received:", data)


if __name__ == "__main__":
    import trio

    trio.run(test)
