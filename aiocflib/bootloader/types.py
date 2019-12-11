"""Enumeration types related to the bootloader of the Crazyflie."""

from enum import IntEnum

__all__ = ("BootloaderProtocolVersion", "BootloaderTargetType", "BootloaderTarget")


class BootloaderCommand(IntEnum):
    """Enum describing the commands that the bootloader handles."""

    SHUTDOWN = 0x01
    SUSPEND = 0x02
    RESUME = 0x03
    GET_BATTERY_VOLTAGE = 0x04
    RESET = 0xF0
    RESET_INIT = 0xFF


class BootloaderProtocolVersion(IntEnum):
    CF1_V0 = 0x00
    CF1_V1 = 0x01
    CF2 = 0x10
    UNKNOWN = 0xFF

    @property
    def description(self) -> str:
        """Returns a human-readable description of the bootloader protocol
        version.
        """
        return _bootloader_protocol_descriptions.get(self, "Unknown")

    @property
    def is_cf2(self) -> bool:
        """Returns whether this protocol version corresponds to a Crazyflie
        2.0 instance.
        """
        return self is BootloaderProtocolVersion.CF2


_bootloader_protocol_descriptions = {
    BootloaderProtocolVersion.CF1_V0: "Crazyflie Nano Quadcopter (1.0)",
    BootloaderProtocolVersion.CF1_V1: "Crazyflie Nano Quadcopter (1.0)",
    BootloaderProtocolVersion.CF2: "Crazyflie 2.0",
}


class BootloaderTargetType(IntEnum):
    """Enum representing the CPU targets that the bootloader can flash with a
    new firmware.
    """

    NRF51 = 0xFE
    STM32 = 0xFF

    @property
    def description(self) -> str:
        """Returns a human-readable description of the CPU target."""
        return _target_descriptions.get(self, "Unknown")

    @classmethod
    def from_string(cls, name):
        for key, value in _target_descriptions.items():
            if value == name:
                return key

        raise ValueError("no such bootloader target: {0!r}".format(name))


_target_descriptions = {
    BootloaderTargetType.NRF51: "nRF51",
    BootloaderTargetType.STM32: "STM32",
}


class BootloaderTarget:
    """Simple value class representing a flashing target for the Crazyflie
    bootloader.

    Attributes:
        id: the identifier of the flashing target in the bootloader
        protocol_version: the bootloader protocol version to use
        page_size: the page size of the flashing process, in bytes
        buffer_pages: number of pages in the flashing buffer
        flash_pages: number of pages in the flash memory where we can write
        start_page: the first page where we can write the firmware
        cpu_id: the CPU ID of the target CPU
    """

    def __init__(self, id: BootloaderTargetType):
        """Constructor."""
        self.id = id
        self.protocol_version = BootloaderProtocolVersion.UNKNOWN
        self.page_size = 0  # type: int
        self.buffer_pages = 0  # type: int
        self.flash_pages = 0  # type: int
        self.start_page = 0  # type: int
        self.cpu_id = b""
        self.data = None

    def __str__(self):
        result = [
            "Target info: {0} (0x{:X})".format(self.id.description, self.id),
            "Flash pages: {0.flash_pages} | Page size: {0.page_size} | "
            "Buffer pages: {0.buffer_pages} | Start page: {0.start_page}".format(self),
            "{0.flash_size_in_kbytes} KBytes of flash available for firmware image.".format(
                self
            ),
        ]
        return "\n".join(result)

    @property
    def flash_size(self):
        """Returns the size of the flash memory available for the firmware image
        on this target, in bytes.
        """
        return (self.flash_pages - self.start_page) * self.page_size

    @property
    def flash_size_in_bytes(self):
        """Returns the size of the flash memory available for the firmware image
        on this target, in KBytes.
        """
        return self.flash_size / 1024
