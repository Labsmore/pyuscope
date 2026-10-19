"""
Pixel formats that can flow through the gstreamer capture pipeline

Names match gstreamer video/x-raw "format" strings so that the same value
can be used for caps negotiation, config files, and decoding
"""

from collections import OrderedDict

import numpy as np
from PIL import Image

# Default / legacy format. 8 bit demosaiced RGB
DEFAULT_FORMAT = "RGB"
"""
name: gstreamer format string
label: human friendly description for GUI
bytes_per_pixel: packed bytes per pixel
dtype: numpy dtype for a sample
channels: samples per pixel
bits: container bits per sample
raw: sensor data (bayer mosaic for color cameras). Not demosaiced / color corrected
"""
FORMATS = OrderedDict([
    ("RGB", {
        "label": "8-bit RGB",
        "bytes_per_pixel": 3,
        "dtype": np.uint8,
        "channels": 3,
        "bits": 8,
        "raw": False,
    }),
    ("ARGB64", {
        "label": "16-bit RGB",
        "bytes_per_pixel": 8,
        "dtype": np.uint16,
        "channels": 4,
        "bits": 16,
        "raw": False,
    }),
    ("GRAY16_LE", {
        "label": "Raw sensor (16-bit)",
        "bytes_per_pixel": 2,
        "dtype": np.uint16,
        "channels": 1,
        "bits": 16,
        "raw": True,
    }),
    ("GRAY8", {
        "label": "Raw sensor (8-bit)",
        "bytes_per_pixel": 1,
        "dtype": np.uint8,
        "channels": 1,
        "bits": 8,
        "raw": True,
    }),
])


def is_valid(fmt):
    return fmt in FORMATS


def validate(fmt):
    if not is_valid(fmt):
        raise ValueError(
            f"Invalid pixel format {fmt!r}. Expected one of {', '.join(FORMATS)}"
        )
    return fmt


def label(fmt):
    return FORMATS[fmt]["label"]


def is_raw(fmt):
    return FORMATS[fmt]["raw"]


def is_16bit(fmt):
    return FORMATS[fmt]["bits"] == 16


def bytes_per_pixel(fmt):
    return FORMATS[fmt]["bytes_per_pixel"]


def bayer_pattern_cv2_code(pattern):
    """
    Map a bayer FourCC (as reported by camera SDKs, ex: "RGGB") to an OpenCV
    demosaic code producing RGB

    OpenCV names patterns by the 2x2 block starting at the second row/column,
    hence the apparent mismatch
    """
    import cv2

    codes = {
        "RGGB": cv2.COLOR_BayerBG2RGB,
        "BGGR": cv2.COLOR_BayerRG2RGB,
        "GRBG": cv2.COLOR_BayerGB2RGB,
        "GBRG": cv2.COLOR_BayerGR2RGB,
    }
    if pattern not in codes:
        raise ValueError(f"Unsupported bayer pattern {pattern!r}")
    return codes[pattern]


"""
How a 2x2 bayer block "ABCD" (rows AB / CD) is relabeled by a geometric operation
Derived assuming even image dimensions, which is always the case for these cameras
Keys match gstreamer videoflip method nicks where applicable
"""
BAYER_OPS = {
    "none": "ABCD",
    # Mirror
    "horizontal-flip": "BADC",
    "hflip": "BADC",
    "vertical-flip": "CDAB",
    "vflip": "CDAB",
    # Rotation
    "rotate-180": "DCBA",
    "clockwise": "CADB",
    "counterclockwise": "BDAC",
    # Transpose
    "upper-left-diagonal": "ACBD",
    "upper-right-diagonal": "DBCA",
    # Dropping an odd number of columns / rows from the left / top
    "shift-x": "BADC",
    "shift-y": "CDAB",
}
# gstreamer videoflip integer method values
VIDEOFLIP_METHODS = {
    0: "none",
    1: "clockwise",
    2: "rotate-180",
    3: "counterclockwise",
    4: "horizontal-flip",
    5: "vertical-flip",
    6: "upper-left-diagonal",
    7: "upper-right-diagonal",
}
# Operations that swap the x / y axes
BAYER_TRANSPOSING_OPS = {
    "clockwise", "counterclockwise", "upper-left-diagonal",
    "upper-right-diagonal"
}


