from __future__ import annotations

from anyio import sleep
from typing import Optional

from aiocflib.bootloader.types import BootloaderCommand, BootloaderTargetType
from aiocflib.crtp import CRTPDispatcher, CRTPDevice, CRTPDriver, CRTPPacket, CRTPPort
from aiocflib.utils.concurrency import ObservableValue
from aiocflib.utils.toc import TOCCache, TOCCacheLike

__all__ = ("Crazyflie",)

MYPY = False
if MYPY:
    from .console import Console
    from .log import Log
    from .mem import Memory
    from .param import Parameters
    from .platform import Platform


class Crazyflie(CRTPDevice):
    """Objects representing a single Crazyflie device.

    This object should be used as a context manager; the methods of this object
    that communicate with the Crazyflie must only be called within the context
    established by the instance, e.g.::

        async with Crazyflie(uri) as cf:
            # ...do anything with the Crazyflie here...
            pass
        # Connection to the Crazyflie closes when the context is exited
    """

    def __init__(self, uri: str, cache: Optional[TOCCacheLike] = None):
        """Constructor.

        Creates a Crazyflie_ instance from a URI specification.

        Parameters:
            uri: the URI where the Crazyflie can be reached
        """
        super().__init__(uri)

        self._cache = TOCCache.create(cache) if cache else None

        # Initialize sub-modules; avoid circular import
        from .console import Console
        from .log import Log
        from .mem import Memory
        from .param import Parameters
        from .platform import Platform

        self._console = Console(self)
        self._log = Log(self)
        self._memory = Memory(self)
        self._parameters = Parameters(self)
        self._platform = Platform(self)

    def _get_cache_for(self, namespace: str) -> Optional[TOCCache]:
        """Returns a namespaced TOC cache instance to be used by submodules
        for caching data, or `None` if the Crazyflie instance was constructed
        without a cache.
        """
        return self._cache.namespace(namespace) if self._cache else None

    async def _prepare_link(self, driver: CRTPDriver) -> None:
        await driver.use_safe_link()

    @property
    def console(self) -> Console:
        """The console message handler module of the Crazyflie."""
        return self._console

    @property
    def dispatcher(self) -> CRTPDispatcher:
        """Returns the packet dispatcher that dispatches incoming messages to
        the appropriate handler functions.

        You may then use the dispatcher to register handler functions to the
        messages you are interested in.
        """
        return self._dispatcher

    @property
    def link_quality(self) -> ObservableValue[float]:
        """Returns an observable link quality measure from the underlying
        link.
        """
        return (
            self._driver.link_quality if self._driver else ObservableValue.constant(0.0)
        )

    @property
    def log(self) -> Log:
        """The logging subsystem of the Crazyflie."""
        return self._log

    @property
    def mem(self) -> Memory:
        """The memory subsystem of the Crazyflie. This is a compatibility alias
        of ``self.memory`` for sake of compatibility with the official
        Crazyflie library.
        """
        return self._memory

    @property
    def memory(self) -> Memory:
        """The memory subsystem of the Crazyflie."""
        return self._memory

    @property
    def param(self) -> Parameters:
        """The parameters subsystem of the Crazyflie. This is a compatibility
        alias of ``self.parameters`` for sake of compatibility with the official
        Crazyflie library.
        """
        return self._parameters

    @property
    def parameters(self) -> Parameters:
        """The parameters subsystem of the Crazyflie."""
        return self._parameters

    @property
    def platform(self) -> Platform:
        """The platform-related message handler module of the Crazyflie."""
        return self._platform

    @property
    def uri(self):
        """The URI where the Crazyflie resides."""
        return self._uri

    async def reboot(self, to_bootloader: bool = False) -> None:
        """Sends a packet to the Crazyflie that reboots its main processor."""
        # Initiate the reset and wait for the acknowledgment
        # TODO(ntamas): resend if needed!
        await self.run_command(
            port=CRTPPort.LINK_CONTROL,
            channel=3,
            command=(BootloaderTargetType.NRF51, BootloaderCommand.RESET_INIT),
        )

        # Acknowledgment received, now we can send the reset command
        packet = CRTPPacket(
            port=CRTPPort.LINK_CONTROL,
            channel=3,
            data=(BootloaderTargetType.NRF51, BootloaderCommand.RESET, 1),
        )
        await self._driver.send_packet(packet)

        # Notify the driver that the Crazyflie was rebooted
        await self._driver.notify_rebooted()

        # Give some time for the outbound thread to send the packet
        await sleep(0.1)

    async def reboot_to_bootloader(self) -> None:
        """Sends a packet to the Crazyflie that reboots its main processor
        into its bootloader.

        It is advised that you close the connection context to the Crazyflie
        after this operation.
        """
        # Initiate the reset and wait for the acknowledgment
        # TODO(ntamas): resend if needed!
        await self.run_command(
            port=CRTPPort.LINK_CONTROL,
            channel=3,
            command=(BootloaderTargetType.NRF51, BootloaderCommand.RESET_INIT),
        )

        # Acknowledgment received, now we can send the reset command
        packet = CRTPPacket(
            port=CRTPPort.LINK_CONTROL,
            channel=3,
            data=(BootloaderTargetType.NRF51, BootloaderCommand.RESET, 0),
        )
        await self._driver.send_packet(packet)

        # Give some time for the outbound thread to send the packet
        await sleep(0.1)

    async def resume(self) -> None:
        """Sends a packet to the Crazyflie that wakes up its main processor
        from a suspended state.
        """
        packet = CRTPPacket(
            header=0xFF, data=(BootloaderTargetType.NRF51, BootloaderCommand.RESUME)
        )
        await self._driver.send_packet(packet)

        # Give some time for the outbound thread to send the packet
        await sleep(0.1)

        # Notify the driver that the Crazyflie was rebooted
        await self._driver.notify_rebooted()

    async def suspend(self) -> None:
        """Sends a packet to the Crazyflie that suspends its main processor.
        You can wake up the Crazyflie later with ``self.resume()`` or with the
        power button.

        You are advised to close the Crazyflie context after executing this
        method and re-establish it later. This is to ensure that the safe-link
        mode is restored properly after the Crazyflie wakes up.
        """
        packet = CRTPPacket(
            header=0xFF, data=(BootloaderTargetType.NRF51, BootloaderCommand.SUSPEND)
        )
        await self._driver.send_packet(packet)

        # Give some time for the outbound thread to send the packet
        await sleep(0.1)

    async def shutdown(self) -> None:
        """Sends a packet to the Crazyflie that turns it off completely."""
        packet = CRTPPacket(
            header=0xFF, data=(BootloaderTargetType.NRF51, BootloaderCommand.SHUTDOWN)
        )
        await self._driver.send_packet(packet)

        # Give some time for the outbound thread to send the packet
        await sleep(0.1)


