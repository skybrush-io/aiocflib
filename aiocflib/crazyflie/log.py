"""Classes related to accessing the logging subsystem of a Crazyflie."""

from collections import namedtuple
from enum import IntEnum
from struct import Struct, error as StructError
from typing import Tuple, Union

from aiocflib.crtp import CRTPPort
from aiocflib.utils.toc import fetch_table_of_contents_gracefully

from .crazyflie import Crazyflie

__all__ = ("Log",)


class LoggingChannel(IntEnum):
    """Enum representing the names of the channels of the logging service in
    the CRTP protocol.
    """

    TABLE_OF_CONTENTS = 0
    CONTROL = 1
    DATA = 2


class LoggingTOCCommand(IntEnum):
    """Enum representing the names of the table-of-contents commands in the
    logging service of the CRTP protocol.

    These commands are valid for LoggingChannel.TABLE_OF_CONTENTS (i.e.
    channel 0).
    """

    GET_ITEM = 0
    GET_INFO = 1
    GET_ITEM_V2 = 2
    GET_INFO_V2 = 3


_VariableSpecification = namedtuple(
    "_VariableSpecification", "id type group name read_only"
)

#: Dictionary mapping integer type codes to their C types, Python structs and
#: aliases
_type_properties = {
    # C type, Python struct, aliases
    0x01: ("uint8_t", Struct("<B"), ("uint8", "u8")),
    0x02: ("uint16_t", Struct("<H"), ("uint16", "u16")),
    0x03: ("uint32_t", Struct("<L"), ("uint32", "u32")),
    0x04: ("int8_t", Struct("<b"), ("int8", "i8")),
    0x05: ("int16_t", Struct("<h"), ("int16", "i16")),
    0x06: ("int32_t", Struct("<i"), ("int32", "i32")),
    0x07: ("float", Struct("<f"), ()),
    0x08: ("fp16", Struct("<h"), ()),
}


class VariableType(IntEnum):
    """Enum containing the possible types of a log variable and the corresponding
    numeric identifiers.
    """

    UINT8 = 1
    UINT16 = 2
    UINT32 = 3
    INT8 = 4
    INT16 = 5
    INT32 = 6
    FLOAT = 7
    FP16 = 8

    @classmethod
    def to_type(cls, value):
        """Converts an integer, string or VariableType_ instance to a
        VariableType.
        """
        if isinstance(value, cls):
            return value
        elif isinstance(value, str):
            return _type_names.get(value)
        else:
            return VariableType(value)

    @property
    def aliases(self) -> Tuple[str]:
        """Returns the registered type aliases of this type."""
        return (_type_properties[self][0],) + _type_properties[self][2]

    @property
    def struct(self) -> Struct:
        """Returns a Python struct that can be used to encode or decode
        log variables of this type.
        """
        return _type_properties[self][1]

    def encode_value(self, value) -> bytes:
        """Encodes a single value of this log variable type into its raw
        byte-level representation.
        """
        return self.struct.pack(value)


#: Type specification for objects that can be converted into a log variable type
VariableTypeLike = Union[str, int, VariableType]

#: Dictionary mapping string type aliases to types
_type_names = dict((alias, type) for type in VariableType for alias in type.aliases)


class VariableSpecification(_VariableSpecification):
    """Class representing the specification of a single log variable of the
    Crazyflie."""

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
            raise ValueError("invalid log variable description") from None

    def encode_value(self, value) -> bytes:
        """Encodes a single value of this log variable into its raw byte-level
        representation.
        """
        return _type_properties[self.type][1].pack(value)

    @property
    def full_name(self) -> str:
        """Returns the fully-qualified name of the log variable, which is
        the concatenation of the group and the name of the variable, separated
        by a dot.
        """
        return "{0.group}.{0.name}".format(self)

    def parse_value(self, data: bytes):
        """Parses the raw byte-level representation of a single value of this
        log variable, as received from the Crazyflie, and returns the
        corresponding Python value.
        """
        return _type_properties[self.type][1].unpack(data)[0]

    def to_bytes(self) -> bytes:
        header = (int(self.type) & 0x0F) + (0x40 if self.read_only else 0)

        parts = []
        parts.append(bytes((header,)))
        parts.append(self.group.encode("ascii"))
        parts.append(b"\x00")
        parts.append(self.name.encode("ascii"))
        parts.append(b"\x00")

        return b"".join(parts)


class Log:
    """Class representing the handler of messages releated to the logging
    subsystem of a Crazyflie instance.
    """

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle the parameter
                subsystem related messages
        """
        self._cache = crazyflie._get_cache_for("log_toc")
        self._crazyflie = crazyflie

        self._max_packet_count = None
        self._max_operation_count = None

        self._variables = None
        self._variables_by_name = None

    async def validate(self):
        """Ensures that the basic information about the parameters of the
        Crazyflie are downloaded.
        """
        if self._variables is not None:
            return

        self._variables, self._variables_by_name = await self._validate()

    async def _get_log_variable_spec_by_index(
        self, index: int
    ) -> VariableSpecification:
        """Returns the specification of the logging variable with the given
        index.

        Parameters:
            index: the index of the logging variable to retrieve

        Returns:
            the specification of the logging variable with the given index
        """
        response = await self._crazyflie.run_command(
            port=CRTPPort.LOGGING,
            channel=LoggingChannel.TABLE_OF_CONTENTS,
            command=(LoggingTOCCommand.GET_ITEM_V2, index & 0xFF, index >> 8),
        )
        if not response:
            raise IndexError("parameter index out of range")
        return VariableSpecification.from_bytes(response, id=index)

    async def _get_table_of_contents_info(self) -> Tuple[int, int]:
        """Returns basic information about the table of contents of the
        log variable list, including the number of log variables and the CRC32
        hash of the parameter table.

        Returns:
            the number of parameters and the CRC32 hash of the parameter table
        """
        response = await self._crazyflie.run_command(
            port=CRTPPort.LOGGING,
            channel=LoggingChannel.TABLE_OF_CONTENTS,
            command=LoggingTOCCommand.GET_INFO_V2,
        )
        try:
            length, hash, max_packet_count, max_operation_count = Struct(
                "<HIBB"
            ).unpack(response)
        except StructError:
            raise ValueError("invalid logging TOC info response")

        self._max_packet_count = max_packet_count
        self._max_operation_count = max_operation_count

        return length, hash

    async def _validate(self):
        """Downloads the basic information about the logging subsystem of the
        Crazyflie.
        """
        # TODO(ntamas): when connecting to multiple drones with the same TOC,
        # fetch the log items only from one of them
        parameters = await fetch_table_of_contents_gracefully(
            self._cache,
            self._get_table_of_contents_info,
            self._get_log_variable_spec_by_index,
            VariableSpecification.from_bytes,
            VariableSpecification.to_bytes,
        )
        by_name = {parameter.full_name: parameter for parameter in parameters}
        return parameters, by_name
