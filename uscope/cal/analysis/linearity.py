"""Exposure response fit: v = a * t^gamma + b."""

import numpy as np

from uscope.cal.analysis.fitting import linear_fit


def fit_response(exposures, values, min_frac=0.05, max_frac=0.90):
    """Fit v = a * t^gamma + b on the linear-ish portion of a sweep.

    exposures: commanded exposure (same units the camera uses)
    values: measured mean (0-1 normalized recommended)

    Returns a dict with a, gamma, black_level (b), linear_max, residuals.
    """
    t = np.asarray(exposures, dtype=float)
    v = np.asarray(values, dtype=float)
    if len(t) < 3:
        raise ValueError("Need at least 3 exposure samples")

    vmin, vmax = float(v.min()), float(v.max())
    span = max(vmax - vmin, 1e-9)
    lo = vmin + min_frac * span
    hi = vmin + max_frac * span
    mask = (v >= lo) & (v <= hi)
    if mask.sum() < 3:
        mask = np.ones(len(v), dtype=bool)

    t_fit = t[mask]
    v_fit = v[mask]
    m, bb = linear_fit(t_fit, v_fit)
    pred_lin = m * t + bb
    rms_lin = float(np.sqrt(np.mean((v - pred_lin)**2)))
    a, gamma, b, rms = m, 1.0, bb, rms_lin
    try:
        from scipy.optimize import curve_fit

        def power(tt, aa, gg, bb):
            return aa * np.power(np.maximum(tt, 1e-9), gg) + bb

        popt, _pcov = curve_fit(power,
                                t_fit,
                                v_fit,
                                p0=(max(m, 1e-6), 1.0, min(bb, float(v.min()))),
                                bounds=([0, 0.2, -0.2], [np.inf, 3.0, 1.0]),
                                maxfev=5000)
        pred = power(t, *popt)
        rms_p = float(np.sqrt(np.mean((v - pred)**2)))
        if rms_p <= rms_lin * 0.98:
            a, gamma, b, rms = float(popt[0]), float(popt[1]), float(
                popt[2]), rms_p
    except Exception:
        pass
    # Saturation knee: first sample above max_frac of peak
    sat = v >= hi
    linear_max = float(v[sat][0]) if sat.any() else float(v.max())
    return {
        "model": "power",
        "a": a,
        "gamma": float(gamma),
        "black_level": b,
        "linear_max": linear_max,
        "linear_min": float(lo),
        "n": int(len(t)),
        "rms": rms,
    }


def invert_response(target, model, exposure_now=None, average_now=None):
    """Exposure that should produce `target` (same units as fit values).

    Falls back to a simple ratio if the model is unusable.
    """
    a = float(model.get("a") or 0)
    gamma = float(model.get("gamma") or 1.0)
    b = float(model.get("black_level") or 0)
    resid = target - b
    if a <= 0 or resid <= 0 or gamma == 0:
        if exposure_now and average_now:
            return max(1.0, exposure_now * target / max(average_now, 1e-3))
        return None
    t = (resid / a)**(1.0 / gamma)
    if not np.isfinite(t) or t <= 0:
        if exposure_now and average_now:
            return max(1.0, exposure_now * target / max(average_now, 1e-3))
        return None
    return float(t)


def apply_linearize(value, model):
    """Map a measured 0-1 value toward a linearized 0-1 using the inverse model."""
    a = float(model.get("a") or 0)
    gamma = float(model.get("gamma") or 1.0)
    b = float(model.get("black_level") or 0)
    linear_max = float(model.get("linear_max") or 1.0)
    if a <= 0:
        return value
    resid = max(value - b, 0.0)
    # Invert v = a t^gamma + b for a "normalized exposure", then scale
    t = (resid / a)**(1.0 / gamma) if resid > 0 else 0.0
    t_max = ((linear_max - b) / a)**(1.0 / gamma) if linear_max > b else 1.0
    if t_max <= 0:
        return value
    return float(np.clip(t / t_max, 0.0, 1.0))
