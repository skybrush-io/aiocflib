"""Middleware that logs all incoming and outgoing packets of a CRTP driver
instance.
"""

from aiocflib.crtp.crtpstack import CRTPPacket
from hexdump import dump, hexdump

from .base import MiddlewareBase
from .registry import register


try:
    from colorama import init, Fore, Back, Style

    _has_colors = True
except ImportError:

    class Unstyled:
        def __getattr__(self, attr):
            return ""

    Fore = Back = Style = Unstyled()

    def init():
        pass

    _has_colors = False


@register("log")
class LoggingMiddleware(MiddlewareBase):
    """Middleware that logs all incoming and outgoing packets of a CRTP driver
    instance.
    """

    _in = Fore.CYAN + Style.BRIGHT + "\u25c0" + Style.RESET_ALL
    _out = Fore.CYAN + "\u25b6" + Style.RESET_ALL
    _unsafe_in = Fore.RED + Style.BRIGHT + "\u25c0" + Style.RESET_ALL
    _unsafe_out = Fore.RED + "\u25b6" + Style.RESET_ALL

    def _format_type(self, packet: CRTPPacket) -> str:
        port, channel = packet.port, packet.channel
        try:
            code = port.code
        except Exception:
            code = str(port)
        return "{0:3}:{1}".format(code, channel)

    @staticmethod
    def _hexdump(data: bytes) -> str:
        result = []
        for line in hexdump(data, result="generator"):
            _, _, line = line.partition(" ")
            result.append(line)
        return "\n             ".join(result)

    def _init(self) -> None:
        from aiocflib.crtp.drivers.radio import RadioDriver
        from aiocflib.crtp.drivers.sitl import SITLDriver
        from aiocflib.crtp.drivers.usb import USBDriver

        self._num_null_packets = 0
        driver = self._wrapped

        if isinstance(driver, USBDriver):
            abbreviation = "usb{0}".format(driver.index or "?")
        elif isinstance(driver, RadioDriver):
            address = driver.address
            abbreviation = dump(address[-2:], sep="") if address else "rdio"
        elif isinstance(driver, SITLDriver):
            abbreviation = "sitl"
        else:
            abbreviation = "????"

        abbreviation = Fore.MAGENTA + abbreviation + Style.RESET_ALL
        self._abbreviation = "{0:4}".format(abbreviation)

    def _report_null_packets(self) -> None:
        """Reports on the console that a certain number of null packets have
        been received.

        Parameters:
            bool: whether the link is currently safe
        """
        if self._num_null_packets == 1:
            print(
                "{0} {1} {2}Null packet{3}".format(
                    self._abbreviation,
                    self._in if self.is_safe else self._unsafe_in,
                    Style.DIM,
                    Style.RESET_ALL,
                )
            )
        elif self._num_null_packets > 1:
            print(
                "{0} {1} {2}{3} null packets{4}".format(
                    self._abbreviation,
                    self._in if self.is_safe else self._unsafe_in,
                    Style.DIM,
                    self._num_null_packets,
                    Style.RESET_ALL,
                )
            )
        self._num_null_packets = 0

    async def receive_packet(self) -> CRTPPacket:
        packet = await super().receive_packet()

        if packet.is_null:
            self._num_null_packets += 1
        else:
            if self._num_null_packets:
                self._report_null_packets()
            data = self._hexdump(packet.data)
            type = Fore.GREEN + self._format_type(packet) + Style.RESET_ALL
            print(
                "{0} {1} {2} {3}".format(
                    self._abbreviation,
                    self._in if self.is_safe else self._unsafe_in,
                    type,
                    data,
                )
            )

        return packet

    async def send_packet(self, packet: CRTPPacket):
        type = Fore.GREEN + self._format_type(packet) + Style.RESET_ALL
        data = self._hexdump(packet.data)
        print(
            "{0} {1} {2} {3}".format(
                self._abbreviation,
                self._out if self.is_safe else self._unsafe_out,
                type,
                data,
            )
        )
        return await super().send_packet(packet)
