"""Classes related to accessing the memory subsystem of a Crazyflie."""

from abc import abstractmethod, abstractproperty, ABCMeta
from dataclasses import dataclass
from enum import IntEnum
from errno import ENODATA
from struct import Struct, error as StructError
from typing import Callable, ClassVar, List, Optional, Tuple

from aiocflib.crtp import CRTPPort, MemoryType
from aiocflib.errors import error_to_string
from aiocflib.utils import chunkify
from aiocflib.utils.checksum import crc32
from aiocflib.utils.registry import Registry

from .crazyflie import Crazyflie

__all__ = ("Memory", "MemoryType")


class MemoryChannel(IntEnum):
    """Enum representing the names of the channels of the memory service in
    the CRTP protocol.
    """

    INFO = 0
    READ = 1
    WRITE = 2


class MemoryInfoCommand(IntEnum):
    """Enum representing the names of the information commands in the memory
    service of the CRTP protocol.
    """

    GET_NUMBER_OF_MEMORIES = 1
    GET_DETAILS = 2


@dataclass(frozen=True)
class MemoryElement:
    """Class containing information about a single memory element on a
    Crazyflie.
    """

    index: int
    """Index of the memory element."""

    type: int
    """Type identifier of the memory element."""

    size: int
    """Size of the memory element, in bytes."""

    address: int
    """Offset of the memory element in its address space."""

    _struct: ClassVar[Struct] = Struct("<BIQ")

    @classmethod
    def from_bytes(cls, index: int, data: bytes):
        """Constructs a MemoryElement_ instance from its representation in
        the CRTP memory details packet.

        Parameters:
            index: the index of the memory element that is being constructed
            data: the data section of the CRTP packet, without the command byte
                and the ID of the memory element

        Raises:
            ValueError: if the data section cannot be parsed
        """
        try:
            return cls(index, *cls._struct.unpack(data))
        except StructError:
            raise ValueError("invalid memory description") from None


_memory_handler_registry: Registry[
    Callable[[MemoryElement, Crazyflie], "MemoryHandler"]
] = Registry()


class MemoryHandler(metaclass=ABCMeta):
    """Interface specification for memory handlers that know how to read and
    write a certain type of memory.
    """

    #: Maximum number of bytes that can be read in a single request
    MAX_READ_REQUEST_LENGTH = 20

    #: Maximum number of bytes that can be written in a single request
    MAX_WRITE_REQUEST_LENGTH = 25

    @staticmethod
    def for_element(element: MemoryElement, owner: Crazyflie) -> "MemoryHandler":
        """Constructs an appropriate memory handler for the given memory
        element, depending on its type.

        Parameters:
            element: the memory element
            owner: the Crazyflie that owns the memory element
        """
        key = str(element.type)
        cls = _memory_handler_registry.find(key, default=MemoryHandlerBase)
        return cls(element, owner=owner)  # type: ignore

    @abstractmethod
    async def dump(self, strip: bool = False) -> bytes:
        """Reads the entire contents of the memory and returns it as a bytes
        object.

        Parameters:
            strip: whether to strip trailing zero bytes from the result
        """
        raise NotImplementedError

    @abstractmethod
    async def read(self, addr: int, length: int) -> bytes:
        """Reads a given number of bytes from the given address.

        Parameters:
            addr: the address to read from
            length: the number of bytes to read
        """
        raise NotImplementedError

    @abstractproperty
    def size(self) -> int:
        """Returns the size of the memory that this handler handles."""
        raise NotImplementedError

    @abstractproperty
    def type(self) -> int:
        """Returns the type of the memory that this handler handles."""
        raise NotImplementedError

    @abstractmethod
    async def write(self, addr: int, data: bytes) -> None:
        """Writes some data to the given address.

        Parameters:
            addr: the address to read from
            length: the number of bytes to read
        """
        raise NotImplementedError


