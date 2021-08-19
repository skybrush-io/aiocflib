"""Classes related to accessing the parameters subsystem of a Crazyflie."""

from __future__ import annotations

from contextlib import asynccontextmanager
from enum import IntEnum
from struct import Struct, error as StructError
from typing import cast, Dict, List, NamedTuple, Optional, Tuple, Union

from aiocflib.crtp import CRTPPort
from aiocflib.errors import error_to_string
from aiocflib.utils.toc import TOCCache, fetch_table_of_contents_gracefully

from .crazyflie import Crazyflie

__all__ = ("Parameters",)


class ParameterChannel(IntEnum):
    """Enum representing the names of the channels of the parameter service in
    the CRTP protocol.
    """

    TABLE_OF_CONTENTS = 0
    READ = 1
    WRITE = 2
    MISC = 3


class ParameterTOCCommand(IntEnum):
    """Enum representing the names of the table-of-contents commands in the
    parameter service of the CRTP protocol.

    These commands are valid for ParameterChannel.TABLE_OF_CONTENTS (i.e.
    channel 0).
    """

    RESET = 0
    READ_PARAMETER_DETAILS_V2 = 2
    READ_TOC_INFO_V2 = 3


class ParameterCommand(IntEnum):
    """Enum representing the names of the generic commands in the parameter
    service of the CRTP protocol.

    These commands are valid for ParameterChannel.MISC (i.e. channel 3).
    """

    SET_BY_NAME = 0
    VALUE_UPDATED = 1


_ParameterSpecification = NamedTuple(
    "_ParameterSpecification",
    [("id", int), ("type", int), ("group", str), ("name", str), ("read_only", bool)],
)

#: Dictionary mapping integer type codes to their C types, Python structs and
#: aliases
_type_properties = {
    # C type, Python struct, aliases
    0x00: ("int8_t", Struct("<b"), ("int8", "i8")),
    0x01: ("int16_t", Struct("<h"), ("int16", "i16")),
    0x02: ("int32_t", Struct("<i"), ("int32", "i32")),
    0x03: ("int64_t", Struct("<q"), ("int64", "i64")),
    0x05: ("fp16", Struct("<h"), ()),
    0x06: ("float", Struct("<f"), ()),
    0x07: ("double", Struct("<d"), ()),
    0x08: ("uint8_t", Struct("<B"), ("uint8", "u8")),
    0x09: ("uint16_t", Struct("<H"), ("uint16", "u16")),
    0x0A: ("uint32_t", Struct("<L"), ("uint32", "u32")),
    0x0B: ("uint64_t", Struct("<Q"), ("uint64", "u64")),
}


class ParameterType(IntEnum):
    """Enum containing the possible types of a parameter and the corresponding
    numeric identifiers.
    """

    INT8 = 0
    INT16 = 1
    INT32 = 2
    INT64 = 3
    FLOAT = 6
    DOUBLE = 7
    UINT8 = 8
    UINT16 = 9
    UINT32 = 10
    UINT64 = 11

    @classmethod
    def to_type(cls, value: Union[int, str, "ParameterType"]):
        """Converts an integer, string or ParameterType_ instance to a
        ParameterType.
        """
        if isinstance(value, cls):
            return value
        elif isinstance(value, str):
            return _type_names[value]
        else:
            return ParameterType(value)

    @property
    def aliases(self) -> Tuple[str]:
        """Returns the registered type aliases of this type."""
        return (_type_properties[self][0],) + _type_properties[self][2]

    @property
    def length(self) -> int:
        """Returns the number of bytes that a single value of this log
        variable would occupy.
        """
        return self.struct.size

    @property
    def struct(self) -> Struct:
        """Returns a Python struct that can be used to encode or decode
        parameter values of this type.
        """
        return _type_properties[self][1]

    def encode_value(self, value) -> bytes:
        """Encodes a single value of this parameter type into its raw byte-level
        representation.
        """
        return self.struct.pack(value)


#: Type specification for objects that can be converted into a parameter type
ParameterTypeLike = Union[str, int, ParameterType]

