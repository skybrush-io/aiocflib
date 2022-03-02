from __future__ import annotations

from anyio import sleep
from binascii import hexlify
from typing import Optional, TYPE_CHECKING

from aiocflib.bootloader.types import BootloaderCommand
from aiocflib.bootloader.target import BootloaderTargetType
from aiocflib.crtp import (
    CRTPDispatcher,
    CRTPDevice,
    CRTPDriver,
    CRTPPort,
    LinkControlChannel,
)
from aiocflib.errors import TimeoutError
from aiocflib.utils.concurrency import ObservableValue
from aiocflib.utils.toc import TOCCache, TOCCacheLike

__all__ = ("Crazyflie",)

if TYPE_CHECKING:
    from .commander import Commander
    from .console import Console
    from .high_level_commander import HighLevelCommander
    from .led_ring import LEDRing
    from .lighthouse import Lighthouse
    from .localization import Localization
    from .log import Log
    from .mem import Memory
    from .motors import Motors
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

    @classmethod
    async def is_responding_at(cls, uri: str, *, tries: int = 1) -> bool:
        """Returns whether a Crazyflie drone is responding at the given URI.

        Parameters:
            uri: the URI to try to connect to
            tries: specifies how many times we should try to connect to the
                radio URI
        """
        instance = cls(uri)

        while tries > 0:
            try:
                async with instance:
                    while tries > 0:
                        try:
                            name = await instance.platform.get_device_type_name()
                            if name is None or not name:
                                return False
                            else:
                                return name.startswith("Crazyflie")
                        except TimeoutError:
                            tries -= 1
            except Exception:
                tries -= 1

        return False

    def __init__(self, uri: str, cache: Optional[TOCCacheLike] = None):
        """Constructor.

        Creates a Crazyflie_ instance from a URI specification.

        Parameters:
            uri: the URI where the Crazyflie can be reached
        """
        super().__init__(uri)

        self._cache = TOCCache.create(cache) if cache else None

        # Initialize sub-modules; avoid circular import
        from .app_channel import AppChannel
        from .commander import Commander
        from .console import Console
        from .high_level_commander import HighLevelCommander
        from .led_ring import LEDRing
        from .lighthouse import Lighthouse
        from .localization import Localization
        from .log import Log
        from .mem import Memory
        from .motors import Motors
        from .param import Parameters
        from .platform import Platform

        self._app_channel = AppChannel(self)
        self._commander = Commander(self)
        self._console = Console(self)
        self._high_level_commander = HighLevelCommander(self)
        self._led_ring = LEDRing(self)
        self._lighthouse = Lighthouse(self)
        self._localization = Localization(self)
        self._log = Log(self)
        self._memory = Memory(self)
        self._motors = Motors(self)
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
    def commander(self) -> Commander:
        """The low-level (roll-pitch-yaw-thrust) commander module of the
        Crazyflie.
        """
        return self._commander

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
    def high_level_commander(self) -> HighLevelCommander:
        """The high-level commander module of the Crazyflie."""
        return self._high_level_commander

    @property
    def led_ring(self) -> LEDRing:
        """The LED ring of the Crazyflie."""
        return self._led_ring

    @property
    def link_quality(self) -> ObservableValue[float]:
        """Returns an observable link quality measure from the underlying
        link.
        """
        return (
            self._driver.link_quality if self._driver else ObservableValue.constant(0.0)
        )

    @property
    def lighthouse(self) -> Lighthouse:
        """The Lighthouse subsystem of the Crazyflie."""
        return self._lighthouse

    @property
    def localization(self) -> Localization:
        """The localization subsystem of the Crazyflie."""
        return self._localization

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
    def motors(self) -> Motors:
        """The motors subsystem of the Crazyflie."""
        return self._motors

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

    async def _reboot(self, to_bootloader: bool = False) -> bytes:
        """Implementation of the common parts of ``reboot()`` and
        ``reboot_to_bootloader()``.
        """
        # Initiate the reset and wait for the acknowledgment
        response = await self.run_command(
            port=CRTPPort.LINK_CONTROL,
            channel=LinkControlChannel.BOOTLOADER,
            command=(BootloaderTargetType.NRF51, BootloaderCommand.RESET_INIT),
            timeout=1,
            attempts=5,
        )

        # Store the new address from the response -- this will be used if we
        # are rebooting to bootloader mode
        address = b"\xb1" + response[3::-1]

        # Acknowledgment received, now we can send the reset command.
        await self.send_packet(
            port=CRTPPort.LINK_CONTROL,
            channel=LinkControlChannel.BOOTLOADER,
            data=(
                BootloaderTargetType.NRF51,
                BootloaderCommand.RESET,
                0 if to_bootloader else 1,
            ),
        )

        return address

    async def reboot(self, to_bootloader: bool = False) -> None:
        """Sends a packet to the Crazyflie that reboots its main processor."""
        await self._reboot()
        if self._driver:
            await self._driver.notify_rebooted()

        # Give some time for the outbound thread to send the packet
        await sleep(0.1)

    async def reboot_to_bootloader(self) -> str:
        """Sends a packet to the Crazyflie that reboots its main processor
        into its bootloader.

        It is advised that you close the connection context to the Crazyflie
        after this operation.

        Note that when warm-booting to the bootloader, the Crazyflie will use
        a different address (constructed from the first four bytes of its CPU
        ID and a fixed prefix) than the one that was used while in firmware
        mode. This function will return a new radio-based connection URI that
        can be used to re-connect to the bootloader.

        Returns:
            a new connection URI that can be used to re-connect to the bootloader
        """
        new_address_bytes = await self._reboot(to_bootloader=True)

        # Give some time for the outbound thread to send the packet
        await sleep(0.1)

        # Construct the new URI
        new_address = hexlify(new_address_bytes).decode("ascii").upper()
        scheme, _, _ = self.uri.partition("://")
        if not scheme.startswith("radio"):
            scheme = "radio"
        return "{0}://0/0/2M/{1}".format(scheme, new_address)

    async def resume(self) -> None:
        """Sends a packet to the Crazyflie that wakes up its main processor
        from a suspended state.
        """
        await self.send_packet(
            port=CRTPPort.LINK_CONTROL,
            channel=LinkControlChannel.BOOTLOADER,
            data=(BootloaderTargetType.NRF51, BootloaderCommand.RESUME),
        )

        # Give some time for the outbound thread to send the packet
        await sleep(0.1)

        # Notify the driver that the Crazyflie was rebooted
        if self._driver:
            await self._driver.notify_rebooted()

    async def suspend(self) -> None:
        """Sends a packet to the Crazyflie that suspends its main processor.
        You can wake up the Crazyflie later with ``self.resume()`` or with the
        power button.

        You are advised to close the Crazyflie context after executing this
        method and re-establish it later. This is to ensure that the safe-link
        mode is restored properly after the Crazyflie wakes up.
        """
        await self.send_packet(
            port=CRTPPort.LINK_CONTROL,
            channel=LinkControlChannel.BOOTLOADER,
            data=(BootloaderTargetType.NRF51, BootloaderCommand.SUSPEND),
        )

        # Give some time for the outbound thread to send the packet
        await sleep(0.1)

    async def shutdown(self) -> None:
        """Sends a packet to the Crazyflie that turns it off completely."""
        await self.send_packet(
            port=CRTPPort.LINK_CONTROL,
            channel=LinkControlChannel.BOOTLOADER,
            data=(BootloaderTargetType.NRF51, BootloaderCommand.SHUTDOWN),
        )

        # Give some time for the outbound thread to send the packet
        await sleep(0.1)


