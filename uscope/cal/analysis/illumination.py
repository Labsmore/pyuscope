"""Dark frame, flat-field gain, usable-area, and correction tables."""

import os

import cv2
import numpy as np
from PIL import Image


def _as_float(im):
    if isinstance(im, Image.Image):
        arr = np.array(im, dtype=np.float64)
    else:
        arr = np.asarray(im, dtype=np.float64)
    return arr


def stack_median(images):
    """Median-stack a sequence of PIL images or arrays. Returns float64."""
    arrays = [_as_float(im) for im in images]
    if not arrays:
        raise ValueError("No images to stack")
    stack = np.stack(arrays, axis=0)
    return np.median(stack, axis=0)


def dark_stats(dark, k_sigma=6.0, stuck_lo=0.0, stuck_hi=None):
    """Return (mean, std, mask) for a dark frame.

    mask is True for hot / stuck / unusable pixels.
    """
    dark = np.asarray(dark, dtype=np.float64)
    if dark.ndim == 3:
        lum = dark.mean(axis=2)
    else:
        lum = dark
    mean = float(np.mean(lum))
    std = float(np.std(lum))
    if stuck_hi is None:
        stuck_hi = np.iinfo(np.uint16).max if lum.max() > 255 else 255
    hot = lum > (mean + k_sigma * max(std, 1e-6))
    stuck = (lum <= stuck_lo) | (lum >= stuck_hi)
    mask = hot | stuck
    return mean, std, mask


def gain_map(flat, dark=None, reference="percentile", percentile=90.0):
    """Normalized gain (ref / (flat - dark)). 1.0 at the reference level.

    flat/dark are float arrays, 2D or HxWxC.
    Pixels at or below dark become 0 (masked later).
    """
    flat = np.asarray(flat, dtype=np.float64)
    if dark is not None:
        flat = flat - np.asarray(dark, dtype=np.float64)
    flat = np.maximum(flat, 0.0)
    if reference == "max":
        if flat.ndim == 3:
            ref = np.max(flat, axis=(0, 1), keepdims=True)
        else:
            ref = np.max(flat)
    else:
        if flat.ndim == 3:
            ref = np.percentile(flat, percentile, axis=(0, 1), keepdims=True)
        else:
            ref = np.percentile(flat, percentile)
    ref = np.maximum(ref, 1e-6)
    gain = ref / np.maximum(flat, 1e-6)
    # Don't boost dead pixels into infinity
    gain = np.where(flat <= 1e-6, 0.0, gain)
    return gain.astype(np.float32)


def usable_rect(gain, threshold=0.5, margin=8, even=True):
    """Largest inscribed axis-aligned rectangle above `threshold` of center.

    gain is a 2D (or HxWxC, averaged) relative-illumination map, typically
    1/gain_map so bright=good. If given a gain_map (bright center ~1, edges
    larger), invert first by passing relative=1/gain.

    Returns dict {top, bottom, left, right} crop pixels, or None.
    """
    g = np.asarray(gain, dtype=np.float64)
    if g.ndim == 3:
        g = g.mean(axis=2)
    # Treat values as relative illumination if median near 1 and min < 1
    # Caller should pass relative illumination (flat / ref), not gain.
    h, w = g.shape
    cy, cx = h // 2, w // 2
    center = g[max(0, cy - 8):cy + 8, max(0, cx - 8):cx + 8].mean()
    if center <= 0:
        return None
    rel = g / center
    good = rel >= threshold
    # Morphological cleanup
    kernel = np.ones((7, 7), np.uint8)
    good_u8 = good.astype(np.uint8) * 255
    good_u8 = cv2.morphologyEx(good_u8, cv2.MORPH_OPEN, kernel)
    good_u8 = cv2.morphologyEx(good_u8, cv2.MORPH_CLOSE, kernel)
    good = good_u8 > 0
    if not good.any():
        return None
    # Largest inscribed axis-aligned rectangle via histogram method
    rect = _largest_rect(good)
    if rect is None:
        return None
    top, left, bottom, right = rect
    top = min(h - 1, top + margin)
    left = min(w - 1, left + margin)
    bottom = max(top + 1, bottom - margin)
    right = max(left + 1, right - margin)
    if even:
        top -= top % 2
        left -= left % 2
        if (bottom - top) % 2:
            bottom -= 1
        if (right - left) % 2:
            right -= 1
    crop = {
        "top": int(top),
        "bottom": int(h - bottom),
        "left": int(left),
        "right": int(w - right),
    }
    if crop["top"] + crop["bottom"] >= h or crop["left"] + crop["right"] >= w:
        return None
    return crop


