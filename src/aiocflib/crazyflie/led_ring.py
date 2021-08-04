"""Classes related to setting the colors and effects on the LED ring of a
Crazyflie.
"""

from anyio import sleep
from contextlib import asynccontextmanager
from enum import IntEnum
from typing import AsyncIterator, Optional, Union

from aiocflib.utils.colors import ColorLike, to_color

from .crazyflie import Crazyflie

__all__ = ("LEDRing", "LEDRingEffect")


class LEDRingEffect(IntEnum):
    BLACK = 0
    WHITE_SPIN = 1
    COLOR_SPIN = 2
    TILT = 3
    BRIGHTNESS = 4
    SPIN = 5
    DOUBLE_SPIN = 6
    SOLID_COLOR = 7
    LED_TEST = 8
    BATTERY_CHARGE = 9
    BOAT = 10
    SIREN = 11
    GRAVITY_LIGHT = 12
    VIRTUAL_MEM = 13
    FADE_COLOR = 14
    RSSI = 15
    LOCATION_SERVICE_STATUS = 16
    TIME_MEM = 17
    LIGHTHOUSE = 18


class LEDRing:
    """Class representing the LED ring of a Crazyflie instance."""

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle LED ring-related
                commands
        """
        self._crazyflie = crazyflie

    async def flash(self) -> None:
        """Instructs the LED ring to show a brief light signal to attract
        attention.
        """
        await self._crazyflie.parameters.set("system.highlight", 1)

    async def get_effect(self) -> Union[LEDRingEffect, int]:
        """Returns the current effect code of the LED ring.

        Returns:
            the current effect code as a member of the LEDRingEffect enum,
            if the effect code is known to the enum, otherwise as an integer
        """
        value = await self._crazyflie.parameters.get("ring.effect")
        value = int(value)
        try:
            return LEDRingEffect(value)
        except ValueError:
            return value

    async def is_installed(self) -> bool:
        """Returns whether the LED ring is installed on the Crazyflie."""
        await self._crazyflie.parameters.validate()
        return bool(await self._crazyflie.parameters.get("deck.bcLedRing"))

    async def set_color(self, color: ColorLike) -> None:
        """Sets a solid color on the LED ring.

        This command also switches the mode (effect) of the LED ring.
        """
        resolved_color = to_color(color)
        await self._crazyflie.parameters.set("ring.fadeTime", 0)
        await self._crazyflie.parameters.set("ring.fadeColor", resolved_color.rgb888)
        await self.set_effect(LEDRingEffect.FADE_COLOR)

    async def set_effect(self, effect: LEDRingEffect) -> None:
        """Switches the LED ring to the given light effect mode."""
        await self._crazyflie.parameters.set("ring.effect", effect)

    async def set_headlight(self, on: bool = True) -> None:
        """Turns the headlight on or off."""
        await self._crazyflie.parameters.set("ring.headlightEnable", 1 if on else 0)

    @asynccontextmanager
    async def set_effect_and_restore(
        self, effect: LEDRingEffect, old_effect: Optional[LEDRingEffect] = None
    ) -> AsyncIterator[None]:
        """Context manager that sets the LED ring effect to the given light
        effect when entering the context and restores it when exiting the context.
        """
        async with self._crazyflie.parameters.set_and_restore(
            "ring.effect", effect, old_effect
        ):
            yield

    async def test(self, duration: float = 2) -> None:
        """Tests the LED ring by sending it to testing mode for the given number
        of seconds.

        Parameters:
            duration: the duration of the test, in seconds
        """
        async with self.set_effect_and_restore(LEDRingEffect.LED_TEST):
            await sleep(duration)
        await self.set_headlight(False)

    async def turn_off(self) -> None:
        """Turns off the LED ring."""
        await self.set_effect(LEDRingEffect.BLACK)
        await self.set_headlight(False)

    @asynccontextmanager
    async def use(self) -> AsyncIterator[None]:
        """Context manager that turns off the LED ring when exiting the context."""
        try:
            yield
        finally:
            await self.turn_off()


async def test():
    from anyio import sleep

    uri = "radio+log://0/80/2M/E7E7E7E701"
    # uri = "sitl+log://"
    # uri = "usb+log://0"

    async with Crazyflie(uri, cache="/tmp/cfcache") as cf:
        installed = await cf.led_ring.is_installed()
        print(f"LED ring installed: {installed}")
        await sleep(1)

        async with cf.led_ring.use():
            print("Flashing LED ring...")
            await cf.led_ring.flash()
            await sleep(1)

            print("Testing LED ring...")
            await cf.led_ring.test()
            await sleep(1)

            print("Setting LED ring to solid colors...")
            await cf.led_ring.set_color("green")
            await sleep(1)
            await cf.led_ring.set_color((0, 127, 255))
            await sleep(1)
            await cf.led_ring.set_color("#ffcc00")
            await sleep(1)


if __name__ == "__main__":
    from aiocflib.crtp import init_drivers
    import trio

    init_drivers()
    try:
        trio.run(test)
    except KeyboardInterrupt:
        pass
