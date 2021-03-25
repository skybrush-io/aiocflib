"""Middleware that logs all incoming and outgoing packets of a CRTP driver
instance.
"""

from aiocflib.crtp.crtpstack import CRTPPacket, CRTPPort
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


#: Underlined style extension for colorama
UNDERLINED = "\033[4m"


@register("log")
class LoggingMiddleware(MiddlewareBase):
    """Middleware that logs all incoming and outgoing packets of a CRTP driver
    instance.
    """

    _in = Fore.CYAN + Style.BRIGHT + "\u25c0" + Style.RESET_ALL
    _out = Fore.CYAN + "\u25b6" + Style.RESET_ALL
    _unsafe_in = Fore.RED + Style.BRIGHT + "\u25c0" + Style.RESET_ALL
    _unsafe_out = Fore.RED + "\u25b6" + Style.RESET_ALL

    @staticmethod
    def _format_type(packet: CRTPPacket) -> str:
        """Formats the port and channel of the given CRTP packet in the format
        that will be used in the log.
        """
        port, channel = packet.port, packet.channel
        try:
            code = port.code
        except Exception:
            code = str(port)
        return "{0:3}:{1}".format(code, channel)

    @staticmethod
    def _has_command_byte(packet: CRTPPacket) -> bool:
        """Returns whether the first byte of the data of the given CRTP packet
        is to be interpreted as a command byte (which is a common pattern in
        many CRTP services and channels).

        Parameters:
            packet: the CRTP packet to test

        Returns:
            whether the CRTP packet has a command byte in the first byte of the
            data payload
        """
        if len(packet.data) < 1:
            return False

        if packet.port == CRTPPort.LOGGING:
            return packet.channel in (0, 1)
        elif packet.port == CRTPPort.PARAMETERS:
            return packet.channel in (0, 3)
        elif packet.port == CRTPPort.MEMORY:
            return packet.channel == 0
        elif packet.port == CRTPPort.LOCALIZATION:
            return packet.channel == 1
        elif packet.port == CRTPPort.GENERIC_COMMANDER:
            return packet.channel == 0
        elif packet.port == CRTPPort.HIGH_LEVEL_COMMANDER:
            return packet.channel == 0
        elif packet.port == CRTPPort.PLATFORM:
            return packet.channel == 1
        elif packet.port == CRTPPort.DEBUG:
            return packet.channel == 0

        return False

    @staticmethod
    def _hexdump(data: bytes) -> str:
        """Creates a hex dump from the given data.

        Parameters:
            data: the data to format as hex dump

        Returns:
            the formatted hex dump, indented properly for the log
        """
        result = []
        for line in hexdump(data, result="generator"):
            _, _, line = line.partition(" ")
            result.append(line)
        return "\n             ".join(result)

    def _init(self) -> None:
        from aiocflib.crtp.drivers.cpplink import CppRadioDriver
        from aiocflib.crtp.drivers.radio import RadioDriver
        from aiocflib.crtp.drivers.sitl import SITLDriver
        from aiocflib.crtp.drivers.usb import USBDriver

        self._num_null_packets = 0
        driver = self._wrapped

        if isinstance(driver, USBDriver):
            index = driver.index
            abbreviation = "usb{0}".format(index if index is not None else "?")
        elif isinstance(driver, RadioDriver):
            address = driver.address
            abbreviation = dump(address[-2:], sep="") if address else "rdio"
        elif isinstance(driver, CppRadioDriver):
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
            if self._has_command_byte(packet):
                data = Fore.YELLOW + UNDERLINED + data[:2] + Style.RESET_ALL + data[2:]
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
        if self._num_null_packets:
            self._report_null_packets()
        type = Fore.GREEN + self._format_type(packet) + Style.RESET_ALL
        data = self._hexdump(packet.data)
        if self._has_command_byte(packet):
            data = Fore.YELLOW + UNDERLINED + data[:2] + Style.RESET_ALL + data[2:]
        print(
            "{0} {1} {2} {3}".format(
                self._abbreviation,
                self._out if self.is_safe else self._unsafe_out,
                type,
                data,
            )
        )
        return await super().send_packet(packet)