async def test():
    from aiocflib.crtp import MemoryType
    from aiocflib.utils import timing

    logging = False

    # uri = "cppradio://0/80/2M/E7E7E7E701"
    uri = "radio://0/80/2M/E7E7E7E701"
    # uri = "sitl://"
    # uri = "usb://0"

    if logging:
        uri = uri.replace("://", "+log://")

    async with Crazyflie(uri, cache="/tmp/cfcache") as cf:
        print("Firmware version:", await cf.platform.get_firmware_version())
        print("Protocol version:", await cf.platform.get_protocol_version())
        print("Device type:", await cf.platform.get_device_type_name())

        with timing("Fetching log TOC"):
            await cf.log.validate()
        with timing("Fetching parameters TOC"):
            await cf.parameters.validate()

        async with cf.parameters.set_and_restore("ring.effect", 6, 0):
            await sleep(1)

        with timing("Reading from memory"):
            memory = await cf.memory.find(MemoryType.LED)
            data = b"\xfc\x00" * 8
            await memory.write(0, data)
            await memory.read(0, len(data))

        with timing("Writing to memory with checksum"):
            await cf.memory.write_with_checksum(
                MemoryType.LED, 0, b"\xde\xad\xbe\xef", only_if_changed=True
            )
            await cf.memory.write_with_checksum(
                MemoryType.LED, 0, b"\xde\xad\xbe\xef", only_if_changed=True
            )

        await cf.led_ring.flash()

        async with cf.parameters.set_and_restore("ring.effect", 6, 0):
            persistence_state = await cf.parameters.get_persistence_state("ring.effect")
            if persistence_state.is_stored:
                await cf.parameters.clear_persisted_value("ring.effect")

            persistence_state = await cf.parameters.get_persistence_state("ring.effect")
            if persistence_state.is_stored:
                raise RuntimeError("ring.effect should not be persisted")

            await cf.parameters.persist("ring.effect")

            persistence_state = await cf.parameters.get_persistence_state("ring.effect")
            if not persistence_state.is_stored:
                raise RuntimeError("ring.effect should be persisted now")
            if persistence_state.stored_value != 6:
                raise RuntimeError("ring.effect persisted value should be 6")

            await cf.parameters.clear_persisted_value("ring.effect")
            persistence_state = await cf.parameters.get_persistence_state("ring.effect")
            if persistence_state.is_stored:
                raise RuntimeError("ring.effect should not be persisted")

        """
        session = cf.log.create_session()
        session.create_block(
            "pm.vbat",
            "stateEstimate.x",
            "stateEstimate.y",
            "stateEstimate.z",
            frequency=5,
        )

        async with session:
            with move_on_after(3):
                await session.process_messages()
        """


if __name__ == "__main__":
    from aiocflib.crtp import init_drivers
    import trio

    init_drivers()
    try:
        trio.run(test)
    except KeyboardInterrupt:
        pass
