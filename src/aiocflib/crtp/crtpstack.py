"""Classes for modeling and handling CRTP packets."""

from anyio import create_memory_object_stream
from anyio.abc import ObjectReceiveStream
from array import array
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
from enum import IntEnum
from functools import partial
from inspect import iscoroutinefunction
from typing import (
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Union,
)

from aiocflib.utils.concurrency import AwaitableValue

__author__ = "CollMot Robotics Ltd"
__all__ = (
    "CRTPCommandLike",
    "CRTPDataLike",
    "CRTPDispatcher",
    "CRTPPacket",
    "CRTPPort",
    "CRTPPortLike",
    "LinkControlChannel",
)


#: Mapping from CRTP port names to short three-letter identifiers
_crtp_port_codes: List[str] = [
    "CON",
    "P01",
    "PRM",
    "CMD",
    "MEM",
    "LOG",
    "LOC",
    "GEN",
    "HLC",
    "P09",
    "P10",
    "P11",
    "P12",
    "PLT",
    "DBG",
    "LNK",
]


class CRTPPort(IntEnum):
    """Enum representing the available ports of the CRTP protocol."""

    CONSOLE = 0x00
    UNUSED_1 = 0x01
    PARAMETERS = 0x02
    COMMANDER = 0x03
    MEMORY = 0x04
    LOGGING = 0x05
    LOCALIZATION = 0x06
    GENERIC_COMMANDER = 0x07
    HIGH_LEVEL_COMMANDER = 0x08
    UNUSED_9 = 0x09
    UNUSED_10 = 0x0A
    UNUSED_11 = 0x0B
    UNUSED_12 = 0x0C
    PLATFORM = 0x0D
    DEBUG = 0x0E
    LINK_CONTROL = 0x0F

    @property
    def code(self) -> str:
        return _crtp_port_codes[int(self)]


class LinkControlChannel(IntEnum):
    """Enum representing the names of the link control channels in the link
    control service of the CRTP protocol.

    This is declared here instead of in a dedicated "link control" module
    because it is used in multiple places in the library (especially the
    bootloader channel).
    """

    ECHO = 0
    SOURCE = 1
    SINK = 2
    BOOTLOADER = 3


#: Type alias for objects that can be convered into a CRTP command byte
CRTPCommandLike = Union[int, bytes, Iterable[Union[int, bytes]]]

#: Type alias for objects that can be converted into the data of a CRTP packet
CRTPDataLike = Union[bytes, Sequence[int]]

#: Type alias for objects that can be converted into a CRTP port
CRTPPortLike = Union[int, CRTPPort]


class MemoryType(IntEnum):
    """Enum representing the types of memories supported by a Crazyflie."""

    I2C = 0
    ONE_WIRE = 1
    LED = 0x10
    LOCO = 0x11
    TRAJECTORY = 0x12
    LOCO2 = 0x13
    LIGHTHOUSE = 0x14
    TESTER = 0x15
    SD_CARD = 0x16
    LED_SEQUENCE = 0x17
    APP = 0x18
    DECK = 0x19

    @property
    def description(self) -> str:
        """Human-readable description of the memory type."""
        return _memory_type_descriptions.get(int(self), "Unknown")


_memory_type_descriptions: Dict[int, str] = {
    MemoryType.I2C: "I2C",
    MemoryType.ONE_WIRE: "1-wire",
    MemoryType.LED: "LED driver",
    MemoryType.LOCO: "Loco positioning",
    MemoryType.TRAJECTORY: "Trajectory",
    MemoryType.LOCO2: "Loco positioning 2",
    MemoryType.LIGHTHOUSE: "Lighthouse positioning",
    MemoryType.TESTER: "Memory tester",
    MemoryType.SD_CARD: "SD card",
    MemoryType.LED_SEQUENCE: "LED sequence",
    MemoryType.APP: "Application",
    MemoryType.DECK: "Deck",
}


