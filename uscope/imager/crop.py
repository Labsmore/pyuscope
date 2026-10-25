"""
Sensor crop

Stored as fractions of the raw image (top / bottom / left / right in [0, 1))
so the same crop works at every camera resolution
Converted to pixels (gstreamer videocrop) at the current resolution
"""

import math

SIDES = ("top", "bottom", "left", "right")
# Enough that converting back at the authored resolution gives the same pixels
FRACTION_DIGITS = 6


def validate_fractions(fractions):
    """
    Returns a dict with all 4 sides as floats (missing sides => 0.0)
    Raises ValueError
    """
    if not isinstance(fractions, dict):
        raise ValueError("crop fractions must be a dict")
    for k in fractions.keys():
        if k not in SIDES:
            raise ValueError("Unexpected crop key: %s" % (k, ))
    ret = {}
    for k in SIDES:
        v = float(fractions.get(k, 0.0))
        if not 0.0 <= v < 1.0:
            raise ValueError("crop %s must be in [0, 1), got %s" % (k, v))
        ret[k] = v
    if ret["top"] + ret["bottom"] >= 1.0:
        raise ValueError("crop top + bottom must be < 1")
    if ret["left"] + ret["right"] >= 1.0:
        raise ValueError("crop left + right must be < 1")
    return ret


def _round(v, even):
    if even:
        return 2 * int(math.floor(v / 2.0 + 0.5))
    return int(math.floor(v + 0.5))


def fractions_to_pixels(fractions, wh, even=False):
    """
    fractions: see validate_fractions()
    wh: (width, height) of the raw image
    even: round each side to an even pixel count (keeps bayer phase)
    Returns {"top", "bottom", "left", "right"} in pixels
    """
    fractions = validate_fractions(fractions)
    w, h = wh
    ret = {}
    for k in SIDES:
        n = w if k in ("left", "right") else h
        ret[k] = _round(fractions[k] * n, even)
    if ret["left"] + ret["right"] >= w or ret["top"] + ret["bottom"] >= h:
        raise ValueError("crop %s leaves no image at %ux%u" % (ret, w, h))
    return ret


def pixels_to_fractions(pixels, wh, digits=FRACTION_DIGITS):
    """
    pixels: {"top", "bottom", "left", "right"} (missing sides => 0)
    wh: (width, height) the pixel values were measured at
    Rounded to digits significant digits
    """
    w, h = wh
    ret = {}
    for k in pixels.keys():
        if k not in SIDES:
            raise ValueError("Unexpected crop key: %s" % (k, ))
    for k in SIDES:
        p = int(pixels.get(k, 0))
        if p < 0:
            raise ValueError("Crop can't be negative")
        n = w if k in ("left", "right") else h
        ret[k] = float("%.*g" % (digits, p / n))
    return validate_fractions(ret)


def is_zero(crop):
    return crop is None or not any(crop.get(k) for k in SIDES)
