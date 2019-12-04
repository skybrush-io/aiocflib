"""Classes for modeling and handling CRTP packets."""

from anyio import create_queue, Queue
from array import array
from collections import defaultdict
from contextlib import contextmanager
from enum import IntEnum
from functools import partial
from inspect import iscoroutinefunction
from typing import Awaitable, Callable, List, Optional, Tuple, Union

from aiocflib.utils.concurrency import AwaitableValue

__author__ = "CollMot Robotics Ltd"
__all__ = ("CRTPDataLike", "CRTPDispatcher", "CRTPPacket", "CRTPPort", "CRTPPortLike")


class CRTPPort(IntEnum):
    """Enum representing the available ports of the CRTP protocol."""

    CONSOLE = 0x00
    UNUSED_1 = 0x01
    PARAM = 0x02
    COMMANDER = 0x03
    MEM = 0x04
    LOGGING = 0x05
    LOCALIZATION = 0x06
    COMMANDER_GENERIC = 0x07
    SETPOINT_HL = 0x08
    UNUSED_9 = 0x09
    UNUSED_10 = 0x0A
    UNUSED_11 = 0x0B
    UNUSED_12 = 0x0C
    PLATFORM = 0x0D
    DEBUGDRIVER = 0x0E
    LINKCTRL = 0x0F


#: Type alias for objects that can be converted into the data of a CRTP packet
CRTPDataLike = Union[array, bytearray, bytes, str, List[int], Tuple[int]]

#: Type alias for objects that can be converted into a CRTP port
CRTPPortLike = Union[int, CRTPPort]


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
        elif isinstance(data, str):
            header = ord(data[0])
        else:
            header = data[0]
        return cls(header=header, data=data[1:])

    @classmethod
    def null(cls):
        """Constructs a null CRTP packet."""
        return cls(header=0xFF)

    def __init__(
        self,
        header: Optional[int] = None,
        data: CRTPDataLike = None,
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
    def header(self) -> int:
        """Returns the header byte of the CRTP packet."""
        return self._header

    @header.setter
    def header(self, value: int):
        # The two bits in position 3 and 4 needs to be set for legacy
        # support of the bootloader, according to the official cflib library
        self._header = (value & 0xFF) | (0x3 << 2)

    @property
    def data(self) -> bytes:
        """Get the packet data as a raw immutable bytes object."""
        return self._data

    @data.setter
    def data(self, value: CRTPDataLike) -> None:
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
    def port(self) -> CRTPPort:
        """The CRTP port of the packet."""
        return CRTPPort((self._header & 0xF0) >> 4)

    @port.setter
    def port(self, value: Union[CRTPPort, int]) -> None:
        self._header = (self._header & 0x0F) | ((value << 4) & 0xF0)

    def to_bytes(self) -> bytes:
        """Convers the packet to its raw byte-level representation."""
        return bytes((self._header,)) + self._data

    __bytes__ = to_bytes

    def __repr__(self):
        """Returns a unique, machine-parseable representation of the packet."""
        return "{0.__class__.__name__}(header={0.header!r}, data={0.data!r})".format(
            self
        )

    def __str__(self):
        """Returns a human-readable string representation of the packet."""
        return "{0}:{1} {2}".format(self._port, self.channel, tuple(self.data))


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

    @contextmanager
    async def create_packet_queue(
        self, port: Optional[CRTPPortLike] = None, *, queue_size: int = 0
    ) -> Queue:
        """Context manager that creates a queue that will yield incoming
        CRTP packets coming from the given port.

        Parameters:
            port: the CRTP port to match; `None` means to match all CRTP ports
            queue_size: number of pending packets that may stay in the queue
                before blocking. Typically 0 is enough.

        Returns:
            a queue in which the dispatcher will place all packets that match
            the given port (or all ports if no port is specified)
        """
        queue = create_queue(queue_size)
        with self.dispatcher.registered(queue.put, port=0):
            yield queue

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
    ):
        """Context manager that waits for a CRTP packet on the given port (or
        all ports) matching a given predicate while the execution is in the
        context.

        Returns:
            an awaitable value that resolves to the first CRTP packet matching
            the port number and the predicate
        """
        value = AwaitableValue()

        if predicate:

            async def matcher(packet: CRTPPacket) -> None:
                if predicate(packet):
                    await value.set(packet)

        else:
            matcher = value.set

        with self.registered(matcher, port=port):
            yield value
