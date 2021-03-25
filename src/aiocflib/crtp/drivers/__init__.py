from .base import CRTPDriver
from .registry import register

__all__ = ("CRTPDriver", "init_drivers", "register")


def init_drivers():
    """Initializes all the commonly supported and used drivers and middleware
    in the CRTP stack.

    This method is provided as a convenience so you don't need to import the
    drivers and middleware you wish to use in the driver URIs before
    constructing a driver object.
    """
    from .cpplink import CppRadioDriver  # noqa
    from .radio import RadioDriver  # noqa
    from .sitl import SITLDriver  # noqa
    from .usb import USBDriver  # noqa

    from ..middleware.log import LoggingMiddleware  # noqa
