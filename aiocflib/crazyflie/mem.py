"""Classes related to accessing the memory subsystem of a Crazyflie."""

from abc import abstractmethod, ABCMeta
from collections import namedtuple
from enum import IntEnum
from struct import Struct, error as StructError

from aiocflib.crtp import CRTPPort

from .crazyflie import Crazyflie

__all__ = ("Memory",)


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


class MemoryType(IntEnum):
    """Enum representing the types of memories supported by a Crazyflie."""

    I2C = 0
    ONE_WIRE = 1
    LED = 0x10
    LOCO = 0x11
    TRAJECTORY = 0x12
    LOCO2 = 0x13
    LIGHTHOUSE = 0x14
    MEMORY_TESTER = 0x15
    SD_CARD = 0x16

    @property
    def description(self):
        """Human-readable description of the memory type."""
        return _memory_type_descriptions.get(int(self), "Unknown")


_memory_type_descriptions = {
    MemoryType.I2C: "I2C",
    MemoryType.ONE_WIRE: "1-wire",
    MemoryType.LED: "LED driver",
    MemoryType.LOCO: "Loco positioning",
    MemoryType.TRAJECTORY: "Trajectory",
    MemoryType.LOCO2: "Loco positioning 2",
    MemoryType.LIGHTHOUSE: "Lighthouse positioning",
    MemoryType.MEMORY_TESTER: "Memory tester",
    MemoryType.SD_CARD: "SD card",
}


class MemoryHandler(metaclass=ABCMeta):
    """Interface specification for memory handlers that know how to read and
    write a certain type of memory.
    """

    @abstractmethod
    async def read(self, addr: int, length: int) -> bytes:
        """Reads a given number of bytes from the given address.

        Parameters:
            addr: the address to read from
            length: the number of bytes to read
        """
        raise NotImplementedError

    @abstractmethod
    async def write(self, addr: int, data: bytes) -> None:
        """Writes some data to the given address.

        Parameters:
            addr: the address to read from
            length: the number of bytes to read
        """
        raise NotImplementedError


_MemoryElement = namedtuple("_MemoryElement", "type size address")


class MemoryElement(_MemoryElement):
    """Class containing information about a single memory element on a
    Crazyflie.
    """

    _struct = Struct("<BIQ")

    @classmethod
    def from_bytes(cls, data: bytes):
        """Constructs a MemoryElement_ instance from its representation in
        the CRTP memory details packet.

        Parameters:
            data: the data section of the CRTP packet, without the command byte
                and the ID of the memory element

        Raises:
            ValueError: if the data section cannot be parsed
        """
        try:
            return cls(*cls._struct.unpack(data))
        except StructError:
            raise ValueError("invalid memory description") from None

    def __new__(cls, type: int, size: int = 0, address: int = 0):
        """Constructor.

        Parameters:
            type: the numeric type ID of the memory
            size: the size of the memory, in bytes
            address: the address of the memory - only for one-wire memories
        """
        return super(MemoryElement, cls).__new__(cls, type, size, address)


class Memory:
    """Class representing the handler of messages releated to the memory
    subsystem of a Crazyflie instance.
    """

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle the memory
                subsystem related messages
        """
        self._crazyflie = crazyflie
        self._memories = None

    async def validate(self):
        """Ensures that the basic information about the memories on the Crazyflie
        are downloaded.
        """
        if self._memories is not None:
            return

        self._memories = await self._validate()

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
            command=MemoryInfoCommand.GET_DETAILS,
            data=bytes((index,)),
        )
        if response:
            return MemoryElement.from_bytes(response)
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

    async def _validate(self):
        """Downloads the basic information about the memories on the Crazyflie."""
        num_memories = await self._get_number_of_memories()
        memories = [await self._get_memory_details(i) for i in range(num_memories)]
        return memories
