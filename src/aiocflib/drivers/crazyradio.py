"""Asynchronous USB driver for the Crazyradio USB dongle."""

from aiocflib.utils.usb import (
    claim_device,
    find_devices,
    get_vendor_setup,
    is_pyusb1,
    release_device,
    send_vendor_setup,
    USBDevice,
    USBError,
)
from anyio import Lock, to_thread
from array import array
from binascii import hexlify
from contextlib import asynccontextmanager, AsyncExitStack
from enum import IntEnum
from functools import total_ordering, wraps
from sys import exc_info
from typing import Iterable, List, Optional, Tuple, Union

from aiocflib.utils.addressing import (
    CrazyradioAddress,
    CrazyradioAddressLike,
    CrazyradioDataRate,
    DEFAULT_RADIO_ADDRESS as DEFAULT_ADDRESS,
    parse_radio_uri,
    to_radio_address,
)
from aiocflib.utils.concurrency import Full, ThreadContext
from aiocflib.errors import NotFoundError

__author__ = "CollMot Robotics Ltd"
__all__ = ("Crazyradio", "DEFAULT_ADDRESS")


CRADIO_VID = 0x1915
CRADIO_PID = 0x7777


class CrazyradioConfigurationRequest(IntEnum):
    """Enum representing the configuration requests supported by the radio.

    See http://wiki.bitcraze.se/projects:crazyradio:protocol for documentation
    """

    SET_RADIO_CHANNEL = 0x01
    SET_RADIO_ADDRESS = 0x02
    SET_DATA_RATE = 0x03
    SET_RADIO_POWER = 0x04
    SET_RADIO_ARD = 0x05
    SET_RADIO_ARC = 0x06
    ACK_ENABLE = 0x10
    SET_CONT_CARRIER = 0x20
    SCAN_CHANNELS = 0x21
    LAUNCH_BOOTLOADER = 0xFF


class CrazyradioPower(IntEnum):
    """Enum representing the power levels supported by the radio."""

    P_M18DBM = 0
    P_M12DBM = 1
    P_M6DBM = 2
    P_0DBM = 3


def _find_devices(serial: Optional[str] = None) -> List[USBDevice]:
    """Returns a list of Crazyradio dongles currently connected to the computer.

    This function uses `pyusb` functions directly, hence it will block the
    calling thread. You must call it in a separate worker thread when you use
    it in conjunction with an asyncio framework.

    Parameters:
        serial: when specified, returns only those devices that match the given
            serial number. (Typically there will be only a single device).

    Returns:
        the list of Crazyradio dongles
    """
    candidates = find_devices(vid=CRADIO_VID, pid=CRADIO_PID)
    if serial is not None:
        candidates = [dev for dev in candidates if dev.serial_number == serial]
    return candidates


def get_serials() -> Tuple[str]:
    """Returns the serial numbers of all the connected Crazyradio dongles.

    Returns:
        the serial numbers of all the connected Crazyradio dongles
    """
    return tuple(dev.serial_number for dev in _find_devices())


class Acknowledgment:
    """Simple value object representing an acknowledgment from the radio."""

    @classmethod
    def from_array(cls, data: array, arc: int):
        """Constructs an acknowledgment from the raw bytes received from the
        radio via the USB connection.

        Parameters:
            data: the byte array that was received
            arc: the value of the ACK retry count in the connection; used when
                the input indicates that the packet was not acknowledged
        """
        result = cls()
        if data[0] != 0:
            result.ack = bool(data[0] & 0x01)
            result.power_detector_status = bool(data[0] & 0x02)
            result.retry_count = data[0] >> 4
            result.data = bytes(data[1:])
        else:
            result.retry_count = arc
        return result

    def __init__(
        self,
        data: bytes = b"",
        *,
        ack: bool = False,
        power_detector_status: bool = False,
        retry_count: int = 0,
    ):
        self.ack = ack
        self.power_detector_status = power_detector_status
        self.retry_count = retry_count
        self.data = data

    def __repr__(self) -> str:
        return (
            "{0.__class__.__name__}({0.data!r}, ack={0.ack!r}, "
            "power_detector_status={0.power_detector_status!r}, "
            "retry_count={0.retry_count!r})"
        ).format(self)


