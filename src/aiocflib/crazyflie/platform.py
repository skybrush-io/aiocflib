"""Classes related to handling platform service messages of a Crazyflie."""

from enum import IntEnum
from typing import Any, Dict, Optional

from aiocflib.crtp import CRTPPort, LinkControlChannel

from .crazyflie import Crazyflie

__all__ = ("Platform",)


class PlatformChannel(IntEnum):
    """Enum representing the names of the channels of the platform service in
    the CRTP protocol.
    """

    PLATFORM_COMMAND = 0
    VERSION_COMMAND = 1
    APP_CHANNEL = 2


class PlatformCommand(IntEnum):
    """Enum representig the names of the platform commands in the platform
    service of the CRTP protocol.
    """

    SET_CONTINUOUS_WAVE = 0


class VersionCommand(IntEnum):
    """Enum representig the names of the version commands in the platform
    service of the CRTP protocol.
    """

    GET_PROTOCOL_VERSION = 0
    GET_FIRMWARE_VERSION = 1
    GET_DEVICE_TYPE_NAME = 2


class Platform:
    """Class representing the handler of platform service messages for a Crazyflie
    instance.
    """

    _crazyflie: Crazyflie
    _cache: Dict[str, Any]

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle the platform
                service messages
        """
        self._crazyflie = crazyflie

        self._cache = {}
        # TODO(ntamas): clear cache when the Crazyflie disconnects

    async def get_device_type_name(self) -> Optional[str]:
        """Returns the device type name of the Crazyflie; `None` if the connected
        device is not a Crazyflie.
        """
        if "device_type_name" not in self._cache:
            self._cache["device_type_name"] = await self._get_device_type_name()
        return self._cache["device_type_name"]

    async def _get_device_type_name(self) -> Optional[str]:
        if await self.is_crazyflie():
            response = await self._crazyflie.run_command(
                port=CRTPPort.PLATFORM,
                channel=PlatformChannel.VERSION_COMMAND,
                command=VersionCommand.GET_DEVICE_TYPE_NAME,
            )
            return response.decode("ISO-8859-1", errors="backslashreplace")
        else:
            return None

    async def get_firmware_revision(self) -> Optional[str]:
        """Returns the firmware revision of the Crazyflie; `None` if the connected
        device is not a Crazyflie.
        """
        if "firmware_revision" not in self._cache:
            self._cache["firmware_revision"] = await self._get_firmware_revision()
        return self._cache["firmware_revision"]

    async def get_firmware_version(self) -> Optional[str]:
        """Returns the firmware version of the Crazyflie; `None` if the connected
        device is not a Crazyflie.
        """
        if "firmware_version" not in self._cache:
            self._cache["firmware_version"] = await self._get_firmware_version()
        return self._cache["firmware_version"]

    async def _get_firmware_revision(self) -> Optional[str]:
        if await self.is_crazyflie():
            rev0 = await self._crazyflie.param.get("firmware.revision0")
            rev1 = await self._crazyflie.param.get("firmware.revision1")
            return f"{rev0:08x}{rev1:04x}"
        else:
            return None

    async def _get_firmware_version(self) -> Optional[str]:
        if await self.is_crazyflie():
            response = await self._crazyflie.run_command(
                port=CRTPPort.PLATFORM,
                channel=PlatformChannel.VERSION_COMMAND,
                command=VersionCommand.GET_FIRMWARE_VERSION,
            )
            return response.decode("ISO-8859-1", errors="backslashreplace")
        else:
            return None

    async def get_protocol_version(self) -> int:
        """Returns the protocol version of the Crazyflie; -1 if the connected
        device is not a Crazyflie or it is too old and cannot supply the
        protocol version.
        """
        if "protocol_version" not in self._cache:
            self._cache["protocol_version"] = await self._get_protocol_version()
        return self._cache["protocol_version"]

    async def _get_protocol_version(self) -> int:
        # TODO(ntamas): cache locally, clear cache when the Crazyflie
        # disconnects
        if await self.is_crazyflie():
            response = await self._crazyflie.run_command(
                port=CRTPPort.PLATFORM,
                channel=PlatformChannel.VERSION_COMMAND,
                command=VersionCommand.GET_PROTOCOL_VERSION,
            )
            return response[0] if response else -1
        else:
            return -1

    async def is_crazyflie(self) -> bool:
        """Determines whether the connected device is a Crazyflie.

        Returns:
            whether the connected device is a Crazyflie
        """
        if "is_crazyflie" not in self._cache:
            self._cache["is_crazyflie"] = await self._is_crazyflie()
        return self._cache["is_crazyflie"]

    async def _is_crazyflie(self) -> bool:
        response = await self._crazyflie.run_command(
            port=CRTPPort.LINK_CONTROL, channel=LinkControlChannel.SOURCE, data=b"\x00"
        )
        return response.startswith(b"Bitcraze Crazyflie")

    async def packets(self):
        """Async generator that yields platform service messages from a
        Crazyflie.
        """
        async for packet in self._crazyflie.packets(port=CRTPPort.PLATFORM):
            yield packet