class CRTPPacket:
    """A single packet that can be sent or received via a CRTP connection."""

    @classmethod
    def from_bytes(cls, data: CRTPDataLike):
        """Constructs a CRTP packet from its raw representation that includes
        the header _and_ the data itself.
        """
        if data is None:
            raise ValueError("data may not be empty")
        if isinstance(data, bytes):
            header = int(data[0])
        else:
            header = data[0]
        return cls(header=header, data=data[1:])

    @classmethod
    def null(cls):
        """Constructs a null CRTP packet."""
        return cls(header=0xFF)

    @classmethod
    def safe_link(cls, value: bool = True):
        """Constructs a special CRTP packet that turns safe link mode on or off.

        Parameters:
            value: whether the safe link mode should be enabled or disabled
        """
        return cls(header=0xFF, data=b"\x05\x01" if value else b"\x05\x00")

    def __init__(
        self,
        header: Optional[int] = None,
        data: Optional[CRTPDataLike] = None,
        *,
        port: Optional[CRTPPortLike] = None,
        channel: int = 0
    ):
        """Constructor.

        Parameters:
            header: the header of the packet. Takes precedence over `port` and
                `channel`.
            data: the data object of the packet
            port: when it is not `None`, and `header` is `None`, specifies the
                CRTP port of the packet
            channel: when `port` is not `None`, and `header` is `None`,
                specifies the CRTP channel of the packet
        """
        self._data = bytes()
        self._header = 0

        if header is not None:
            self.header = header
        elif port is not None:
            self.port = port
            self.channel = channel

        if data is not None:
            self.data = data

    @property
    def channel(self):
        """The channel index of the packet."""
        return self._header & 0x03

    @channel.setter
    def channel(self, value):
        self._header = (self._header & 0xFC) | (value & 0x03)

    @property
    def command(self) -> Optional[int]:
        """Returns the first byte of the data block of the CRTP packet. This
        byte is sometimes used as a command byte in some CRTP packets.
        """
        return self._data[0] if self._data else None

    @property
    def data(self) -> bytes:
        """Get the packet data as a raw immutable bytes object."""
        return self._data

    @data.setter
    def data(self, value) -> None:
        """Set the packet data"""
        if isinstance(value, bytes):
            self._data = value
        elif isinstance(value, str):
            self._data = value.encode("ISO-8859-1")
        elif isinstance(value, (array, bytearray, list, tuple)):
            self._data = bytes(value)
        elif value is None:
            self._data = bytes()
        else:
            raise TypeError(
                "Data must be None, bytes, bytearray, string, list or tuple,"
                " not {}".format(type(value))
            )

    @property
    def header(self) -> int:
        """Returns the header byte of the CRTP packet."""
        return self._header

    @header.setter
    def header(self, value: int):
        # The two bits in position 3 and 4 needs to be set for legacy
        # support of the bootloader, according to the official cflib library
        self._header = (value & 0xFF) | (0x3 << 2)

    @property
    def is_null(self) -> bool:
        """Returns whether this packet is a null packet."""
        return (self._header & 0xF3) == 0xF3 and not self._data

    @property
    def port(self) -> CRTPPort:
        """The CRTP port of the packet."""
        return CRTPPort((self._header & 0xF0) >> 4)

    @port.setter
    def port(self, value) -> None:
        value = int(value)
        self._header = (self._header & 0x0F) | ((value << 4) & 0xF0)

    def to_bytes(self, safelink_bits: int = 12) -> bytes:
        """Convers the packet to its raw byte-level representation."""
        return bytes(((self._header & 0xF3) | safelink_bits,)) + self._data

    __bytes__ = to_bytes

    def __repr__(self):
        """Returns a unique, machine-parseable representation of the packet."""
        return "{0.__class__.__name__}(header={0.header!r}, data={0.data!r})".format(
            self
        )

    def __str__(self):
        """Returns a human-readable string representation of the packet."""
        return "{0}:{1} {2}".format(self.port, self.channel, tuple(self.data))


