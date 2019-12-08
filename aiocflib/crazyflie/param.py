"""Classes related to accessing the parameters subsystem of a Crazyflie."""

from collections import namedtuple
from enum import IntEnum
from struct import Struct, error as StructError
from typing import Tuple

from aiocflib.crtp import CRTPPort

from .crazyflie import Crazyflie

__all__ = ("Parameters",)


class ParameterChannel(IntEnum):
    """Enum representing the names of the channels of the parameter service in
    the CRTP protocol.
    """

    TABLE_OF_CONTENTS = 0
    READ = 1
    WRITE = 2
    COMMAND = 3


class ParameterTOCCommand(IntEnum):
    """Enum representing the names of the table-of-contents commands in the
    parameter service of the CRTP protocol.
    """

    RESET = 0
    READ_PARAMETER_DETAILS_V2 = 2
    READ_TOC_INFO_V2 = 3


_ParameterSpecification = namedtuple(
    "_ParameterSpecification", "id type group name read_only"
)


class ParameterSpecification(_ParameterSpecification):
    """Class representing the specification of a single parameter of the
    Crazyflie."""

    _struct = Struct("<BIQ")

    _types = {
        0x08: ("uint8_t", Struct("<B")),
        0x09: ("uint16_t", Struct("<H")),
        0x0A: ("uint32_t", Struct("<L")),
        0x0B: ("uint64_t", Struct("<Q")),
        0x00: ("int8_t", Struct("<b")),
        0x01: ("int16_t", Struct("<h")),
        0x02: ("int32_t", Struct("<i")),
        0x03: ("int64_t", Struct("<q")),
        0x06: ("float", Struct("<f")),
        0x07: ("double", Struct("<d")),
    }

    @classmethod
    def from_bytes(cls, data: bytes, id: int):
        try:
            type = data[0] & 0x0F
            read_only = bool(data[0] & 0x40)
            group, name, *rest = data[1:].split(b"\x00")
            return cls(
                id=id,
                type=type,
                group=group.decode("ascii"),
                name=name.decode("ascii"),
                read_only=read_only,
            )
        except Exception:
            raise ValueError("invalid parameter description") from None

    def encode_value(self, value) -> bytes:
        """Encodes a single value of this parameter into its raw byte-level
        representation.
        """
        return self._types[self.type][1].pack(value)

    @property
    def full_name(self) -> str:
        """Returns the fully-qualified name of the parameter, which is
        the concatenation of the group and the name of the parameter, separated
        by a dot.
        """
        return "{0.group}.{0.name}".format(self)

    def parse_value(self, data: bytes):
        """Parses the raw byte-level representation of a single value of this
        parameter, as received from the Crazyflie, and returns the corresponding
        Python value.
        """
        return self._types[self.type][1].unpack(data)[0]


class Parameters:
    """Class representing the handler of messages releated to the parameter
    subsystem of a Crazyflie instance.
    """

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle the parameter
                subsystem related messages
        """
        self._crazyflie = crazyflie

        self._parameters = None
        self._parameters_by_name = None
        self._values = None

    async def get(self, name: str, fetch: bool = False):
        """Returns the current value of a parameter, given its fully-qualified
        name.

        Parameters:
            name: the fully-qualified name of the parameter
            fetch: whether to forcefully fetch a new value from the drone even
                if we have a locally cached copy

        Returns:
            the current value of the parameter (which may be a cached value)
        """
        value = None if fetch else self._values.get(name)
        if value is None:
            value = self._values[name] = await self._fetch(name)
        return value

    async def validate(self):
        """Ensures that the basic information about the parameters of the
        Crazyflie are downloaded.
        """
        if self._parameters is not None:
            return

        self._parameters, self._parameters_by_name = await self._validate()
        self._values = {}

    async def _fetch(self, name: str):
        """Furcefully fetch the current value of a parameter, given its
        fully-qualified name.

        Parameters:
            name: the fully-qualified name of the parameter

        Returns:
            the current value of the parameter
        """
        await self.validate()

        parameter = self._parameters_by_name[name]
        index = parameter.id

        response = await self._crazyflie.run_command(
            port=CRTPPort.PARAMETERS,
            channel=ParameterChannel.READ,
            data=bytes((index & 0xFF, index >> 8)),
        )

        if not response:
            raise IndexError("parameter index out of range")
        if len(response) < 3:
            raise ValueError("invalid response for parameter query")

        return parameter.parse_value(response[3:])

    async def _get_parameter_spec_by_index(self, index: int) -> ParameterSpecification:
        """Returns the specification of the parameter with the given index."""
        response = await self._crazyflie.run_command(
            port=CRTPPort.PARAMETERS,
            channel=ParameterChannel.TABLE_OF_CONTENTS,
            command=ParameterTOCCommand.READ_PARAMETER_DETAILS_V2,
            data=bytes((index & 0xFF, index >> 8)),
        )
        if not response:
            raise IndexError("parameter index out of range")
        return ParameterSpecification.from_bytes(response, id=index)

    async def _get_table_of_contents_info(self) -> Tuple[int, int]:
        """Returns basic information about the table of contents of the
        parameter list, including the number of parameters and the CRC32
        hash of the parameter table.

        Returns:
            the number of parameters and the CRC32 hash of the parameter table
        """
        response = await self._crazyflie.run_command(
            port=CRTPPort.PARAMETERS,
            channel=ParameterChannel.TABLE_OF_CONTENTS,
            command=ParameterTOCCommand.READ_TOC_INFO_V2,
        )
        try:
            return Struct("<HI").unpack(response)
        except StructError:
            raise ValueError("invalid parameter TOC info response")

    async def _validate(self):
        """Downloads the basic information about the parameters of the
        Crazyflie.
        """
        # TODO(ntamas): check if we already have the parameters by CRC
        # TODO(ntamas): when connecting to multiple drones with the same TOC,
        # fetch the parameters only from one of them
        num_parameters, param_toc_crc32 = await self._get_table_of_contents_info()

        parameters = [
            await self._get_parameter_spec_by_index(i) for i in range(num_parameters)
        ]
        by_name = {parameter.full_name: parameter for parameter in parameters}
        return parameters, by_name