@total_ordering
class RadioConfiguration:
    """Simple value class that contains the commonly used configuration variables
    of a Crazyradio instance that we expect the user to provide when sending
    packets.

    This class is also used to specify a single configuration of a radio scan
    for reachable devices.
    """

    @classmethod
    def ensure(cls, value: "RadioConfigurationLike"):
        """When the input is a string, converts it into a RadioConfiguration_
        object, assuming that it contains a ``radio://`` URI. When the input
        is a RadioConfiguration_, it returns the object intact.

        Raises:
            TypeError: if the input is neither a string nor a RadioConfiguration
        """
        if isinstance(value, str):
            return cls.from_uri(value)
        elif isinstance(value, cls):
            return value
        else:
            raise TypeError("expected string or {0}, got {1!r}", cls.__name__, value)

    @classmethod
    def from_uri(cls, uri: str):
        """Creates a RadioConfiguration_ object from a ``radio://`` URI.

        The scheme of the supplied URI is ignored; it is assumed that the rest
        of the URI follows the format used for ``radio://`` URIs, and that the
        first path component is the radio index (which will also be ignored).
        """
        parts = parse_radio_uri(uri)
        del parts["index"]
        return cls(**parts)

    def __init__(
        self,
        *,
        address: Optional[CrazyradioAddressLike] = None,
        channel: Optional[int] = None,
        data_rate: CrazyradioDataRate = CrazyradioDataRate.DR_2MPS,
        scheme: str = "radio",
    ):
        """Constructor.

        Parameters:
            address: the address to use for sending packets
            data_rate: the data rate to use for sending packets
            channel: the channel to use for sending packets; `None` is usable
                for radio channel scans where we want to specify that the
                scan will happen on all channels. `None` is not allowed if the
                object is used for declaring where packets should be sent.
            scheme: the URI scheme to use when converting the configuration to
                a URI
        """
        self._address = Crazyradio.to_address(address) if address is not None else None
        self._channel = channel
        self._data_rate = data_rate
        self._scheme = str(scheme)

    @property
    def address(self) -> Optional[CrazyradioAddress]:
        """The address to use when sending packets."""
        return self._address

    @property
    def channel(self) -> Optional[int]:
        """The channel to use when sending packets."""
        return self._channel

    @property
    def data_rate(self) -> CrazyradioDataRate:
        """The data rate to use when sending packets."""
        return self._data_rate

    @property
    def is_full(self) -> bool:
        """Returns whether the configuration is fully specified."""
        return (
            self._address is not None
            and self._channel is not None
            and self._data_rate is not None
            and self._scheme is not None
        )

    @property
    def scheme(self) -> str:
        """The URI scheme to use when converting this configuration to a URI."""
        return self._scheme

    def replace(
        self,
        address: Optional[CrazyradioAddressLike] = None,
        data_rate: Optional[CrazyradioDataRate] = None,
        channel: Optional[int] = None,
        scheme: Optional[str] = None,
    ):
        """Replaces the address, the data rate and/or the channel in the
        configuration object and returns a new configuration object.
        """
        return self.__class__(
            address=address if address is not None else self._address,
            data_rate=data_rate if data_rate is not None else self._data_rate,
            channel=channel if channel is not None else self._channel,
            scheme=scheme if scheme is not None else self._scheme,
        )

    def to_uri(self, index: int = 0) -> str:
        """Converts the radio configuration to a string URI that can be
        passed to a CRTPDevice_ constructor.
        """
        parts = ["{0}://{1}".format(self.scheme, index)]
        if self.channel is not None:
            parts.append(str(self.channel))
            if self.data_rate is not None:
                parts.append(str(self.data_rate))
                if self.address is not None:
                    parts.append(hexlify(self.address).upper().decode("ascii"))
        return "/".join(parts)

    def __eq__(self, other):
        return (self._data_rate, self._channel, self._address, self._scheme) == (
            other._data_rate,
            other._channel,
            other._address,
            other._scheme,
        )

    def __lt__(self, other):
        # Order is important here: we want RadioConfiguration objects to be
        # sortable in a way that same data rates are clustered together. This
        # is because it is faster to switch addresses than channels or data
        # rates, and we want the radio scans to be as fast as possible.
        return (self._data_rate, self._channel, self._address, self._scheme) < (
            other._data_rate,
            other._channel,
            other._address,
            other._scheme,
        )

    def __repr__(self):
        return (
            "{0.__class__.__name__}(address={0.address!r}, "
            "channel={0.channel!r}, "
            "data_rate={0.data_rate!r}, "
            "scheme={0.scheme!r})"
        ).format(self)


