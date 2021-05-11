"""Script that tests how much time it takes to send an empty (null) CRTP
packet on the radio to a given address.
"""

from aiocflib.crazyflie import Crazyflie
from aiocflib.crtp import CRTPPort, LinkControlChannel
from aiocflib.crtp.drivers import init_drivers
from aiocflib.utils import timing


async def run_test():
    URI = "cppradio://0/80/2M/E7E7E7E701"

    PACKET_SIZE = 24
    NUM_PACKETS = 1024

    init_drivers()

    async with Crazyflie(URI, cache="/tmp/cfcache") as cf:
        total_bytes = 0
        with timing() as t:
            for i in range(NUM_PACKETS):
                data = bytes([i & 0xFF] * PACKET_SIZE)
                response = await cf.run_command(
                    port=CRTPPort.LINK_CONTROL,
                    channel=LinkControlChannel.ECHO,
                    data=data,
                )
                assert data == response
                total_bytes += len(response)

        print(
            f"{total_bytes} bytes, {t.elapsed:.2f} seconds, {total_bytes / t.elapsed:.3f} bytes/sec"
        )


if __name__ == "__main__":
    import anyio

    anyio.run(run_test)
