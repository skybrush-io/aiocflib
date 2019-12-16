from typing import Generator, Tuple


def chunkify(
    addr: int, length: int, step: int
) -> Generator[Tuple[int, int], None, None]:
    """Calculates the start addresses and the sizes of individual chunks
    when trying to read some data from (or write some data to) a given start
    address with the given total length, and each individual request may
    have a limited size only.

    Parameters:
        addr: the address to start reading from or writing to
        length: the total number of bytes to read or write
        step: the number of bytes that we can read or write in a single
            request

    Returns:
        a generator yielding address-length combinations for the individual
        read requests that we need to execute
    """
    end = addr + length
    for start in range(addr, end, step):
        yield start, min(step, end - start)