#: Type specification for objects that can be converted into a RadioConfiguration
RadioConfigurationLike = Union[RadioConfiguration, str]


class Crazyradio:
    """Low-level driver object that is used for communication with the
    Crazyflie via a Crazyradio.

    This object is intended to be used as an asynchronous context manager as
    follows::

        device = await Crazyradio.detect_one()
        async with device as radio:
            response = await radio.send_and_receive_bytes(b"1234")
            if response and response.ack:
                print(repr(response.data))

    In the context, a background thread is running and managing the low-level
    communication with the device; you may call `radio.send_and_receive_bytes()`
    to send raw bytes to and receive raw bytes from the device. The thread is
    terminated when the execution exits the context.

    Note that you cannot receive data from the Crazyflie without sending some
    as the downstream is contained in the acknowledgment packets. If you need
    to receive data but you have nothing to say, send a CRTP null packet.

    If there is another thread that is already using the same device, the
    context blocks upon entering until the device becomes available.
    """

    @classmethod
    async def detect_all(cls):
        """Creates a list of low-level driver objects by scanning the USB buses
        for a suitable USB dongle.
        """
        devices = await to_thread.run_sync(_find_devices)
        return [cls(device) for device in devices]

    @classmethod
    async def detect_one(cls, *, index: int = 0):
        """Creates a low-level driver object by scanning the USB buses for a
        suitable USB dongle, and selecting one based on the provided device
        index.

        Parameters:
            index: the index of the USB device to select if multiple dongles
                are connected

        Raises:
            NotFoundError: if there is no such device with the given index
        """
        devices = await to_thread.run_sync(_find_devices)
        try:
            device = devices[index]
        except IndexError:
            raise NotFoundError() from None
        return cls(device)

    @classmethod
    async def from_uri(cls, uri: str):
        """Creates a low-level driver object from its URI representation.

        Only the device index is used from the URI; the remaining parts are
        ignored.
        """
        parts = parse_radio_uri(uri)
        return await cls.detect_one(index=parts["index"])

    to_address = to_radio_address

    def __init__(self, device: USBDevice):
        """Constructor.

        Creates a low-level driver object that uses the specified USB device.

        Parameters:
            device: the USB device to use
        """
        self._device = device

        self._arc = -1  # type: int
        self._current_address = None  # type: Optional[CrazyradioAddress]
        self._current_channel = None  # type: Optional[int]
        self._current_configuration = None  # type: Optional[RadioConfiguration]
        self._current_data_rate = None  # type: Optional[CrazyradioDataRate]
        self._version = None  # type: Optional[float]

        self._sender_thread_context = ThreadContext.create_worker(
            setup=self._configure_device, teardown=self._teardown_device
        )

        self._exit_stack = None  # type: Optional[AsyncExitStack]

    @property
    def version(self) -> Optional[float]:
        """Returns the version number of the associated device.

        This property returns a valid value only after you have connected to
        the USB device.
        """
        return self._version

    async def __aenter__(self):
        """Opens the driver object. This function must be called before you
        start using the driver.

        Starts an OS-level thread that will be responsible for managing
        communication over the given low-level device. Returns when the
        thread has been started.
        """
        assert self._exit_stack is None

        exit_stack = AsyncExitStack()

        try:
            await exit_stack.__aenter__()
            await exit_stack.enter_async_context(claim_device(self._device))
            sender = await exit_stack.enter_async_context(self._sender_thread_context)
            self._exit_stack = exit_stack
        finally:
            if self._exit_stack is None:
                await exit_stack.__aexit__(*exc_info())

        return _CfRadioCommunicator(sender, self)

    async def __aexit__(self, exc_type, exc_value, tb) -> bool:
        assert self._exit_stack is not None
        exit_stack = self._exit_stack
        self._exit_stack = None
        return await exit_stack.__aexit__(exc_type, exc_value, tb)

    def _configure_device(self):
        """Configures the USB device when the worker thread starts.

        This function is executed in the worker thread.
        """
        device = self._device

        self._current_configuration = None
        self._version = None

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

        if version < 0.3:
            raise RuntimeError("This driver requires Crazyradio firmware V0.3+")

        self._handle, self._version = handle, version

        # Reset the dongle to power up settings
        self._set_data_rate(CrazyradioDataRate.DR_2MPS)
        self._set_channel(2)
        self._arc = -1
        if version >= 0.4:
            self._set_cont_carrier(False)
            self._set_address(b"\xe7" * 5)
            self._set_power(CrazyradioPower.P_0DBM)
            self._set_arc(3)
            self._set_ard_bytes(32)
            self._set_ack_enable(True)

    def _teardown_device(self, exc_type, exc_value, tb):
        """Tears down the connection to the USB device when the worker thread
        exits.

        This function is executed in the worker thread.
        """
        self._current_address = None
        self._current_channel = None
        self._current_configuration = None
        self._current_data_rate = None
        self._version = None

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

    def _configure(self, configuration: RadioConfiguration) -> None:
        """Sets the address, channel and data rate of the radio in a single
        call.

        This function also caches the last configuration object it was called
        with. If the current configuration object is the same as the last one
        and there were no manual changes to the address, channel and data
        rates in the meanwhile with the appropriate methods, the configuration
        step will be skipped to save some USB bandwidth.
        """
        if configuration is not self._current_configuration:
            self._set_data_rate(configuration.data_rate)
            self._set_channel(configuration.channel)
            self._set_address(configuration.address)
            self._current_configuration = configuration

    def _has_fw_scan(self):
        """Returns whether the Crazyradio supports accelerated firmware-driven
        channel scans.
        """
        return self._version is not None and self._version >= 0.5

    def _set_ack_enable(self, enable: bool) -> None:
        """Sets whether acknowledgments are enabled on the radio."""
        send_vendor_setup(
            self._handle, CrazyradioConfigurationRequest.ACK_ENABLE, int(bool(enable))
        )

    def _set_address(self, address: CrazyradioAddress) -> None:
        """Sets the address that the radio uses when sending packets.

        Parameters:
            address: the radio address to set
        """
        if len(address) != 5:
            raise ValueError("the radio address must be 5 bytes long")

        if address != self._current_address:
            send_vendor_setup(
                self._handle,
                CrazyradioConfigurationRequest.SET_RADIO_ADDRESS,
                0,
                0,
                address,
            )
            self._current_address = address
            self._current_configuration = None

    def _set_arc(self, arc: int) -> None:
        """Sets the ACK retry count in a synchronous manner.

        Parameters:
            arc: the ACK retry count to use for subsequent packets
        """
        send_vendor_setup(
            self._handle, CrazyradioConfigurationRequest.SET_RADIO_ARC, arc
        )
        self._arc = arc

    def _set_ard_bytes(self, nbytes):
        """Sets the ACK retry delay time for radio communication, in
        terms of ACK payload bytes. The firmware will calculate and use the
        equivalent number of microseconds depending on the current data rate.

        Parameters:
            us: the ACK retry delay time to use for subsequent packets, in
                terms of ACK payload bytes
        """
        send_vendor_setup(
            self._handle, CrazyradioConfigurationRequest.SET_RADIO_ARD, 0x80 | nbytes
        )

    def _set_ard_time(self, us: int) -> None:
        """Sets the ACK retry delay time for radio communication, in
        microseconds.

        Parameters:
            us: the ACK retry delay time to use for subsequent packets, in
                microseconds
        """
        # Auto Retransmit Delay:
        # 0000 - Wait 250uS
        # 0001 - Wait 500uS
        # 0010 - Wait 750uS
        # ........
        # 1111 - Wait 4000uS

        # Round down, to value representing a multiple of 250uS
        t = max(min(int((us / 250) - 1), 0xF), 0)
        send_vendor_setup(self._handle, CrazyradioConfigurationRequest.SET_RADIO_ARD, t)

    def _set_channel(self, channel: int) -> None:
        """Sets the radio channel to be used in a synchronous manner.

        Parameters:
            channel: the channel on which subsequent packets will be sent
        """
        if channel < 0 or channel > 125:
            raise ValueError("Invalid channel: {0}".format(channel))
        if channel != self._current_channel:
            send_vendor_setup(
                self._handle, CrazyradioConfigurationRequest.SET_RADIO_CHANNEL, channel
            )
            self._current_channel = channel
            self._current_configuration = None

    def _set_cont_carrier(self, active):
        """Enables or disables continuous carrier mode on the radio.

        In continuous carrier mode the radio transmit a constant sine wave at
        the currently set frequency (channel) and power. This is a test mode
        that can affect other 2.4GHz devices. It should only be used in a lab
        for testing purposes.
        """
        send_vendor_setup(
            self._handle,
            CrazyradioConfigurationRequest.SET_CONT_CARRIER,
            int(bool(active)),
        )

    def _set_data_rate(self, data_rate: CrazyradioDataRate) -> None:
        """Sets the radio data rate to be used in a synchronous manner.

        Parameters:
            data_rate: the data rate to use for subsequent packets
        """
        if data_rate != self._current_data_rate:
            send_vendor_setup(
                self._handle, CrazyradioConfigurationRequest.SET_DATA_RATE, data_rate
            )
            self._current_data_rate = data_rate
            self._current_configuration = None

    def _set_power(self, power: CrazyradioPower) -> None:
        """Sets the radio power to be used in a synchronous manner.

        Parameters:
            power: the transmission power to use for subsequent packets
        """
        send_vendor_setup(
            self._handle, CrazyradioConfigurationRequest.SET_RADIO_POWER, power
        )

    def _scan(
        self,
        targets: Optional[List[RadioConfigurationLike]] = None,
        address: Optional[
            Union[CrazyradioAddressLike, Iterable[CrazyradioAddressLike]]
        ] = None,
        packet: bytes = b"\xff\xff\xff",
    ) -> List[RadioConfiguration]:
        """Scans a selected combination of channels and data rates to detect
        devices listening on these channels.

        Parameters:
            targets: items specifying the data rates and channels to scan, or
                strings that contain ``radio://`` URIs that can be converted
                into data rates and channels. When omitted, it defaults to all
                combinations of channels and data rates.
            address: Crazyradio address to use when sending packets. `None`
                means to use the current address. A single address means to use
                the given address. An iterable of addresses means to scan all
                specified addresses. Integers are mapped to the five byte
                long address E7E7E7E7XX where XX is replaced by the integer.
            packet: packet to send during testing; defaults to the null CRTP
                packet, repeated three times

        Returns:
            a list containing the targets where a device was detected
        """
        result = []

        # If no targets are given, scan all channels and all data rates
        if targets is None:
            targets = [
                RadioConfiguration(data_rate=data_rate)
                for data_rate in CrazyradioDataRate
            ]
        else:
            targets = [RadioConfiguration.ensure(target) for target in targets]

        # If an address is given, replace all address-less targets with the
        # given address. If multiple addresses are given, replace all address-less
        # targets with a combination of each such target and address
        if address is not None:
            addresses = (
                list(address)
                if not isinstance(address, (CrazyradioAddress, int))
                else [address]
            )
            addresses = [Crazyradio.to_address(address) for address in addresses]

            new_targets = []
            for target in targets:
                if target.address is None:
                    new_targets.extend(
                        target.replace(address=address) for address in addresses
                    )
                else:
                    new_targets.append(target)

            targets = new_targets
            address = None

        # Check whether the target list contains a mixture of address-less and
        # address-based targets as this is not allowed
        has_addresses = any(target.address is not None for target in targets)
        if has_addresses:
            if any(target.address is None for target in targets):
                raise ValueError(
                    "mixing address-less targets with address-based targets is not supported"
                )

        for target in sorted(targets):
            if has_addresses:
                self._set_address(target.address)

            self._set_data_rate(target.data_rate)

            if target.channel is None:
                matches = self._scan_channels(packet=packet)
                result.extend(target.replace(channel=channel) for channel in matches)
            else:
                self._set_channel(target.channel)
                status = self._send_and_receive_bytes(packet)
                if status and status.ack:
                    result.append(target)

        return result

    def _scan_channels(
        self, first: int = 0, last: int = 125, packet: bytes = b"\xff\xff\xff"
    ) -> List[int]:
        """Scans all channels in the given channel range to detect devices
        listening on these channels.

        If the radio supports it, the scan will be executed by the firmware,
        switching channels as necessary. If the radio does not support it, the
        scan will fall back to a slower USB-driven scan where we explicitly
        switch channels one by one.

        The data rate of the radio is left untouched during the scan.

        Parameters:
            first: first channel to test (inclusive)
            last: last channel to test (inclusive)
            packet: packet to send during testing; defaults to the null CRTP
                packet, repeated three times

        Returns:
            a list containing the indices of all the channels where a device
            was detected
        """

        if self._has_fw_scan():  # Fast firmware-driven scan
            self.current_channel = None
            self.current_address = None
            self.current_data_rate = None

            send_vendor_setup(
                self._handle,
                CrazyradioConfigurationRequest.SCAN_CHANNELS,
                first,
                last,
                packet,
                timeout=2000,  # apprently 1000 msec is not enough sometimes
            )
            result = list(
                get_vendor_setup(
                    self._handle, CrazyradioConfigurationRequest.SCAN_CHANNELS, 0, 0, 64
                )
            )

            # Workaround for USB bug, see Crazyradio issue #9
            if len(result) == 64:
                result.clear()
            return result

        else:  # Slow PC-driven scan
            result = []
            for i in range(first, last + 1):
                self._set_channel(i)
                status = self._send_and_receive_bytes(packet)
                if status and status.ack:
                    result.append(i)
            return result

    def _send_and_receive_bytes(self, data: bytes) -> Optional[Acknowledgment]:
        """Sends some data via the radio connection in a synchronous manner.

        This function is executed in the worker thread.

        Returns:
            the acknowledgment received from the radio, or `None` if no
            acknowledgment was received in time

        Raises:
            IOError: when the Crazyflie was disconnected
        """
        try:
            if is_pyusb1:
                self._handle.write(endpoint=1, data=data, timeout=1000)
                response = self._handle.read(0x81, 64, timeout=1000)
            else:
                self._handle.bulkWrite(1, data, 1000)
                response = self._handle.bulkRead(0x81, 64, 1000)
            return Acknowledgment.from_array(response, arc=self._arc)
        except USBError:
            return None

    def _configure_send_and_receive_bytes(
        self, configuration: RadioConfiguration, data: bytes
    ) -> Optional[Acknowledgment]:
        """Shortcut for a common use-case that arises frequently: configure the
        radio link, then send a single packet and wait for the acknowledgment.

        This function is executed in the worker thread.

        Returns:
            the acknowledgment received from the radio, or `None` if no
            acknowledgment was received in time

        Raises:
            IOError: when the Crazyflie was disconnected
        """
        self._configure(configuration)
        return self._send_and_receive_bytes(data)


