from __future__ import annotations

from anyio import open_file
from enum import IntEnum
from math import ceil
from struct import Struct
from typing import Optional, Union

from aiocflib.utils import chunkify

from .types import BootloaderCommand, BootloaderProtocolVersion, ProgressHandler

__all__ = ("BootloaderTarget", "BootloaderTargetType")


class BootloaderTargetType(IntEnum):
    """Enum representing the CPU targets that the bootloader can flash with a
    new firmware.
    """

    NRF51 = 0xFE
    STM32 = 0xFF

    @property
    def description(self) -> str:
        """Returns a human-readable description of the CPU target."""
        return _target_descriptions.get(self, "Unknown")

    @classmethod
    def from_string(cls, name: Union[str, "BootloaderTargetType"]):
        if isinstance(name, cls):
            return name

        assert isinstance(name, str)
        name = name.lower()

        for key, value in _target_descriptions.items():
            if value.lower() == name:
                return key

        raise ValueError("no such bootloader target: {0!r}".format(name))


_target_descriptions = {
    BootloaderTargetType.NRF51: "nRF51",
    BootloaderTargetType.STM32: "STM32",
}


class BootloaderTarget:
    """Class representing a flashing target for the Crazyflie bootloader.

    This value class is populated from a response to the ``GET_TARGET_INFO``
    command of the bootloader.

    Attributes:
        id: the identifier of the flashing target in the bootloader
        protocol_version: the bootloader protocol version
        page_size: the page size of the flashing process, in bytes
        buffer_pages: number of pages in the flashing buffer
        flash_pages: number of pages in the flash memory where we can write
        start_page: the first page where we can write the firmware
        cpu_id: the CPU ID of the target CPU
    """

    _load_buffer_command_struct = Struct("<BBHH")
    _read_buffer_command_struct = Struct("<BBHH")
    _read_command_struct = Struct("<BBHH")
    _write_command_struct = Struct("<BB")
    _write_command_params_struct = Struct("<HHH")
    _target_struct = Struct("<HHHH12s")

    _LOAD_BUFFER_CHUNK_SIZE = 25
    _READ_FLASH_CHUNK_SIZE = 25

    @classmethod
    def from_bytes(cls, owner, id: BootloaderTargetType, data: bytes):
        """Constructs a BootloaderTarget_ instance from its raw byte-level
        representation in the appropriate CRTP packet.

        Parameters:
            owner: the bootloader instance that owns this target
            id: the bootloader target type that was used when sending the
                CRTP packet whose response we are decoding
            data: the data section of the CRTP response, without the command
                bytes

        Returns:
            an appropriately constructed BootloaderTarget_ instance
        """
        result = cls(owner, id)

        (
            page_size,
            buffer_pages,
            flash_pages,
            start_page,
            cpu_id,
        ) = cls._target_struct.unpack(data[: cls._target_struct.size])

        if len(data) > cls._target_struct.size:
            protocol_version = BootloaderProtocolVersion(data[cls._target_struct.size])
        else:
            protocol_version = BootloaderProtocolVersion.UNKNOWN

        result.page_size = page_size
        result.buffer_pages = buffer_pages
        result.flash_pages = flash_pages
        result.start_page = start_page
        result.cpu_id = cpu_id
        result.protocol_version = protocol_version

        return result

    def __init__(self, owner, id: BootloaderTargetType):
        """Constructor."""
        self._bootloader = owner

        self.id = id
        self.protocol_version = BootloaderProtocolVersion.UNKNOWN
        self.page_size = 0  # type: int
        self.buffer_pages = 0  # type: int
        self.flash_pages = 0  # type: int
        self.start_page = 0  # type: int
        self.cpu_id = b""

    def __str__(self):
        result = [
            "Target info: {0} (0x{1:X})".format(self.id.description, self.id),
            "Flash pages: {0.flash_pages} | Page size: {0.page_size} | "
            "Buffer pages: {0.buffer_pages} | Start page: {0.start_page}".format(self),
            "{0.max_firmware_size_in_kbytes} KBytes of flash available for firmware image.".format(
                self
            ),
        ]
        return "\n".join(result)

    @property
    def buffer_size(self) -> int:
        """Returns the size of the upload buffer on this target, in bytes."""
        return self.buffer_pages * self.page_size

    @property
    def firmware_address(self) -> int:
        """Address where the firmware should be written in the flash memory."""
        return self.start_page * self.page_size

    @property
    def flash_size(self) -> int:
        """Returns the size of the flash memory available for the firmware image
        on this target, in bytes.
        """
        return self.flash_pages * self.page_size

    @property
    def flash_size_in_kbytes(self) -> int:
        """Returns the size of the flash memory available for the firmware image
        on this target, in KBytes.
        """
        return self.flash_size // 1024

    @property
    def max_firmware_size(self) -> int:
        """Returns the size of the flash memory available for the firmware image
        on this target, in bytes.
        """
        return self.flash_size - self.firmware_address

    @property
    def max_firmware_size_in_kbytes(self) -> int:
        """Returns the size of the flash memory available for the firmware image
        on this target, in KBytes.
        """
        return self.max_firmware_size // 1024

    async def read_flash(
        self,
        address: int = 0,
        length: int = -1,
        *,
        on_progress: Optional[ProgressHandler] = None,
    ) -> bytes:
        """Reads the given number of bytes from the given address of the flash
        memory of the target.

        Parameters:
            address: the address to read from
            length: the maximum number of bytes to read. Negative numbers mean
                to read everything until the end of the flash memory.
            on_progress: function to call periodically with the number of bytes
                read in each iteration

        Returns:
            the contents of the flash memory, starting from the given address.
        """
        if address < 0:
            raise ValueError("address cannot be negative")

        result = []
        to_read = length if length >= 0 else (self.flash_size - address)

        # Special support for tqdm progress bars
        if hasattr(on_progress, "reset") and hasattr(on_progress, "update"):
            on_progress.reset(to_read)  # type: ignore
            on_progress = on_progress.update  # type: ignore

        while to_read > 0:
            page, offset = divmod(address, self.page_size)
            data = await self._bootloader.run_bootloader_command(
                command=self._read_command_struct.pack(
                    self.id, BootloaderCommand.READ_FLASH, page, offset
                )
            )
            result.append(data)

            bytes_read = len(data)
            address += bytes_read
            to_read -= bytes_read

            if on_progress:
                on_progress(bytes_read)

            if bytes_read < self._READ_FLASH_CHUNK_SIZE:
                # end of flash
                break

        return b"".join(result)

    async def read_firmware(
        self, length: int = -1, *, on_progress: Optional[ProgressHandler] = None
    ) -> bytes:
        """Reads the given number of bytes from the firmware area of the flash
        memory of the target.

        Parameters:
            length: the maximum number of bytes to read. Negative numbers mean
                to read everything until the end of the flash memory.
            on_progress: function to call periodically with the number of bytes
                read in each iteration

        Returns:
            the contents of the flash memory, starting from the given address.
        """
        return await self.read_flash(
            self.firmware_address, length, on_progress=on_progress
        )

    async def write_flash(
        self,
        address: int,
        data: bytes,
        *,
        on_progress: Optional[ProgressHandler] = None,
    ) -> None:
        """Writes some data at the given address into the flash memory of the
        target.

        Parameters:
            address: the address to write the data to. Right now it must point
                to the start of a page on the flash memory.
            data: the data to write
            on_progress: function to call periodically with the number of bytes
                written during the operation
        """
        if address % self.page_size:
            raise ValueError("write_flash() address must point to the start of a page")

        # Special support for tqdm progress bars
        if hasattr(on_progress, "reset") and hasattr(on_progress, "update"):
            on_progress.reset(len(data))  # type: ignore
            on_progress = on_progress.update  # type: ignore

        for start, size in chunkify(0, len(data), step=self.buffer_size):
            await self._fill_buffer_with(
                data[start : (start + size)], on_progress=on_progress
            )
            await self._flush_buffer_to_flash(address, size)

            address += size

    async def write_firmware(
        self,
        firmware: Union[bytes, str],
        *,
        on_progress: Optional[ProgressHandler] = None,
    ) -> None:
        """Writes the given data to the firmware area of the flash memory of
        the target.

        Parameters:
            firmware: the firmware to write. When it is a string, it is treated
                as the name of a file containing the firmware. When it is a
                bytes object, it is treated as the firmware itself.
            on_progress: function to call periodically with the number of bytes
                written during the operation
        """
        if isinstance(firmware, str):
            async with await open_file(firmware, "rb") as fp:
                firmware = await fp.read()

        assert isinstance(firmware, bytes)

        await self.write_flash(self.firmware_address, firmware, on_progress=on_progress)

    async def _fill_buffer_with(
        self,
        data: bytes,
        *,
        validate: bool = False,
        on_progress: Optional[ProgressHandler] = None,
    ) -> None:
        """Fills the upload buffer on the target with the given data.

        Parameters:
            data: the data to write into the buffer
        """
        assert len(data) <= self.buffer_size

        # First we fill the entire buffer, then, if we need to validate the
        # result, we validate in one batch after uploading. This is to ensure
        # that the Crazyflie has time to process the inbound packets before we
        # start reading the buffer back.
        for start, size in chunkify(0, len(data), step=self._LOAD_BUFFER_CHUNK_SIZE):
            page, offset = divmod(start, self.page_size)
            to_write = data[start : (start + size)]
            await self._bootloader.send_bootloader_packet(
                self._load_buffer_command_struct.pack(
                    self.id, BootloaderCommand.LOAD_BUFFER, page, offset
                )
                + to_write
            )

            if on_progress:
                on_progress(size)

        # Now we validate if needed
        if validate:
            errors = []
            for index, (start, size) in enumerate(
                chunkify(0, len(data), step=self._LOAD_BUFFER_CHUNK_SIZE)
            ):
                page, offset = divmod(start, self.page_size)
                expected = data[start : (start + size)]

                observed = await self._bootloader.run_bootloader_command(
                    command=self._read_buffer_command_struct.pack(
                        self.id, BootloaderCommand.READ_BUFFER, page, offset
                    ),
                    timeout=0.1,
                )

                if observed[:size] != expected:
                    from hexdump import hexdump

                    print("Tried to upload:")
                    hexdump(expected)
                    print()
                    print("Currently in buffer:")
                    hexdump(observed[:size])
                    print()

                    errors.append(index)

            if errors:
                print(repr(errors))
                raise IOError("failed to update buffer")

    async def _flush_buffer_to_flash(self, start: int, size: int) -> None:
        start, remainder = divmod(start, self.page_size)
        assert remainder == 0

        num_pages = int(ceil(size / self.page_size))

        # Note that we use a timeout of 2.5 seconds here and we don't re-send
        # this packet. This is intentional; sometimes the flash request takes
        # more than one second, and the STM32 bootloader has a problem with
        # re-sent flash requests.
        result = await self._bootloader.run_bootloader_command(
            command=self._write_command_struct.pack(
                self.id, BootloaderCommand.WRITE_FLASH
            ),
            data=self._write_command_params_struct.pack(0, start, num_pages),
            timeout=2.5,
            attempts=3,
        )

        if len(result) < 2:
            raise IOError("invalid response from flash write request")

        done = result[0] > 0
        status = result[1]

        if status == 1:
            raise IOError("invalid write request sent to target")
        elif status == 2:
            raise IOError("failed to erase sector in flash memory")
        elif status == 3:
            raise IOError("failed to write new data into flash memory")
        elif status > 0:
            raise IOError("unknown error (code = {0})".format(status))
        elif not done:
            raise IOError("target says write is not done but returned no error code")
