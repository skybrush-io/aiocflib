"""Middleware-related utility functions."""

from aiocflib.crtp.middleware.base import CRTPDriverWithMiddleware

from ..drivers.base import CRTPDriver

__all__ = ("unwrap_middleware",)


def unwrap_middleware(driver: CRTPDriver) -> CRTPDriver:
    """Takes a CRTP driver possibly wrapped in CRTP middleware, and returns
    the innermost driver without all the wrapping middleware.
    """
    while isinstance(driver, CRTPDriverWithMiddleware):
        driver = driver.wrapped
    return driver