class _CfRadioCommunicator:
    """Object that is returned when entering a Crazyradio context and that allows
    us to send packets to and receive packets from the radio connection.

    The object essentially proxies all relevant methods to the underlying
    Crazyradio_ object such that the methods are executed by the worker thread.

    This is an internal class; you do not need to construct it yourself.
    """

    def __init__(self, sender, radio: Crazyradio):
        """Constructor.

        Parameters:
            sender: an async function that can be used to send a method execution
                request to the outbound worker thread
            radio: the Crazyradio object that constructed this instance
        """

        self._configuration_lock = Lock()
        self._radio = radio
        self._sender = sender

        def create_proxy_for(name):
            target = getattr(radio, "_" + name)

            @wraps(target)
            async def proxy(*args, **kwds):
                try:
                    return await sender(target, *args, **kwds)
                except Full:
                    raise IOError("Request queue to radio outbound thread is full")

            return proxy

        methods = (
            "configure_send_and_receive_bytes",
            "scan",
            "scan_channels",
            "send_and_receive_bytes",
            "set_ack_enable",
            "set_arc",
            "set_ard_bytes",
            "set_ard_time",
            "set_cont_carrier",
            "set_power",
        )

        for name in methods:
            setattr(self, name, create_proxy_for(name))

    @asynccontextmanager
    async def configure(self, configuration: RadioConfiguration):
        """Configures the radio address, channel and data rate according to
        the given configuration object, and establishes a context. While the
        context is open, any other tasks trying to call `configure()` will
        block upon entering the context.

        This ensures that tasks do not step on each other's foot by modifying
        the address, channel or data rate without other tasks knowing about it.

        Parameters:
            configuration: the configuration to establish
        """
        async with self._configuration_lock:
            try:
                await self._sender(self._radio._configure, configuration)
            except Full:
                raise IOError("Request queue to radio outbound thread is full")
            yield


async def test():
    device = await Crazyradio.detect_one()
    async with device as radio:
        targets = await radio.scan(address=1)
        if not targets:
            print("No Crazyflie found")
        else:
            # \xfd\x01 sends a "get version" command to the link control port
            async with radio.configure(targets[0]):
                response = await radio.send_and_receive_bytes(b"\xfd\x01")
                print(repr(response))


if __name__ == "__main__":
    import trio

    trio.run(test)