class MemoryHandlerBase(MemoryHandler):
    """Base implementation of a memory handler."""

    _addressing_struct: ClassVar[Struct] = Struct("<BI")

    def __init__(self, element: MemoryElement, owner: Crazyflie):
        """Constructor.

        Parameters:
            element: the memory element object that contains the type, size,
                address and index of the memory managed by this handler
            owner: the Crazyflie that owns this handler
        """
        self._crazyflie = owner
        self._element = element

    async def dump(self, strip: bool = False) -> bytes:
        result = await self.read(0, self.size)
        return result.rstrip(b"\x00") if strip else result

    async def read(self, addr: int, length: int) -> bytes:
        chunks = []
        for start, size in chunkify(
            addr, length, step=MemoryHandler.MAX_READ_REQUEST_LENGTH
        ):
            chunk, status = await self._read_chunk(start, size)
            if status == 0:
                chunks.append(chunk)
            else:
                raise IOError(
                    status,
                    "Read request returned error code {0} ({1})".format(
                        status, error_to_string(status)
                    ),
                )
        return b"".join(chunks)

    @property
    def size(self) -> int:
        return self._element.size

    @property
    def type(self) -> int:
        return self._element.type

    async def write(self, addr: int, data: bytes) -> None:
        for start, size in chunkify(
            0, len(data), step=MemoryHandler.MAX_WRITE_REQUEST_LENGTH
        ):
            status = await self._write_chunk(addr + start, data[start : (start + size)])
            if status:
                raise IOError(
                    status,
                    "Write request returned error code {0} ({1})".format(
                        status, error_to_string(status)
                    ),
                )

    async def _read_chunk(self, addr: int, length: int) -> Tuple[bytes, int]:
        """Reads a chunk of data that fits into a single packet, starting
        from the given address.

        Parameters:
            addr: the address to start the read operation from
            length: the number of bytes to read; must be less than
                `MemoryHandler.MAX_READ_REQUEST_LENGTH`.

        Returns:
            the data that was read and the status code sent by the Crazyflie,
            in a tuple. A zero status code means that the read operation was
            successful.
        """
        response = await self._crazyflie.run_command(
            port=CRTPPort.MEMORY,
            channel=MemoryChannel.READ,
            command=self._addressing_struct.pack(self._element.index, addr),
            data=(length,),
        )
        return (response[1:], response[0]) if response else (b"", ENODATA)

    async def _write_chunk(self, addr: int, data: bytes) -> int:
        """Writes a chunk of data that fits into a single packet, starting
        from the given address.

        Parameters:
            addr: the address to write the data to
            data: the data to write; must be shorter than
                `MemoryHandler.MAX_WRITE_REQUEST_LENGTH`.

        Returns:
            the status code sent by the Crazyflie; zero means that the write
            was successful
        """
        response = await self._crazyflie.run_command(
            port=CRTPPort.MEMORY,
            channel=MemoryChannel.WRITE,
            command=self._addressing_struct.pack(self._element.index, addr),
            data=data,
        )
        return response[0] if response else ENODATA


