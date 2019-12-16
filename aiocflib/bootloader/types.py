"""Enumeration types related to the bootloader of the Crazyflie."""

from enum import IntEnum
from typing import Callable

__all__ = ("BootloaderProtocolVersion",)


class BootloaderCommand(IntEnum):
    """Enum describing the commands that the Crazyflie or its bootloader
    handles on the LINK_CONTROL CRTP port, channel 3 (bootloader).

    Commands 0x10 - 0x1F are responded to only when the Crazyflie is in
    bootloader mode. All other commands are responded to only when the
    Crazyflie is in firmware mode.
    """

    SHUTDOWN = 0x01
    SUSPEND = 0x02
    RESUME = 0x03
    GET_BATTERY_VOLTAGE = 0x04

    GET_TARGET_INFO = 0x10
    SET_ADDRESS = 0x11
    GET_MAPPING = 0x12
    LOAD_BUFFER = 0x14
    READ_BUFFER = 0x15
    WRITE_FLASH = 0x18
    READ_FLASH = 0x1C

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


#: Type alias for progress handler functions in BootloaderTarget_
ProgressHandler = Callable[[float], None]
