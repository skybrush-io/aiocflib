"""Functions and classes related to color handling."""

from colour import Color as ColorBase
from typing import Tuple, Union

__all__ = ("Color", "ColorLike", "to_color")


#: Typing for objects that can be converted into an RGB color
ColorLike = Union[str, Tuple[float, float, float]]


class Color(ColorBase):
    def get_intrgb(self):
        return tuple(max(0, min(255, round(x * 255))) for x in self.rgb)

    def set_intrgb(self, value):
        value = tuple(x / 255.0 for x in value)
        self.set_rgb(value)

    def get_rgb888(self):
        r, g, b = self.get_intrgb()
        return (r << 16) | (g << 8) | b

    def set_rgb888(self, value):
        r, g, b = ((value >> 16) & 0xFF), ((value >> 8) & 0xFF), (value & 0xFF)
        self.set_intrgb(r, g, b)


def to_color(value: ColorLike) -> Color:
    """Converts a string or an RGB tuple into a color object.

    RGB tuples must specify the components in tn [0; 255] range.
    """
    if not isinstance(value, str):
        r, g, b = value
        return Color(rgb=(r / 255.0, g / 255.0, b / 255.0))
    else:
        return Color(value)