#: Dictionary mapping string type aliases to types
_type_names = dict((alias, type) for type in ParameterType for alias in type.aliases)


class ParameterSpecification(_ParameterSpecification):
    """Class representing the specification of a single parameter of the
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
            raise ValueError("invalid parameter description") from None

    def encode_value(self, value) -> bytes:
        """Encodes a single value of this parameter into its raw byte-level
        representation.
        """
        return _type_properties[self.type][1].pack(value)

    @property
    def full_name(self) -> str:
        """Returns the fully-qualified name of the parameter, which is
        the concatenation of the group and the name of the parameter, separated
        by a dot.
        """
        return "{0.group}.{0.name}".format(self)

    def parse_value(self, data: bytes) -> Union[int, float]:
        """Parses the raw byte-level representation of a single value of this
        parameter, as received from the Crazyflie, and returns the corresponding
        Python value.
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


class Parameters:
    """Class representing the handler of messages related to the parameter
    subsystem of a Crazyflie instance.
    """

    _cache: Optional[TOCCache]
    _crazyflie: Crazyflie

    _values: Dict[str, Union[int, float]]
    _variables: List[ParameterSpecification]
    _variables_by_name: Dict[str, ParameterSpecification]

    def __init__(self, crazyflie: Crazyflie):
        """Constructor.

        Parameters:
            crazyflie: the Crazyflie for which we need to handle the parameter
                subsystem related messages
        """
        self._cache = crazyflie._get_cache_for("param_toc")
        self._crazyflie = crazyflie

        self._values = None  # type: ignore
        self._variables = None  # type: ignore
        self._variables_by_name = None  # type: ignore

    async def get(self, name: str, fetch: bool = False) -> Union[int, float]:
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

    async def has(self, name: str) -> bool:
        """Returns whether the parameter with the given name is known to the
        Crazyflie.

        Parameters:
            name: the fully-qualified name of the parameter

        Returns:
            True if the parameter is known to the Crazyflie, False otherwise
        """
        await self.validate()
        return self.has_sync(name)
        return name in self._variables_by_name

    def has_sync(self, name: str) -> bool:
        """Synchronous variant of the ``has()`` method; returns whether the
        parameter with the given name is known to the Crazyflie.

        This method assumes that the parameter TOC has already been downloaded
        with ``self.validate()``.

        Parameters:
            name: the fully-qualified name of the parameter

        Returns:
            True if the parameter is known to the Crazyflie, False otherwise
        """
        return name in self._variables_by_name

    async def set(self, name: str, value) -> None:
        """Sets the value of a parameter, given its fully-qualified name.

        Parameters:
            name: the fully-qualified name of the parameter
            value: the new value of the parameter
        """
        # TODO(ntamas): make it possible to set by name if we have no TOC
        await self.validate()

        parameter = self._variables_by_name[name]
        if parameter.read_only:
            raise AttributeError("{} is read only".format(name))

        index = parameter.id

        response = await self._crazyflie.run_command(
            port=CRTPPort.PARAMETERS,
            channel=ParameterChannel.WRITE,
            command=(index & 0xFF, index >> 8),
            data=parameter.encode_value(value),
        )

        if not response:
            raise IndexError("parameter index out of range")
        if len(response) < 1:
            raise ValueError("invalid response for parameter setting")

        self._values[name] = parameter.parse_value(response)

    @asynccontextmanager
    async def set_and_restore(
        self, name: str, value, restore_to=None, fetch: bool = False
    ):
        """Asynchronous context manager that sets the value of a parameter
        when entering the context and restores it to another value when exiting
        the context.

        Parameters:
            name: the fully-qualified name of the parameter
            value: the new value of the parameter
            restore_to: the old value of the parameter to restore when exiting
                the context. `None` means to use the last cached copy of the
                parameter, or to fetch a new parameter if `fetch` is set to
                `True`.
            fetch: whether to fetch the old parameter value unconditionally before
                entering the context. Used only if `restore_to` is `None`,
                otherwise the value of `restore_to` will be unsed, in which
                case there is no point in fetching the old value.
        """
        if restore_to is None:
            restore_to = await self.get(name, fetch=fetch)

        try:
            await self.set(name, value)
            yield
        finally:
            await self.set(name, restore_to)

    async def set_fast(self, name: str, type: ParameterTypeLike, value) -> None:
        """Sets the value of a parameter without fetching the full parameter
        TOC first, given its fully-qualified name and its type.

        This function is useful if you don't want to spend time with fetching
        the parameter TOC from the drone and you only need to set the values
        of some parameters. Triggering a parameter read anywhere will download
        the TOC from the drone anyway if needed.
        """
        resolved_type = ParameterType.to_type(type)
        group, _, name_bytes = name.encode("ascii").rpartition(b".")
        command = [ParameterCommand.SET_BY_NAME, group, 0, name_bytes, 0]
        data = bytes((resolved_type,)) + resolved_type.encode_value(value)
        response = await self._crazyflie.run_command(
            port=CRTPPort.PARAMETERS,
            channel=ParameterChannel.MISC,
            command=command,
            data=data,
        )

        if not response:
            raise ValueError("Crazyflie returned an empty response")

        code = response[0]
        if code:
            raise ValueError(
                "Crazyflie returned error code {0} ({1})".format(
                    code, error_to_string(code)
                )
            )

    async def trigger(self, name: str) -> None:
        """Triggers some function on the drone by setting the value of the
        corresponding parameter to 1.

        Some parameters on the Crazyflie are used to trigger some functionality;
        for instance, the ``kalman.resetEstimation`` parameter is used to reset
        the state of the Kalman filter. This function is a convenience wrapper
        for setting the value of this parameter to 1.
        """
        return await self.set(name, 1)

    async def validate(self) -> None:
        """Ensures that the basic information about the parameters of the
        Crazyflie are downloaded.
        """
        if self._variables is not None:
            return

        self._variables, self._variables_by_name = await self._validate()
        self._values = {}

    async def _fetch(self, name: str) -> Union[int, float]:
        """Furcefully fetch the current value of a parameter, given its
        fully-qualified name.

        Parameters:
            name: the fully-qualified name of the parameter

        Returns:
            the current value of the parameter
        """
        await self.validate()

        parameter = self._variables_by_name[name]
        index = parameter.id

        response = await self._crazyflie.run_command(
            port=CRTPPort.PARAMETERS,
            channel=ParameterChannel.READ,
            command=(index & 0xFF, index >> 8),
        )

        if not response:
            raise IndexError("parameter index out of range")
        if len(response) < 2:
            raise ValueError("invalid response for parameter query")

        return parameter.parse_value(response[1:])

    async def _get_parameter_spec_by_index(self, index: int) -> ParameterSpecification:
        """Returns the specification of the parameter with the given index.

        Parameters:
            index: the index of the parameter to retrieve

        Returns:
            the specification of the parameter with the given index
        """
        response = await self._crazyflie.run_command(
            port=CRTPPort.PARAMETERS,
            channel=ParameterChannel.TABLE_OF_CONTENTS,
            command=(
                ParameterTOCCommand.READ_PARAMETER_DETAILS_V2,
                index & 0xFF,
                index >> 8,
            ),
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
            return cast(Tuple[int, int], Struct("<HI").unpack(response))
        except StructError:
            raise ValueError("invalid parameter TOC info response")

    async def _validate(self):
        """Downloads the basic information about the parameters of the
        Crazyflie.
        """
        # TODO(ntamas): when connecting to multiple drones with the same TOC,
        # fetch the parameters only from one of them
        parameters = await fetch_table_of_contents_gracefully(
            self._cache,
            self._get_table_of_contents_info,
            self._get_parameter_spec_by_index,
            ParameterSpecification.from_bytes,
            ParameterSpecification.to_bytes,
        )
        by_name = {parameter.full_name: parameter for parameter in parameters}
        return parameters, by_name
