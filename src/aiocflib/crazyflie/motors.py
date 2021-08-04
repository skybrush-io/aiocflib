"""Classes related to sending motor-related commands to a Crazyflie."""

from anyio import sleep
from typing import Iterable, Optional

from .crazyflie import Crazyflie

__all__ = ("Motors",)


class Motors:
    """Class representing the motors of a Crazyflie instance."""

    _crazyflie: Crazyflie

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle motor-related
                messages
        """
        self._crazyflie = crazyflie

    async def test(
        self,
        indices: Optional[Iterable[int]] = None,
        power: int = 20000,
        duration: float = 1,
        delay: float = 1,
    ) -> None:
        """Tests the motors by spinning them up and down.

        Parameters:
            indices: the 1-based indices of the motors to test; `None` means all
                the motors
            power: the power value to send to the motors; must be an unsigned
                integer in the range [0; 65535]. The default will spin up the
                motor but it will not spin fast enough to cause any harm on a
                standard Crazyflie.
            duration: the duration of the motor test, in seconds
            delay: the delay between consecutive tests, in seconds
        """
        cf = self._crazyflie
        if indices is None:
            indices = (1, 2, 3, 4)

        async with cf.parameters.set_and_restore("motorPowerSet.enable", 1, 0):
            for index, motor_index in enumerate(indices):
                param_name = f"motorPowerSet.m{index + 1}"
                if index > 0:
                    await sleep(delay)
                await cf.parameters.set(param_name, power)
                await sleep(duration)
                await cf.parameters.set(param_name, 0)
