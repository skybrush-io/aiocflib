from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterable, Optional, Union

from aiocflib.drivers.crazyradio import RadioConfiguration
from aiocflib.crtp.crtpstack import CRTPPacket, CRTPPortLike
from aiocflib.crtp.device import _handle_data_argument
from aiocflib.crtp.drivers.radio import SharedCrazyradio
from aiocflib.crtp.exceptions import WrongURIType
from aiocflib.utils.addressing import DEFAULT_RADIO_BROADCAST_ADDRESS, parse_radio_uri


class _Broadcaster:
    """Class that provides functions for broadcasting CRTP packets to all
    devices listening at a specific broadcast address.
    """

    def __init__(self, uri: str, radio, configuration: RadioConfiguration):
        """Constructor.

        Creates a broadcaster object that uses the given shared radio object and
        radio configuration.

        Do not use directly; use the Broadcaster_ context manager instead.
        """
        self._uri = uri
        self._radio = radio
        self._configuration = configuration

    @property
    def uri(self) -> str:
        """URI of the target of the broadcaster."""
        return self._uri

    async def send_packet(
        self,
        *,
        port: CRTPPortLike,
        channel: int = 0,
        data: Optional[Union[int, bytes, Iterable[Union[int, bytes]]]] = None,
    ) -> None:
        """Broadcasts a packet with the given CRTP port, channel and the given
        body.

        Parameters:
            port: the CRTP port to send the packet to
            channel: the CRTP channel to send the packet to
            data: the body of the request packet
        """
        packet = CRTPPacket(port=port, channel=channel)
        packet.data = _handle_data_argument(data)
        await self.send_bytes(packet.to_bytes())

    async def send_bytes(self, data: bytes) -> None:
        """Broadcasts some raw bytes to the associated broadcast address.

        Parameters:
            data: the data to broadcast
        """
        await self._radio.configure_send_and_receive_bytes(self._configuration, data)


@asynccontextmanager
async def Broadcaster(uri: str) -> AsyncIterator[_Broadcaster]:
    """Async context manager that creates an object that allows the user to
    send broadcast packets to the given radio URI.

    Parameters:
        uri: the URI describing the data link where the broadcast packets
            should be sent to. It must be a radio URL with the radio index,
            the channel and the data rate specified; the address may be
            omitted and defaults to the Crazyradio broadcast address
    """
    try:
        parts = parse_radio_uri(uri, default_address=DEFAULT_RADIO_BROADCAST_ADDRESS)
    except ValueError:
        raise WrongURIType from None

    index = parts.pop("index")
    configuration = RadioConfiguration(**parts)

    async with SharedCrazyradio(index) as radio:
        yield _Broadcaster(configuration.to_uri(index), radio, configuration)


async def test():
    from aiocflib.bootloader.target import BootloaderTargetType
    from aiocflib.bootloader.types import BootloaderCommand
    from aiocflib.crtp.crtpstack import CRTPPort, LinkControlChannel

    async with Broadcaster("radio://0/80/2M") as broadcaster:
        print(repr(broadcaster.uri))
        await broadcaster.send_packet(
            port=CRTPPort.LINK_CONTROL,
            channel=LinkControlChannel.BOOTLOADER,
            data=(BootloaderTargetType.NRF51, BootloaderCommand.SHUTDOWN),
        )


if __name__ == "__main__":
    from anyio import run

    run(test)