#: Type alias for functions that can handle CRTP packets
CRTPPacketHandler = Union[
    Callable[[CRTPPacket], None], Callable[[CRTPPacket], Awaitable[None]]
]


class CRTPDispatcher:
    """Auxiliary data structure that allows one to associate queues to
    combinations of CRTP ports and channels, and to dispatch a CRTP packet to
    interested queues.
    """

    def __init__(self):
        """Constructor.

        Creates an empty dispatch table.
        """
        self._by_port_sync = defaultdict(list)
        self._by_port_async = defaultdict(list)

    @asynccontextmanager
    async def create_packet_queue(
        self, port: Optional[CRTPPortLike] = None, *, queue_size: int = 0
    ) -> AsyncIterator[ObjectReceiveStream]:
        """Context manager that creates a queue that will yield incoming
        CRTP packets coming from the given port.

        Parameters:
            port: the CRTP port to match; `None` means to match all CRTP ports
            queue_size: number of pending packets that may stay in the queue
                before blocking. Typically 0 is enough.

        Returns:
            a readable stream in which the dispatcher will place all packets
            that match the given port (or all ports if no port is specified)
        """
        tx_queue, rx_queue = create_memory_object_stream(queue_size)
        async with tx_queue:
            with self.registered(tx_queue.send, port=port):
                yield rx_queue

    async def dispatch(self, packet: CRTPPacket) -> None:
        """Handles an incoming CRTP packet and dispatches them to the
        appropriate listener functions.

        Right now this function may block the current task if an asynchronous
        listener is registered on the packet and the listener blocks. If you
        plan to do long-running operations in the listener, you should spawn a
        new task within the listener instead.
        """
        # Send the packet to the port-specific sync and async handlers
        for handler in self._by_port_sync[packet.port]:
            handler(packet)
        for handler in self._by_port_async[packet.port]:
            await handler(packet)

        # Send the packet to the "all packets" handlers
        for handler in self._by_port_sync[None]:
            handler(packet)
        for handler in self._by_port_async[None]:
            await handler(packet)

    def register(
        self, handler: CRTPPacketHandler, *, port: Optional[CRTPPortLike] = None
    ) -> Callable[[], None]:
        """Registers a handler to call for each incoming CRTP packet matching a
        given port.

        Parameters:
            handler: the handler to call. This may be a synchronous or an
                asynchronous function.
            port: the CRTP port that the incoming packet must have in order to
                match the handler. `None` means that any port will match.

        Returns:
            a disposer function that can be called when the handler must be
            deregistered
        """
        if port is not None:
            port = CRTPPort(port)
        if iscoroutinefunction(handler):
            handlers = self._by_port_async[port]
        else:
            handlers = self._by_port_sync[port]
        handlers.append(handler)
        return partial(handlers.remove, handler)

    @contextmanager
    def registered(
        self, handler: CRTPPacketHandler, *, port: Optional[CRTPPortLike] = None
    ):
        """Context manager that registers a given packet handler for a CRTP
        port when the context is entered and deregisters the handler when the
        context is exited.
        """
        disposer = self.register(handler, port=port)
        try:
            yield
        finally:
            disposer()

    @contextmanager
    def wait_for_next_packet(
        self,
        predicate: Optional[Callable[[CRTPPacket], bool]],
        *,
        port: Optional[CRTPPortLike] = None
    ) -> Iterator[AwaitableValue]:
        """Context manager that waits for a CRTP packet on the given port (or
        all ports) matching a given predicate while the execution is in the
        context.

        Returns:
            an awaitable value that resolves to the first CRTP packet matching
            the port number and the predicate
        """
        value: AwaitableValue[CRTPPacket] = AwaitableValue()

        if predicate:

            def matcher(packet: CRTPPacket) -> None:
                if predicate(packet):  # type: ignore
                    value.set(packet)

        else:
            matcher = value.set  # type: ignore

        with self.registered(matcher, port=port):
            yield value
