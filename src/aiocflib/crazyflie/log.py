"""Classes related to accessing the logging subsystem of a Crazyflie."""

from anyio import create_lock
from async_generator import async_generator, yield_, yield_from_
from collections import namedtuple
from contextlib import asynccontextmanager
from enum import IntEnum
from functools import partial
from itertools import count
from struct import Struct, error as StructError
from typing import Iterable, List, Optional, Tuple, Union

from aiocflib.crtp import CRTPPort
from aiocflib.errors import error_to_string
from aiocflib.utils.toc import fetch_table_of_contents_gracefully
from aiocflib.utils.typing import Disposer

from .crazyflie import Crazyflie

__all__ = ("Log", "LogBlock")

#: The maximum size of a CRTP packet payload
MAX_LOG_DATA_PACKET_SIZE = 28


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


class LoggingControlCommand(IntEnum):
    """Enum representing the names of the control commands in the logging
    service of the CRTP protocol.

    These commands are valid for LoggingChannel.CONTROL (i.e. channel 1).
    """

    CREATE_BLOCK = 0
    APPEND_BLOCK = 1
    DELETE_BLOCK = 2
    START_LOGGING = 3
    STOP_LOGGING = 4
    RESET = 5
    CREATE_BLOCK_V2 = 6
    APPEND_BLOCK_V2 = 7