class Memory:
    """Class representing the handler of messages related to the memory
    subsystem of a Crazyflie instance.
    """

    _crazyflie: Crazyflie
    _handlers: Optional[List[MemoryHandler]]

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle the memory
                subsystem related messages
        """
        self._crazyflie = crazyflie
        self._handlers = None

    async def find(self, type: MemoryType) -> MemoryHandler:
        """Finds the first memory element with the given type.

        Parameters:
            type: the type of the memory element to look for

        Returns:
            a handler object that can be used to read from and write to the
            given memory

        Raises:
            ValueError: if there is no such memory element
        """
        await self.validate()
        assert self._handlers is not None
        for handler in self._handlers:
            if handler.type == type:
                return handler
        raise ValueError("no memory matching type {0!r}".format(type))

    async def find_all(self, type: MemoryType) -> List[MemoryHandler]:
        """Finds all memory elements with the given type.

        Parameters:
            type: the type of the memory element to look for

        Yields:
            handler objects for all memory elements that have the given type.
            The handler objects can be used to read from and write to the
            corresponding memory element.
        """
        await self.validate()
        assert self._handlers is not None
        return [handler for handler in self._handlers if handler.type == type]

    async def find_eeprom(self) -> MemoryHandler:
        """Shortcut to find the memory handler for the internal EEPROM of the
        Crazyflie where the basic configuration settings are stored.
        """
        return await self.find(MemoryType.I2C)

    async def read(self, type: MemoryType, addr: int, length: int) -> bytes:
        """Shortcut to read the given number of bytes from the given address
        of the first memory element of the given type.

        Parameters:
            type: the type of the memory element to look for
            addr: the address to read from
            length: the number of bytes to read
        """
        handler = await self.find(type)
        return await handler.read(addr, length)

    async def validate(self):
        """Ensures that the basic information about the memories on the Crazyflie
        are downloaded.
        """
        if self._handlers is not None:
            return

        self._handlers = await self._validate()

    async def write(self, type: MemoryType, addr: int, data: bytes) -> None:
        """Shortcut to write the given data to the given address of the first
        memory element of the given type.

        Parameters:
            type: the type of the memory element to write to
            addr: the address to write to
            data: the data to write
        """
        handler = await self.find(type)
        return await handler.write(addr, data)

    async def write_with_checksum(
        self,
        type: MemoryType,
        addr: int,
        data: bytes,
        *,
        only_if_changed: bool = False,
        checksum: Callable[[bytes], bytes] = crc32,
    ) -> int:
        """Writes some data to the given address of the first memory element of
        the given type, _prepended by a checksum_.

        The primary purpose of this function is to prevent spending time with
        writing some data to some place in the Crazyflie memory if the same data
        has been written before. See the documentation of the
        `write_with_checksum()` function for more details; this method is just
        a convenience wrapper around it.

        Parameters:
            type: the type of the memory element to write to
            addr: the address to write to. The first few bytes will contain the
                checksum; the real data will be written _after_ the checksum.
                The length of the checksum will be returned from the function.
            data: the data to write
            only_if_changed: whether to write the data only if we detect that
                the checksum in front of the data is not identical to the
                expected checksum. When this parameter is False (which is the
                default), the data will be written unconditionally.
            checksum: the checksum function. This function must take the data to
                write and return a bytes object of _fixed_ length that contains
                the checksum of the data.

        Returns:
            the number of checksum bytes to skip if we want to read the data
            only, without its checksum
        """
        handler = await self.find(type)
        return await write_with_checksum(
            handler, addr, data, only_if_changed=only_if_changed, checksum=checksum
        )

    async def _get_memory_details(self, index: int) -> MemoryElement:
        """Retrieves detailed information about a single memory with the given
        index.

        Parameters:
            index: the index of the memory for which we need to retrieve its
            details

        Returns:
            the parsed details of the memory

        Raises:
            IndexError: if there is no such memory with the given index
            ValueError: if the response from the Crazyflie cannot be parsed
        """
        response = await self._crazyflie.run_command(
            port=CRTPPort.MEMORY,
            channel=MemoryChannel.INFO,
            command=(MemoryInfoCommand.GET_DETAILS, index),
        )
        if response:
            return MemoryElement.from_bytes(index, response)
        else:
            raise IndexError("memory index out of range")

    async def _get_number_of_memories(self) -> int:
        """Returns the number of memories present on the Crazyflie."""
        response = await self._crazyflie.run_command(
            port=CRTPPort.MEMORY,
            channel=MemoryChannel.INFO,
            command=MemoryInfoCommand.GET_NUMBER_OF_MEMORIES,
        )
        return response[0]

    async def _validate(self) -> List[MemoryHandler]:
        """Downloads the basic information about the memories on the Crazyflie."""
        num_memories = await self._get_number_of_memories()
        memories = [await self._get_memory_details(i) for i in range(num_memories)]
        return [
            MemoryHandler.for_element(memory, owner=self._crazyflie)
            for memory in memories
        ]


async def write_with_checksum(
    handler: MemoryHandler,
    addr: int,
    data: bytes,
    *,
    only_if_changed: bool = False,
    checksum: Callable[[bytes], bytes] = crc32,
) -> int:
    """Writes some data to the given address, _prepended by a checksum_.

    The primary purpose of this function is to prevent spending time with
    writing some data to some place in the Crazyflie memory if the same data
    has been written before. This is achieved by prepending the data with a
    checksum of fixed length. During subsequent write attempts, one can read
    the checksum first and compare it with the expected checksum of the
    data; if the two are the same, one can simply assume that the data has
    already been written and skip writing it again.

    Implementation note: since the Crazyflie memory segments are initialized
    to all-zeros after powerup, we must ensure that the checksum that we
    write to the Crazyflie memory is never zero. When the checksum function
    supplied by the user returns a checksum where all bytes are zeros, the
    checksum will be replaced by a placeholder value.

    Parameters:
        addr: the address to write to. The first few bytes will contain the
            checksum; the real data will be written _after_ the checksum.
            The length of the checksum will be returned from the function.
        data: the data to write
        only_if_changed: whether to write the data only if we detect that
            the checksum in front of the data is not identical to the
            expected checksum. When this parameter is False (which is the
            default), the data will be written unconditionally.
        checksum: the checksum function. This function must take the data to
            write and return a bytes object of _fixed_ length that contains
            the checksum of the data.

    Returns:
        the number of checksum bytes to skip if we want to read the data
        only, without its checksum
    """
    expected_chksum = checksum(data)
    chksum_length = len(expected_chksum)

    if not only_if_changed:
        need_to_write = True
    else:
        observed_chksum = await handler.read(addr, chksum_length)
        need_to_write = observed_chksum != expected_chksum

    if need_to_write:
        zeros = bytes([0] * chksum_length)
        await handler.write(addr, zeros)
        await handler.write(addr + chksum_length, data)
        await handler.write(addr, expected_chksum)

    return chksum_length
