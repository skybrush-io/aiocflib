import sys

from anyio import create_lock, sleep
from contextlib import asynccontextmanager

lock = None


@asynccontextmanager
async def some_other_context():
    await sleep(1)
    yield 42
    await sleep(1)


@asynccontextmanager
async def some_context():
    global lock

    if lock is None:
        lock = create_lock()

    async with lock:
        print("In lock, waiting...")
        ctx = some_other_context()
        stuff = await ctx.__aenter__()
        print(f"In lock, sleep ended, got {stuff}.")

    try:
        yield
    finally:
        async with lock:
            print("In lock during cleanup")
            await ctx.__aexit__(*sys.exc_info())
            print("In lock during cleanup, sleep ended.")


async def test():
    from aiocflib.crtp.drivers import init_drivers

    from aiocflib.crtp.drivers.radio import SharedCrazyradio
    from aiocflib.crazyflie import Crazyflie

    init_drivers()

    # uri = "usb://0"
    uri = "radio+log://0/80/2M/E7E7E7E704"

    async with Crazyflie(uri):
        print("Got shared Crazyradio")
        await sleep(1)
        print("Releasing shared Crazyradio")

    print("Exited all contexts successfully")


if __name__ == "__main__":
    from anyio import run

    run(test, backend="trio")