async def test():
    from aiocflib.crtp import MemoryType
    from aiocflib.utils import timing

    uri = "radio+log://0/80/2M/E7E7E7E704"
    async with Crazyflie(uri, cache="/tmp/cfcache") as cf:
        print("Firmware version:", await cf.platform.get_firmware_version())
        print("Protocol version:", await cf.platform.get_protocol_version())
        print("Device type:", await cf.platform.get_device_type_name())

        with timing("Fetching log TOC"):
            await cf.log.validate()
        with timing("Fetching parameters TOC"):
            await cf.parameters.validate()

        with timing("Reading from memory"):
            memory = await cf.memory.find(MemoryType.LED)
            data = b"\xfc\x00" * 8
            await memory.write(0, data)
            await memory.read(0, len(data))

        """
        await cf.parameters.set("motorPowerSet.enable", 1)
        for var in "m1 m2 m3 m4".split():
            await cf.parameters.set("motorPowerSet." + var, 20000)
            await sleep(1.5)
            await cf.parameters.set("motorPowerSet." + var, 0)
            await sleep(2.5)
        await cf.parameters.set("motorPowerSet.enable", 0)
        """


if __name__ == "__main__":
    from aiocflib.crtp import init_drivers
    import anyio

    init_drivers()
    anyio.run(test)
