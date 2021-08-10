from aiocflib.crtp import CRTPDevice, CRTPPort, LinkControlChannel
from aiocflib.errors import NotFoundError
from aiocflib.drivers.crazyradio import Crazyradio
from aiocflib.utils.addressing import BootloaderAddressSpace
from anyio import sleep
from typing import List, Optional, Union

from .target import BootloaderTarget, BootloaderTargetType
from .types import BootloaderCommand, BootloaderProtocolVersion

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

    _targets: Optional[List[BootloaderTarget]]

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

        results: List[str] = []
        for index, device in enumerate(devices):
            address_space = BootloaderAddressSpace(index=index)
            async with device as radio:
                items = await radio.scan(address_space)
                results.extend(item.to_uri(index) for item in items)

        return results

    @classmethod
    async def detect_one(cls, *, tries: int = 1) -> str:
        """Uses all connected Crazyradio dongles to scan for available Crazyflie
        quadcopters that are in bootloader mode, finds the first one and returns
        the URI that can be used to connect to it.

        Parameters:
            tries: specifies how many times we should try to connect to a single
                radio URI

        Returns:
            the URI of a single Crazyflie in bootloader mode
        """
        devices = await Crazyradio.detect_all()

        for index, device in enumerate(devices):
            address_space = BootloaderAddressSpace(index=index)
            for uri in address_space:
                found = await cls.is_responding_at(uri, tries=tries)
                if found:
                    return uri

        raise NotFoundError()

    @classmethod
    async def is_responding_at(cls, uri: str, *, tries: int = 1) -> bool:
        """Returns whether the bootloader is responding at the given radio URI.

        Parameters:
            uri: the radio URI, typically ``radio://X/0`` or ``radio://X/110``,
                where X is the index of the Crazyradio device
            tries: specifies how many times we should try to connect to the
                radio URI
        """
        device = await Crazyradio.from_uri(uri)

        async with device as radio:
            while tries > 0:
                result = await radio.scan([uri])
                if result:
                    return True
                tries -= 1

        return False

    def __init__(self, uri: str):
        """Constructor.

        Creates a Bootloader_ instance from a URI specification.

        Parameters:
            uri: the URI where the bootloader can be reached
        """
        super().__init__(uri)

        self._targets = None

    async def find_target(
        self, type: Union[BootloaderTargetType, str]
    ) -> BootloaderTarget:
        """Finds the first bootloader target of the given type.

        Raises:
            NotFoundError: if there is no such bootloader target
        """
        type = BootloaderTargetType.from_string(type)
        targets = await self.get_targets()
        for target in targets:
            if target.id == type:
                return target
        raise NotFoundError("no such bootloader target")

    async def get_targets(self) -> List[BootloaderTarget]:
        """Returns information about the possible bootloader targets. Loads it
        from the bootloader if necessary.
        """
        await self.validate()
        assert self._targets is not None
        return self._targets

    async def _reboot(self, to_firmware: bool = False) -> None:
        """Implementation of the common parts of ``reboot()`` and
        ``reboot_to_firmware()``.
        """
        # Initiate the reset and wait for the acknowledgment
        await self.run_bootloader_command(
            command=(BootloaderTargetType.NRF51, BootloaderCommand.RESET_INIT)
        )

        # Acknowledgment received, now we can send the reset command.
        await self.send_bootloader_packet(
            data=(
                BootloaderTargetType.NRF51,
                BootloaderCommand.RESET,
                1 if to_firmware else 0,
            )
        )

    async def reboot(self, to_firmware: bool = False) -> None:
        """Sends a packet to the bootloader that reboots it."""
        await self._reboot()

        # Give some time for the outbound thread to send the packet
        await sleep(0.1)

    async def reboot_to_firmware(self) -> None:
        """Sends a packet to the bootloader that reboots the main processor
        of the device into the uploaded firmware.

        It is advised that you close the connection context to the bootloader
        after this operation.
        """
        await self._reboot(to_firmware=True)

        # Give some time for the outbound thread to send the packet
        await sleep(0.1)

    async def run_bootloader_command(self, **kwds) -> bytes:
        """Shortcut to ``self.run_command()`` with the CRTP port and channel
        set to the values that make sure that the bootloader will process the
        command.

        The bootloader responds only to packets sent on the CRTP link control
        port, channel 3.
        """
        new_kwds = dict(timeout=1, attempts=5)
        new_kwds.update(kwds)
        return await self.run_command(
            port=CRTPPort.LINK_CONTROL,
            channel=LinkControlChannel.BOOTLOADER,
            **new_kwds
        )

    async def send_bootloader_packet(self, data, **kwds) -> None:
        """Shortcut to ``self.send_packet()`` with the CRTP port and channel
        set to the values that make sure that the bootloader will process the
        command.

        The bootloader responds only to packets sent on the CRTP link control
        port, channel 3.
        """
        return await self.send_packet(
            port=CRTPPort.LINK_CONTROL,
            channel=LinkControlChannel.BOOTLOADER,
            data=data,
            **kwds
        )

    async def validate(self) -> None:
        """Ensures that the information about the flashing targets of the
        bootloader is already retrieved.
        """
        if self._targets is None:
            self._targets = await self._get_targets()

    async def _get_target_info(
        self, target_type: BootloaderTargetType
    ) -> BootloaderTarget:
        response = await self.run_bootloader_command(
            command=(target_type, BootloaderCommand.GET_TARGET_INFO)
        )
        return BootloaderTarget.from_bytes(self, target_type, response)

    async def _get_targets(self) -> List[BootloaderTarget]:
        """Loads information about the possible bootloader targets from the
        bootloader.
        """
        result = [await self._get_target_info(BootloaderTargetType.STM32)]

        if result[-1].protocol_version == BootloaderProtocolVersion.CF2:
            # On the CF2 we also have an NRF32 target
            result.append(await self._get_target_info(BootloaderTargetType.NRF51))

        return result


async def test():
    from aiocflib.crazyflie import Crazyflie
    from tqdm import tqdm

    uri = "radio://0/80/2M/E7E7E7E704"
    async with Crazyflie(uri) as cf:
        uri = await cf.reboot_to_bootloader()

    async with Bootloader(uri) as bl:
        target = await bl.find_target("stm32")
        firmware = open("cf2.bin", "rb").read()

        with tqdm(desc="Flashing", total=len(firmware), unit=" bytes") as progress:
            await target.write_firmware("cf2.bin", on_progress=progress)

        await bl.reboot_to_firmware()


if __name__ == "__main__":
    from aiocflib.crtp import init_drivers
    from anyio import run

    init_drivers()
    run(test)
