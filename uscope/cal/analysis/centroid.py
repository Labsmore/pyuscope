"""Sub-pixel centroid of a bright spot on a dark field."""

import cv2
import numpy as np
from PIL import Image


def find_centroid(im, show=None, threshold=127):
    """Return (x, y) centroid in pixel coordinates (sub-pixel).

    im: PIL image or numpy array.
    """
    arr = _as_gray(im)
    return find_centroid_array(arr, show=show, threshold=threshold)


def find_centroid_fn(fn, show=None, threshold=127):
    im = cv2.imread(fn, cv2.IMREAD_GRAYSCALE)
    if im is None:
        raise ValueError("Failed to read image: %s" % fn)
    return find_centroid_array(im, show=show, threshold=threshold)


def find_centroid_array(im_gray, show=None, threshold=127):
    if im_gray.ndim != 2:
        raise ValueError("Expected grayscale array")
    # 16-bit: scale threshold
    if im_gray.dtype != np.uint8:
        maxv = float(np.iinfo(im_gray.dtype).max) if np.issubdtype(
            im_gray.dtype, np.integer) else float(im_gray.max() or 1)
        thresh = threshold / 255.0 * maxv
        im_bin = np.where(im_gray >= thresh, 255, 0).astype(np.uint8)
    else:
        _ret, im_bin = cv2.threshold(im_gray, threshold, 255, cv2.THRESH_BINARY)
    kernel = np.ones((5, 5), np.uint8)
    im_wip = cv2.dilate(im_bin, kernel)
    for _i in range(2):
        im_wip = cv2.erode(im_wip, kernel)

    moments = cv2.moments(im_wip)
    if moments["m00"] <= 0:
        raise ValueError("Failed to find centroid")
    x = moments["m10"] / moments["m00"]
    y = moments["m01"] / moments["m00"]

    if show:
        show(im_wip, "Centroid threshold")
    return float(x), float(y)


def _as_gray(im):
    if isinstance(im, Image.Image):
        return np.array(im.convert("L"))
    arr = np.asarray(im)
    if arr.ndim == 3:
        # RGB or BGR — use luma-ish mean
        return arr.mean(axis=2).astype(arr.dtype)
    return arr
