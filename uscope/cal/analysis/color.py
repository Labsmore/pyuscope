"""Software white balance and gain: per-channel gains from a white target."""

import numpy as np

NORMALIZE_MODES = ("green", "max", "min")


def dtype_max(dtype):
    dtype = np.dtype(dtype)
    if np.issubdtype(dtype, np.integer):
        return np.iinfo(dtype).max
    return 1.0


def center_roi(arr, fraction=0.5):
    """Central region covering `fraction` of the width and height."""
    arr = np.asarray(arr)
    if not 0.0 < fraction <= 1.0:
        raise ValueError("Bad ROI fraction %s" % (fraction, ))
    h, w = arr.shape[:2]
    rh = max(1, int(round(h * fraction)))
    rw = max(1, int(round(w * fraction)))
    top = (h - rh) // 2
    left = (w - rw) // 2
    return arr[top:top + rh, left:left + rw]


def channel_levels(arr, roi_fraction=0.5, percentile=None):
    """Per-channel level (mean, or percentile if given) of the central ROI.

    Saturated pixels (any channel at full scale) are excluded since they
    under-report the true level of the unsaturated channels.
    Returns (levels, saturated_fraction)
    """
    arr = np.asarray(arr)
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError("Need an RGB image, got shape %s" % (arr.shape, ))
    maxv = dtype_max(arr.dtype)
    roi = center_roi(arr[:, :, 0:3], roi_fraction).reshape(-1, 3)
    saturated = np.any(roi >= maxv, axis=1)
    saturated_fraction = float(saturated.mean())
    good = roi[~saturated]
    if len(good) == 0:
        good = roi
    good = good.astype(np.float64)
    if percentile is None:
        levels = good.mean(axis=0)
    else:
        levels = np.percentile(good, percentile, axis=0)
    return [float(x) for x in levels], saturated_fraction


def gains_from_levels(levels, normalize="green"):
    """Per-channel gains that make the measured levels equal.

    green: green gain is 1.0 (camera ISP convention)
    max: smallest gain is 1.0. Never darkens, may clip highlights
    min: largest gain is 1.0. Never clips, darkens
    """
    levels = np.asarray(levels, dtype=np.float64)
    if len(levels) != 3:
        raise ValueError("Need 3 levels, got %s" % (levels, ))
    if np.any(levels <= 0):
        raise ValueError("Channel level must be positive: %s" % (levels, ))
    if normalize == "green":
        ref = levels[1]
    elif normalize == "max":
        ref = levels.max()
    elif normalize == "min":
        ref = levels.min()
    else:
        raise ValueError("Bad normalize mode %s" % (normalize, ))
    return [float(ref / x) for x in levels]


def compute_white_balance(arr,
                          roi_fraction=0.5,
                          percentile=None,
                          normalize="green"):
    """Measure a white target image. Returns a dict suitable for storing."""
    arr = np.asarray(arr)
    levels, saturated_fraction = channel_levels(arr,
                                                roi_fraction=roi_fraction,
                                                percentile=percentile)
    return {
        "gains": gains_from_levels(levels, normalize=normalize),
        "levels": levels,
        # Levels relative to full scale, to judge exposure
        "levels_frac": [x / dtype_max(arr.dtype) for x in levels],
        "saturated_fraction": saturated_fraction,
        "roi_fraction": roi_fraction,
        "percentile": percentile,
        "normalize": normalize,
    }


def effective_gains(wb_gains=None, gain=1.0):
    """Combine white balance gains and a scalar gain. None if a no-op."""
    gain = float(gain if gain is not None else 1.0)
    if gain <= 0:
        raise ValueError("Gain must be positive: %s" % (gain, ))
    if wb_gains is None:
        gains = [gain, gain, gain]
    else:
        if len(wb_gains) != 3:
            raise ValueError("Need 3 gains, got %s" % (wb_gains, ))
        gains = [float(g) * gain for g in wb_gains]
    if any(g <= 0 for g in gains):
        raise ValueError("Gains must be positive: %s" % (gains, ))
    if all(abs(g - 1.0) < 1e-6 for g in gains):
        return None
    return gains


def _scale_channel(chan, gain, maxv):
    if np.issubdtype(chan.dtype, np.integer) and chan.dtype.itemsize <= 2:
        # LUT: exact rounding and much faster than float math on big frames
        lut = np.clip(np.rint(np.arange(maxv + 1, dtype=np.float64) * gain),
                      0, maxv).astype(chan.dtype)
        return lut[chan]
    out = chan.astype(np.float64) * gain
    if np.issubdtype(chan.dtype, np.integer):
        out = np.rint(out)
    return np.clip(out, 0, maxv).astype(chan.dtype)


def apply_gains(arr, gains, max_value=None):
    """Scale channels by gains, clipping to the dtype (or max_value) range.

    arr: HxW (gray) or HxWxC. Gray images use the mean gain since the channel
    can't be balanced. Channels past the third (ex: alpha) are untouched.
    Returns a new array of the same dtype.
    """
    arr = np.asarray(arr)
    maxv = dtype_max(arr.dtype) if max_value is None else max_value
    if gains is None:
        return arr.copy()
    if arr.ndim == 2:
        return _scale_channel(arr, float(np.mean(gains)), maxv)
    out = arr.copy()
    for i, gain in enumerate(gains[:min(3, arr.shape[2])]):
        out[:, :, i] = _scale_channel(arr[:, :, i], gain, maxv)
    return out


def apply_gains_pil(im, gains):
    """PIL wrapper for apply_gains(). Keeps the image mode."""
    from PIL import Image
    if gains is None:
        return im.copy()
    if im.mode in ("RGB", "RGBA", "L", "I;16"):
        return Image.fromarray(apply_gains(np.array(im), gains))
    if im.mode == "I":
        # 32 bit container, typically holding 16 bit samples
        arr = np.array(im).astype(np.int32)
        return Image.fromarray(apply_gains(arr, gains, max_value=0xFFFF))
    raise ValueError("Unsupported image mode %s" % (im.mode, ))
