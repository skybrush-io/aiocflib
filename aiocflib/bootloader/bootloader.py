from aiocflib.crtp import CRTPDevice, CRTPPort
from aiocflib.errors import DetectionFailed
from aiocflib.drivers.crazyradio import Crazyradio
from typing import List

from .types import (
    BootloaderCommand,
    BootloaderProtocolVersion,
    BootloaderTarget,
    BootloaderTargetType,
)

__all__ = ("Bootloader",)


class Bootloader(CRTPDevice):
    """Objects representing a single Crazyflie device when it is in bootloader
    mode.

    This object should be used as a context manager; the methods of this object
    that communicate with the bootloader must only be called within the context
    established by the instance, e.g.::

        async with Bootloader(uri) as cf:
            # ...do anything with the bootloader here...
            pass
        # Connection to the bootloader closes when the context is exited
    """

    @classmethod
    async def detect_all(cls) -> List[str]:
        """Uses all connected Crazyradio dongles to scan for available Crazyflie
        quadcopters that are in bootloader mode, and returns a list with
        appropriate connection URIs that could be used to connect to them.

        TODO(ntamas): can this be used to find Crazyflies that are in bootloader
        mode after a warm (not cold) reboot?

        Returns:
            the list of connection URIs where a bootloader was detected
        """
        devices = await Crazyradio.detect_all()
        results = []
        for index, device in enumerate(devices):
            # TODO(ntamas): faster, parallel scan for multiple radios?
            async with device as radio:
                items = await radio.scan(["radio://0/0", "radio://0/110"])
                results.extend(item.to_uri(index) for item in items)
        return results

    @classmethod
    async def detect_one(cls) -> str:
        """Uses all connected Crazyradio dongles to scan for available Crazyflie
        quadcopters that are in bootloader mode, finds the first one and returns
        the URI that can be used to connect to it.

        TODO(ntamas): can this be used to find Crazyflies that are in bootloader
        mode after a warm (not cold) reboot?

        Returns:
            the URI of a single Crazyflie in bootloader mode

        Raises:
        """
        devices = await Crazyradio.detect_all()

        for index, device in enumerate(devices):
            # TODO(ntamas): faster, parallel scan for multiple radios?
            async with device as radio:
                result = await radio.scan(["radio://0/0", "radio://0/110"])
                if result:
                    return result[0].to_uri(index)

        raise DetectionFailed()

    async def get_targets(self) -> List[BootloaderTarget]:
        """Returns information about the possible bootloader targets."""
        result = [await self._get_target_info(BootloaderTargetType.STM32)]

        if result[-1].protocol_version == BootloaderProtocolVersion.CF2:
            # On the CF2 we also have an NRF32 target
            result.append(await self._get_target_info(BootloaderTargetType.NRF51))

        return result

    async def run_bootloader_command(self, **kwds) -> bytes:
        """Shortcut to ``self.run_command()`` with the CRTP port and channel
        set to the values that make sure that the bootloader will process the
        command.

        The bootloader responds only to packets sent on the CRTP link control
        port, channel 3.
        """
        return await self.run_command(port=CRTPPort.LINK_CONTROL, channel=3, **kwds)

    async def _get_target_info(
        self, target_type: BootloaderTargetType
    ) -> BootloaderTarget:
        response = await self.run_bootloader_command(
            command=(target_type, BootloaderCommand.GET_BOOTLOADER_TARGET_INFO),
            timeout=0.1,
            retries=10,
        )
        return BootloaderTarget.from_bytes(target_type, response)


async def test():
    uri = (await Bootloader.detect_one()).replace("://", "+log://")
    async with Bootloader(uri) as bl:
        targets = await bl.get_targets()
        for target in targets:
            print(target)


if __name__ == "__main__":
    from aiocflib.crtp import init_drivers
    import trio

    init_drivers()
    trio.run(test)
