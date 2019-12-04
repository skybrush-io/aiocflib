from .crtpdriver import CRTPDriver
from .crtpstack import CRTPDataLike, CRTPDispatcher, CRTPPacket, CRTPPort, CRTPPortLike

__all__ = (
    "CRTPDataLike",
    "CRTPDispatcher",
    "CRTPDriver",
    "CRTPPacket",
    "CRTPPort",
    "CRTPPortLike",
    "init_drivers",
)


def init_drivers():
    """Initializes all the commonly supported and used drivers in the CRTP
    stack.

    This method is provided as a convenience so you don't need to import the
    drivers you wish to use in the driver URIs before constructing a driver
    object.
    """
    from .radiodriver import RadioDriver  # noqa
    from .usbdriver import USBDriver  # noqa
