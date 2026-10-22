"""Focus metric and settling-time helpers."""

import cv2
import numpy as np
from PIL import Image


def focus_score(im, blur=9, take_center=True):
    """Laplacian variance of the (optionally center-cropped) image."""
    if isinstance(im, Image.Image):
        if take_center:
            w, h = im.size
            im = im.crop(((w - w / 3) / 2, (h - h / 3) / 2,
                          (w + w / 3) / 2, (h + h / 3) / 2))
        arr = np.array(im.convert("L"))
    else:
        arr = np.asarray(im)
        if arr.ndim == 3:
            arr = arr.mean(axis=2)
        if take_center:
            h, w = arr.shape[:2]
            arr = arr[h // 3:2 * h // 3, w // 3:2 * w // 3]
        if arr.dtype != np.uint8:
            arr = np.clip(arr / (arr.max() or 1) * 255, 0, 255).astype(np.uint8)
        else:
            arr = arr.astype(np.uint8)
    filtered = cv2.medianBlur(arr, blur)
    laplacian = cv2.Laplacian(filtered, cv2.CV_64F)
    return float(laplacian.var())


def settle_time(times, values, tolerance, min_hold=0.0):
    """First time after which |v - v_final| stays within tolerance.

    times/values are sequences, times in seconds from the move.
    Returns (t_settle, settled) — settled is False if it never holds.
    """
    times = np.asarray(times, dtype=float)
    values = np.asarray(values, dtype=float)
    if len(times) == 0:
        return 0.0, False
    final = values[-1]
    # Walk backward to the last violation, then forward one
    last_bad = -1
    for i, v in enumerate(values):
        if abs(v - final) > tolerance:
            last_bad = i
    if last_bad < 0:
        t = float(times[0])
        return t, True
    if last_bad >= len(times) - 1:
        return float(times[-1]), False
    t = float(times[last_bad + 1])
    if min_hold and (times[-1] - t) < min_hold:
        return float(times[-1]), False
    return t, True