_VariableSpecification = namedtuple("_VariableSpecification", "id type group name")

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
    def length(self) -> int:
        """Returns the number of bytes that a single value of this log
        variable would occupy.
        """
        return self.struct.size

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
            group, name, *rest = data[1:].split(b"\x00")
            return cls(
                id=id, type=type, group=group.decode("ascii"), name=name.decode("ascii")
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
        header = int(self.type) & 0x0F

        parts = []
        parts.append(bytes((header,)))
        parts.append(self.group.encode("ascii"))
        parts.append(b"\x00")
        parts.append(self.name.encode("ascii"))
        parts.append(b"\x00")

        return b"".join(parts)


_LogBlockItem = namedtuple("_LogBlockItem", "name id fetch_as stored_as")


class LogBlockItem(_LogBlockItem):
    """A single item in a log block specification that holds the name of a
    log variable and the type it should be fetched as over the wire from the
    Crazyflie. The type does not need to match the type that is used to _store_
    the same variable in the Crazyflie firmware - conversion will occur
    on-the-fly.
    """

    def to_bytes(self) -> bytes:
        """Returns a byte-level representation of this item that can be used in
        a `CREATE_BLOCK` request.
        """
        return bytes(
            (
                ((self.fetch_as << 4) & 0xF0) | (self.stored_as & 0x0F),
                self.id & 0xFF,
                self.id >> 8,
            )
        )


_LogMessage = namedtuple("_LogMessage", "timestamp block items")


class LogMessage(_LogMessage):
    """Value object representing a single log message from the Crazyflie."""

    @classmethod
    def from_bytes(cls, data: bytes, block: "LogBlock"):
        return cls(
            block=block,
            timestamp=int.from_bytes(data[1:4], byteorder="little"),
            items=block._decode_values(data),
        )


class LogBlock:
    """Specification of a single log block that bundles together a desired
    logging period and a list of variables to log.
    """

    @staticmethod
    def _process_period_and_frequency(
        *,
        period: Optional[float] = None,
        frequency: Optional[float] = None,
        period_msec: Optional[int] = None,
        default: int = 100,
    ) -> int:
        """Processes `period` and `frequency` keyword arguments from a
        function call, returning an appropriate period in milliseconds.

        Parameters:
            period: the logging period, in seconds
            period_msec: the logging period, in milliseconds. Takes precedence
                over `period` or `frequency`.
            frequency: the logging frequency, in Hertz. Takes precedence over
                `period` if both are given
            default: the default logging period to return, in milliseconds,
                if neither the period nor the frequency are given

        Returns:
            the logging period, in milliseconds
        """
        if period_msec is not None:
            return int(period_msec)
        if frequency is None:
            frequency = 1.0 / period if period is not None else 10.0
        return 1000.0 // frequency

    def __init__(self, owner: "Log"):
        """Constructor.

        Parameters:
            owner: the log object that owns this specification
        """
        self._owner = owner
        self._disposer = None

        self._id = None
        self._items = []  # type: List[LogBlockItem]
        self._struct = None  # type: Optional[Struct]

    @property
    def id(self) -> int:
        """The numeric ID of the block when it is already submitted to the
        Crazyflie.
        """
        return self._id

    @id.setter
    def id(self, value: Union[int, None]) -> None:
        if self._id == value:
            return

        if self._id is not None and value is not None:
            raise ValueError("block already has a different ID")

        self._id = value

    @property
    def is_submitted(self) -> bool:
        """Returns whether this log specification has already been submitted
        to the Crazyflie.
        """
        return self._disposer is not None

    @property
    def items(self) -> Iterable[LogBlockItem]:
        """Iterates over the items in this log block."""
        return iter(self._items)

    @property
    def packet_size(self) -> int:
        """Returns the total number of bytes that the data in this log
        configuration would occupy.
        """
        return sum(item.fetch_as.length for item in self._items)

    def add_variable(
        self, name: str, type: Optional[Union[str, VariableType]] = None
    ) -> None:
        """Adds a new variable to this logging block."""
        toc = self._owner._variables_by_name
        try:
            spec = toc[name]
        except KeyError:
            raise KeyError("no such variable in log TOC: {0!r}".format(name))

        if type is None:
            type = spec.type

        item = LogBlockItem(
            name=name,
            id=spec.id,
            stored_as=VariableType(spec.type),
            fetch_as=VariableType(type),
        )
        self._items.append(item)

    @async_generator
    async def receive(
        self,
        *,
        period: Optional[float] = None,
        period_msec: Optional[int] = None,
        frequency: Optional[float] = None,
    ):
        """Async generator that yields log messages according to the
        specification from the Crazyflie.

        This function will automatically submit the log specification to the
        Crazyflie if it hasn't been submitted yet. The generator will remove
        the log specification from the Crazyflie when the generator is closed
        if and only if the log specification was submitted by the generator
        itself.

        Parameters:
            period: the logging period, in seconds
            period_msec: the logging period, in milliseconds. Takes precedence
                over `period` or `frequency`.
            frequency: the logging frequency, in Hertz. Takes precedence over
                `period` if both are given
        """
        period_msec = self._process_period_and_frequency(
            period=period, period_msec=period_msec, frequency=frequency
        )

        if self.is_submitted:
            await yield_from_(self._receive(period_msec))
        else:
            async with self.submitted():
                await yield_from_(self._receive(period_msec))

    @async_generator
    async def _receive(self, period_msec):
        await self._owner._start_log_block_by_id(self.id, period_msec)
        try:
            packets = self._owner._crazyflie.packets(port=CRTPPort.LOGGING)
            async for packet in packets:
                if (
                    packet.channel == LoggingChannel.DATA
                    and len(packet.data) >= 4
                    and packet.data[0] == self.id
                ):
                    await yield_(LogMessage.from_bytes(packet.data, block=self))
        finally:
            await self._owner._stop_log_block_by_id(self.id)

    async def submit(self) -> Disposer:
        """Submits this log specification to the Crazyflie and creates a new
        log block.

        Returns:
            a function that can be used to remove the log block from the
            Crazyflie
        """
        if self.is_submitted:
            raise RuntimeError("log block is already submitted to Crazyflie")

        id, self._disposer = await self._owner._submit_block(self)
        self.id = id

        format_strings = [item.fetch_as.struct.format[1:] for item in self._items]
        if format_strings and isinstance(format_strings[0], bytes):
            # Python <3.7
            format_strings = [fmt.decode("ascii") for fmt in format_strings]

        self._struct = Struct("<" + "".join(format_strings))

        return self._dispose

    @asynccontextmanager
    async def submitted(self):
        """Async context manager that submits the log specification to the
        Crazyflie when entering the context and removes it when exiting the
        context.
        """
        disposer = await self.submit()
        try:
            yield
        finally:
            await disposer()

    def to_bytes(self) -> bytes:
        """Returns a byte-level representation of this logging block that can
        be used in a `CREATE_BLOCK` request.
        """
        self._validate_packet_size()
        return b"".join(item.to_bytes() for item in self._items)

    def _decode_values(self, data: bytes) -> Tuple:
        """Decodes the values received in a log message from the Crazyflie.

        Parameters:
            data: the data part of the Crazyflie log message, starting from the
                log block ID.

        Returns:
            the decoded values
        """
        return self._struct.unpack_from(data, offset=4)

    async def _dispose(self):
        """Removes this log block from the Crazyflie."""
        if self.id is not None:
            self.id = None

        if self._disposer is not None:
            try:
                await self._disposer()
            except RuntimeError:
                # Well, maybe the Crazyflie was disconnected?
                pass
            self._disposer = None

    def _validate_packet_size(self):
        """Checks whether the contents of this log specification would fit into
        a single CRTP packet.

        Raises:
            ValueError: if there are too many entries in the specification
        """
        size = self.packet_size
        if size > MAX_LOG_DATA_PACKET_SIZE:
            raise ValueError(
                "log packet too large ({0} bytes, max is {1})".format(
                    size, MAX_LOG_DATA_PACKET_SIZE
                )
            )


class Log:
    """Class representing the handler of messages related to the logging
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

        self._block_id_generator = None

        self._variables = None
        self._variables_by_name = None

        self._operation_lock = create_lock()

    def create_block(self,) -> LogBlock:
        """Creates a new, empty log block specification object that can be
        used to start logging variables from the Crazyflie.
        """
        return LogBlock(self)

    async def reset(self):
        """Resets the logging framework and clears all logging blocks."""
        async with self._operation_lock:
            await self._crazyflie.run_command(
                port=CRTPPort.LOGGING,
                channel=LoggingChannel.CONTROL,
                command=LoggingControlCommand.RESET,
            )
        self._block_id_generator = count()

    async def validate(self):
        """Ensures that the basic information about the parameters of the
        Crazyflie are downloaded, and that the log subsystem is in a known
        state with no log blocks.
        """
        if self._variables is not None:
            return

        self._variables, self._variables_by_name = await self._validate()

    async def _create_log_block(self, id: int, block: LogBlock) -> None:
        """Creates a new log block with the given ID on the Crazyflie."""
        await self.validate()
        async with self._operation_lock:
            status = await self._crazyflie.run_command(
                port=CRTPPort.LOGGING,
                channel=LoggingChannel.CONTROL,
                command=(LoggingControlCommand.CREATE_BLOCK_V2, id),
                data=block.to_bytes(),
            )

        status = int(status[0])
        if status:
            raise RuntimeError(
                "Log block creation request returned error code {0} ({1})".format(
                    status, error_to_string(status)
                )
            )

    async def _delete_log_block_by_id(self, id: int) -> None:
        """Deletes the log block with the given ID from the Crazyflie."""
        async with self._operation_lock:
            status = await self._crazyflie.run_command(
                port=CRTPPort.LOGGING,
                channel=LoggingChannel.CONTROL,
                command=(LoggingControlCommand.DELETE_BLOCK, id),
            )

        status = int(status[0])
        if status:
            raise RuntimeError(
                "Log block deletion request returned error code {0} ({1})".format(
                    status, error_to_string(status)
                )
            )

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

    async def _start_log_block_by_id(self, id: int, period_msec: int) -> None:
        """Starts streaming messages from the log block with the given ID."""
        period_byte = int(period_msec / 10)
        if period_byte < 0 or period_byte > 255:
            raise ValueError("logging period must be between 0 and 2.55 seconds")

        async with self._operation_lock:
            status = await self._crazyflie.run_command(
                port=CRTPPort.LOGGING,
                channel=LoggingChannel.CONTROL,
                command=(LoggingControlCommand.START_LOGGING, id),
                data=(period_byte,),
            )

        status = int(status[0])
        if status:
            raise RuntimeError(
                "Log block start request returned error code {0} ({1})".format(
                    status, error_to_string(status)
                )
            )

    async def _stop_log_block_by_id(self, id: int) -> None:
        """Stops streaming messages from the log block with the given ID."""
        async with self._operation_lock:
            status = await self._crazyflie.run_command(
                port=CRTPPort.LOGGING,
                channel=LoggingChannel.CONTROL,
                command=(LoggingControlCommand.STOP_LOGGING, id),
            )

        status = int(status[0])
        if status:
            raise RuntimeError(
                "Log block stop request returned error code {0} ({1})".format(
                    status, error_to_string(status)
                )
            )

    async def _submit_block(self, block: LogBlock) -> Disposer:
        """Submits a log block to the Crazyflie for registration.

        Returns:
            the ID of the registered log block and an async function that can be
            called to remove the log block from the Crazyflie
        """
        id = next(self._block_id_generator)
        await self._create_log_block(id, block)
        return id, partial(self._delete_log_block_by_id, id)

    async def _validate(self):
        """Downloads the basic information about the logging subsystem of the
        Crazyflie, and that the log subsystem is in a known
        state with no log blocks.
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

        await self.reset()

        return parameters, by_name
