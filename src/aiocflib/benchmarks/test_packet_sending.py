"""Script that tests how much time it takes to send an empty (null) CRTP
packet on the radio to a given address.
"""

import sys

from aiocflib.crtp.crtpstack import CRTPPacket
from aiocflib.drivers.crazyradio import Crazyradio, RadioConfiguration
from aiocflib.utils import timing


async def run_test():
    # General results:
    #
    # Sending a CRTP packet to an online target takes 1.06 + 0.02*k msec, where
    # k is the number of bytes in the packet.
    #
    # Sending a CRTP packet to an offline target that does not respond takes
    # 3.433 + 0.033*k msec, where k is the number of bytes in the packet.

    URI = "radio://0/80/2M/E7E7E7E701"
    URI_MISSING = "radio://0/80/2M/E7E7E7E7FA"

    device = await Crazyradio.from_uri(URI)
    config_existing = RadioConfiguration.from_uri(URI)
    config_missing = RadioConfiguration.from_uri(URI_MISSING)

    async with device as radio:
        for size in (1, 5, 10, 15, 20, 25, 30):
            print(
                f"Packet size: {size} byte(s)...", end="", file=sys.stderr, flush=True
            )
            row = []
            for config in (config_existing, config_missing):
                async with radio.configure(config):
                    packet = CRTPPacket.null().to_bytes()
                    if len(packet) < size:
                        packet += b"\x00" * (size - len(packet))
                    with timing() as t:
                        for i in range(5000):
                            await radio.send_and_receive_bytes(packet)
                    row.append(t.elapsed / 5)
            row = "\t".join(map(str, row))
            print(" done.", file=sys.stderr, flush=True)
            print(f"{size}\t{row}")


if __name__ == "__main__":
    import anyio

    anyio.run(run_test)