def _largest_rect(good):
    """good is a 2D bool array. Return (top, left, bottom, right) exclusive-bottom/right."""
    h, w = good.shape
    height = np.zeros(w, dtype=int)
    best = 0
    best_rect = None
    for y in range(h):
        height = np.where(good[y], height + 1, 0)
        stack = [-1]
        for x in range(w + 1):
            hx = height[x] if x < w else 0
            while stack[-1] != -1 and height[stack[-1]] > hx:
                hgt = height[stack.pop()]
                left = stack[-1] + 1
                area = hgt * (x - left)
                if area > best:
                    best = area
                    top = y - hgt + 1
                    best_rect = (top, left, y + 1, x)
            stack.append(x)
    return best_rect


class IlluminationTables:
    """Dark + gain + mask used at correction time. Shared / read-only."""

    _cache = {}
    _cache_lock = None

    def __init__(self, gain, dark=None, mask=None, source=None):
        self.gain = np.asarray(gain, dtype=np.float32)
        self.dark = None if dark is None else np.asarray(dark, dtype=np.float32)
        self.mask = None if mask is None else np.asarray(mask, dtype=bool)
        self.source = source
        if self.gain.ndim == 2:
            self.size = (self.gain.shape[1], self.gain.shape[0])
        else:
            self.size = (self.gain.shape[1], self.gain.shape[0])

    @classmethod
    def _lock(cls):
        if cls._cache_lock is None:
            import threading
            cls._cache_lock = threading.Lock()
        return cls._cache_lock

    @classmethod
    def get(cls, path):
        key = (os.path.realpath(path), os.path.getmtime(path))
        with cls._lock():
            ret = cls._cache.get(key)
            if ret is None:
                ret = cls.load(path)
                cls._cache[key] = ret
            return ret

    @classmethod
    def invalidate(cls):
        with cls._lock():
            cls._cache.clear()

    @classmethod
    def load(cls, path):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".npz":
            data = np.load(path)
            return cls(gain=data["gain"],
                       dark=data["dark"] if "dark" in data else None,
                       mask=data["mask"] if "mask" in data else None,
                       source=path)
        # Legacy RGB TIFF treated as a flat (no dark)
        im = Image.open(path)
        im.load()
        arr = np.array(im, dtype=np.float64)
        gain = gain_map(arr, reference="max")
        return cls(gain=gain, source=path)

    def save(self, path):
        parent = os.path.dirname(path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent)
        kw = {"gain": self.gain}
        if self.dark is not None:
            kw["dark"] = self.dark.astype(np.float32)
        if self.mask is not None:
            kw["mask"] = self.mask
        np.savez_compressed(path, **kw)

    def apply(self, image, bit_depth=None):
        """Return corrected array, same shape/dtype family as input (uint8/16)."""
        arr = np.asarray(image)
        orig_dtype = arr.dtype
        work = arr.astype(np.float32)
        if self.dark is not None:
            dark = self._match_shape(self.dark, work)
            work = work - dark
        gain = self._match_shape(self.gain, work)
        work = work * gain
        if self.mask is not None:
            work = self._inpaint(work, self.mask)
        if np.issubdtype(orig_dtype, np.integer):
            maxv = (1 << bit_depth) - 1 if bit_depth else np.iinfo(
                orig_dtype).max
            work = np.clip(work, 0, maxv)
            return work.astype(orig_dtype)
        return work

    @staticmethod
    def _match_shape(table, work):
        table = np.asarray(table)
        if table.shape == work.shape:
            return table
        if table.ndim == 2 and work.ndim == 3:
            return table[..., None]
        if table.ndim == 3 and work.ndim == 2:
            return table.mean(axis=2)
        if table.shape[:2] != work.shape[:2]:
            raise ValueError(
                "Illumination table size %s does not match image %s" %
                (table.shape, work.shape))
        return table

    @staticmethod
    def _inpaint(work, mask):
        mask = np.asarray(mask, dtype=bool)
        if mask.ndim == 3:
            mask = mask.any(axis=2)
        if not mask.any():
            return work
        out = np.array(work, copy=True)
        good = ~mask
        if not good.any():
            return out
        fill = np.median(out[good], axis=0)
        out[mask] = fill
        return out