def bayer_transform(pattern, op):
    """
    Return the bayer pattern after applying geometric operation op to the image
    pattern: ex "GRBG"
    op: key of BAYER_OPS, a gstreamer videoflip method (int or nick), or None
    """
    if pattern is None or op is None:
        return pattern
    if isinstance(op, int):
        op = VIDEOFLIP_METHODS.get(op)
        if op is None:
            raise ValueError(f"Unsupported videoflip method {op}")
    if op not in BAYER_OPS:
        raise ValueError(f"Unsupported bayer operation {op!r}")
    assert len(pattern) == 4
    return "".join(pattern["ABCD".index(c)] for c in BAYER_OPS[op])


def frame_to_array(buf, fmt, width, height, stride=None):
    """
    Interpret a packed gstreamer video frame as a numpy array

    Returns array of shape (height, width) for single channel formats
    or (height, width, channels) otherwise, in the native sample depth
    Channel order for ARGB64 is converted to RGB (alpha dropped)
    """
    info = FORMATS[fmt]
    bpp = info["bytes_per_pixel"]
    row_bytes = width * bpp
    if stride is None:
        stride = row_bytes
    if len(buf) < stride * (height - 1) + row_bytes:
        raise ValueError(
            "Short frame for %s %uw x %uh (stride %u): wanted %u, got %u" %
            (fmt, width, height, stride, stride * (height - 1) + row_bytes,
             len(buf)))
    a = np.frombuffer(buf, dtype=np.uint8, count=stride * height)
    a = a.reshape((height, stride))[:, :row_bytes]
    a = np.ascontiguousarray(a).view(info["dtype"])
    if info["channels"] == 1:
        a = a.reshape((height, width))
    else:
        a = a.reshape((height, width, info["channels"]))
    if fmt == "ARGB64":
        # Drop alpha
        a = np.ascontiguousarray(a[:, :, 1:4])
    return a


def to_rgb8(data, fmt, bayer_pattern=None):
    """
    Convert native depth data (from frame_to_array) to an 8 bit RGB numpy array
    Raw data is demosaiced if the bayer pattern is known, otherwise shown as gray
    """
    if data.dtype == np.uint16:
        # Samples are left aligned so take the high byte
        data8 = (data >> 8).astype(np.uint8)
    else:
        data8 = data
    if data8.ndim == 2:
        if bayer_pattern:
            import cv2
            return cv2.cvtColor(data8, bayer_pattern_cv2_code(bayer_pattern))
        return np.stack([data8] * 3, axis=-1)
    return data8


def decode_frame(buf, fmt, width, height, stride=None, bayer_pattern=None):
    """
    Decode a captured frame

    Returns (image, data)
    image: 8 bit RGB PIL image. Always present
    data: native depth numpy array or None for 8 bit RGB (image already holds it)
    """
    if fmt == DEFAULT_FORMAT and (stride is None or stride == width * 3):
        assert len(buf) >= width * height * 3, (
            "Wanted %u, got %u, w=%u, h=%u" %
            (width * height * 3, len(buf), width, height))
        image = Image.frombytes('RGB', (width, height),
                                bytes(buf[0:width * height * 3]), 'raw',
                                'RGB')
        return image, None
    data = frame_to_array(buf, fmt, width, height, stride=stride)
    rgb8 = to_rgb8(data, fmt, bayer_pattern=bayer_pattern)
    image = Image.fromarray(rgb8)
    if fmt == DEFAULT_FORMAT:
        return image, None
    return image, data
