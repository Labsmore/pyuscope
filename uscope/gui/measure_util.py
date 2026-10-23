"""
Pure geometry helpers for the Measure tab annotations
Points are (x, y) tuples, rects are (x, y, w, h) tuples
"""

import math


def point_segment_distance(p, a, b):
    """
    Shortest distance from point p to the line segment a-b
    """
    ax, ay = a
    bx, by = b
    px, py = p
    dx = bx - ax
    dy = by - ay
    len2 = dx * dx + dy * dy
    if len2 == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / len2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def label_normal(a, b):
    """
    Unit vector perpendicular to a-b on the side labels go
    Labels go above the line, or to the right of a vertical line
    """
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length = math.hypot(dx, dy)
    if length == 0:
        return (0.0, -1.0)
    nx = dy / length
    ny = -dx / length
    if ny > 0 or (ny == 0 and nx < 0):
        nx, ny = -nx, -ny
    # Avoid -0.0 leaking out
    return (nx + 0.0, ny + 0.0)


def _rect_inside(rect, bounds):
    x, y, w, h = rect
    bx, by, bw, bh = bounds
    return x >= bx and y >= by and x + w <= bx + bw and y + h <= by + bh


def label_rect(a, b, size, offset=6, bounds=None):
    """
    Place a label of size (w, h) next to the segment a-b

    The label is centered on the segment midpoint and pushed out along the
    normal so that its nearest edge is offset away from the line, independent
    of line angle and label size
    If bounds (x, y, w, h) is given the label flips to the other side of the
    line when that keeps it inside, otherwise it is clamped to bounds
    Returns (x, y, w, h) of the label's top left corner and size
    """
    w, h = size
    nx, ny = label_normal(a, b)
    # Distance from the label center to its edge along the normal
    extent = abs(nx) * w / 2 + abs(ny) * h / 2
    mx = (a[0] + b[0]) / 2
    my = (a[1] + b[1]) / 2

    def place(sign):
        cx = mx + sign * nx * (offset + extent)
        cy = my + sign * ny * (offset + extent)
        return (cx - w / 2, cy - h / 2, w, h)

    rect = place(1)
    if bounds is None or _rect_inside(rect, bounds):
        return rect
    flipped = place(-1)
    if _rect_inside(flipped, bounds):
        return flipped
    x, y, w, h = rect
    bx, by, bw, bh = bounds
    x = max(bx, min(x, bx + bw - w))
    y = max(by, min(y, by + bh - h))
    return (x, y, w, h)
