"""Small fitting helpers used by several calibrations."""

import numpy as np


def linear_fit(xs, ys):
    """Return (m, b) for y = m x + b."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    if len(xs) < 2:
        raise ValueError("Need at least 2 points for a linear fit")
    m, b = np.polyfit(xs, ys, 1)
    return float(m), float(b)


def invert_linear(m, b, y=0.0):
    """Solve y = m x + b for x."""
    if m == 0:
        raise ValueError("Zero slope")
    return (y - b) / m


def peak_quadratic(xs, ys):
    """Fit y = a x^2 + b x + c near the max and return (x_peak, y_peak)."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    if len(xs) < 3:
        i = int(np.argmax(ys))
        return float(xs[i]), float(ys[i])
    i = int(np.argmax(ys))
    lo = max(0, i - 2)
    hi = min(len(xs), i + 3)
    if hi - lo < 3:
        return float(xs[i]), float(ys[i])
    a, b, c = np.polyfit(xs[lo:hi], ys[lo:hi], 2)
    if a >= 0:
        return float(xs[i]), float(ys[i])
    x_peak = -b / (2 * a)
    x_peak = float(np.clip(x_peak, xs[lo], xs[hi - 1]))
    y_peak = float(a * x_peak**2 + b * x_peak + c)
    return x_peak, y_peak


def distance(p1, p2):
    return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)**0.5
