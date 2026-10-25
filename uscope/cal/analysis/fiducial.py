"""
Fiducial (bright dot on a dark field) quality metrics

Scores an image against what find_centroid() needs: a single dot well above
the threshold, background well below it, fully in frame with room for the
scale calibration's moves
"""

import cv2
import numpy as np

from uscope.cal.analysis.centroid import _as_gray

# find_centroid() default threshold (8 bit)
THRESHOLD = 127
# The scale calibration moves the stage +/- this fraction of the field of view
MOVE_FRACTION = 0.15
# README: dot should fill about 1/6 to 1/3 of the image width
DIAMETER_MIN = 1 / 10
DIAMETER_MAX = 1 / 2
# Dot level (8 bit) where it reads as saturated / clearly above threshold
DOT_SATURATED = 245
DOT_TARGET = 252
DOT_MIN = 180
BG_MAX = 70
# Components smaller than this fraction of the frame are treated as specks
SPECK_FRACTION = 0.0005
EDGE_WIDTH_WARN = 0.03


class Issue:
    def __init__(self, name, fatal, text, hint):
        self.name = name
        self.fatal = fatal
        self.text = text
        self.hint = hint

    def line(self):
        return "%s: %s. %s" % ("FAIL" if self.fatal else "WARN", self.text,
                               self.hint)


def _components(gray, threshold):
    _ret, im_bin = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    kernel = np.ones((5, 5), np.uint8)
    im_bin = cv2.morphologyEx(im_bin, cv2.MORPH_OPEN, kernel)
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(im_bin)
    comps = []
    for i in range(1, n):
        comps.append({
            "label": i,
            "area": int(stats[i, cv2.CC_STAT_AREA]),
            "x": int(stats[i, cv2.CC_STAT_LEFT]),
            "y": int(stats[i, cv2.CC_STAT_TOP]),
            "w": int(stats[i, cv2.CC_STAT_WIDTH]),
            "h": int(stats[i, cv2.CC_STAT_HEIGHT]),
            "cx": float(centroids[i][0]),
            "cy": float(centroids[i][1]),
        })
    comps.sort(key=lambda c: -c["area"])
    return comps, labels


def dot_levels(im, threshold=THRESHOLD):
    """
    Quick (dot, background) 8 bit levels for exposure adjustment
    dot is None if nothing is above threshold
    """
    gray = _to_u8(_as_gray(im))
    comps, labels = _components(gray, threshold)
    bg = float(np.median(gray))
    if not comps:
        # Nothing above threshold: use the brightest blurred region instead
        peak = float(cv2.GaussianBlur(gray, (15, 15), 0).max())
        return None, bg, peak
    mask = labels == comps[0]["label"]
    mask = cv2.erode(mask.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    if not mask.any():
        mask = labels == comps[0]["label"]
    dot = float(np.median(gray[mask]))
    bg = float(np.median(gray[labels == 0]))
    return dot, bg, dot


def _to_u8(gray):
    if gray.dtype == np.uint8:
        return gray
    if np.issubdtype(gray.dtype, np.integer):
        return (gray.astype(np.float64) / np.iinfo(gray.dtype).max *
                255).astype(np.uint8)
    return np.clip(gray, 0, 255).astype(np.uint8)


def _edge_width(gray, mask, dot, bg):
    """
    Approximate 10-90% edge width in pixels from the gradient at the boundary
    Larger => blurrier
    """
    if dot - bg < 10:
        return None
    blur = cv2.GaussianBlur(gray.astype(np.float32), (3, 3), 0)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3) / 8
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3) / 8
    grad = np.hypot(gx, gy)
    m8 = mask.astype(np.uint8)
    ring = cv2.dilate(m8, np.ones((7, 7), np.uint8)) - cv2.erode(
        m8, np.ones((7, 7), np.uint8))
    vals = grad[ring > 0]
    if vals.size == 0:
        return None
    # Peak gradient along the edge, robust to noise
    g = float(np.percentile(vals, 90))
    if g <= 0:
        return None
    return 0.8 * (dot - bg) / g


def analyze_image(im, threshold=THRESHOLD, move_fraction=MOVE_FRACTION):
    """
    Score one image
    Returns dict with metrics, "issues" (list of Issue), "ok" (no fatal issues)
    """
    gray = _to_u8(_as_gray(im))
    h, w = gray.shape
    ret = {
        "width": w,
        "height": h,
        "threshold": threshold,
        "issues": [],
    }
    issues = ret["issues"]
    comps, labels = _components(gray, threshold)
    big = [c for c in comps if c["area"] >= SPECK_FRACTION * w * h]
    specks = len(comps) - len(big)
    ret["n_blobs"] = len(big)
    ret["n_specks"] = specks

    if not big:
        peak = float(cv2.GaussianBlur(gray, (15, 15), 0).max())
        ret.update({"bg_level": float(np.median(gray)), "peak_level": peak})
        if peak < threshold:
            hint = ("Nothing is above the threshold (%u). Increase exposure / "
                    "illumination or move the dot into view" % threshold)
        else:
            hint = "Only small specks above threshold. Center the dot in view"
        issues.append(
            Issue("dot_found", True, "No fiducial dot found", hint))
        ret["ok"] = False
        return ret

    dot_c = big[0]
    dot_mask = labels == dot_c["label"]
    core = cv2.erode(dot_mask.astype(np.uint8), np.ones(
        (9, 9), np.uint8)) > 0
    if not core.any():
        core = dot_mask
    bg_mask = cv2.dilate(dot_mask.astype(np.uint8), np.ones(
        (25, 25), np.uint8)) == 0
    for c in big[1:]:
        bg_mask &= labels != c["label"]
    if not bg_mask.any():
        bg_mask = ~dot_mask

    dot = float(np.median(gray[core]))
    bg = float(np.median(gray[bg_mask]))
    bg_hi = float(np.percentile(gray[bg_mask], 99))
    contrast = (dot - bg) / max(dot + bg, 1.0)
    diameter = 2.0 * (dot_c["area"] / np.pi)**0.5
    sat_frac = float(np.mean(gray[dot_mask] >= 254))
    edge = _edge_width(gray, dot_mask, dot, bg)
    ret.update({
        "dot_level": dot,
        "bg_level": bg,
        "bg_p99": bg_hi,
        "contrast": contrast,
        "margin_dot": dot - threshold,
        "margin_bg": threshold - bg,
        "dot_saturated_fraction": sat_frac,
        "centroid": (dot_c["cx"], dot_c["cy"]),
        "diameter_px": diameter,
        "diameter_frac": diameter / w,
        "bbox": (dot_c["x"], dot_c["y"], dot_c["w"], dot_c["h"]),
        "edge_width_px": edge,
    })

    # Contrast vs the fixed centroid threshold
    if dot < DOT_MIN:
        issues.append(
            Issue(
                "dot_level", True,
                "Dot too dim (level %0.0f, want >= %u)" % (dot, DOT_MIN),
                "Increase exposure / illumination (Auto exposure option)"))
    if bg > BG_MAX:
        issues.append(
            Issue(
                "bg_level", True,
                "Background too bright (level %0.0f, want <= %u)" %
                (bg, BG_MAX),
                "Lower exposure / illumination, or use a darker background"))
    elif bg_hi > threshold:
        issues.append(
            Issue(
                "bg_glare", False, "Background has bright areas near the "
                "threshold (99th percentile %0.0f)" % bg_hi,
                "Clean the target, reduce glare or lower exposure"))
    if contrast < 0.5 and not any(i.fatal for i in issues):
        issues.append(
            Issue("contrast", False, "Low contrast (%0.2f)" % contrast,
                  "Improve illumination or use a higher contrast target"))
    if dot < DOT_SATURATED and dot >= DOT_MIN and bg <= BG_MAX * 0.5:
        issues.append(
            Issue(
                "dot_saturated", False,
                "Dot not saturated (level %0.0f)" % dot,
                "Slightly more exposure would add margin above the threshold"))

    # Other bright objects pull the centroid
    if len(big) > 1:
        issues.append(
            Issue(
                "single_dot", True,
                "%u bright objects above threshold" % len(big),
                "Only the fiducial dot should be bright. Remove debris or "
                "move to a clean area"))
    elif specks > 3:
        issues.append(
            Issue("specks", False,
                  "%u small bright specks above threshold" % specks,
                  "Clean the target / lens"))

    # Size
    dfrac = diameter / w
    if dfrac < DIAMETER_MIN:
        issues.append(
            Issue(
                "dot_size", False,
                "Dot small (%0.0f px, %0.0f%% of width)" %
                (diameter, dfrac * 100),
                "Use a higher magnification or a larger dot (aim 1/6 to 1/3 "
                "of the width) for better centroid precision"))
    elif dfrac > DIAMETER_MAX:
        issues.append(
            Issue(
                "dot_size", True,
                "Dot too large (%0.0f%% of width)" % (dfrac * 100),
                "Use a lower magnification or a smaller dot (aim 1/6 to 1/3 "
                "of the width)"))

    # Must stay fully in frame during the scale moves
    x, y, bw, bh = dot_c["x"], dot_c["y"], dot_c["w"], dot_c["h"]
    if x <= 0 or y <= 0 or x + bw >= w or y + bh >= h:
        issues.append(
            Issue("in_frame", True, "Dot touches the image edge",
                  "Center the dot in the field of view"))
    else:
        move = move_fraction * w
        room = min(x, w - (x + bw)) - move, min(y, h - (y + bh)) - move
        ret["move_room_px"] = room
        if min(room) < 0:
            dx = dot_c["cx"] - w / 2
            dy = dot_c["cy"] - h / 2
            text = ("Dot would leave the frame during the +/- %u%% scale "
                    "moves" % (move_fraction * 100))
            if max(abs(dx), abs(dy)) > 0.02 * w:
                text += " (offset from center %+0.0f, %+0.0f px)" % (dx, dy)
                hint = "Center the dot (and / or use a smaller dot)"
            else:
                hint = "Use a smaller dot or lower magnification"
            issues.append(Issue("centered", True, text, hint))

    if edge is not None and edge > EDGE_WIDTH_WARN * w:
        issues.append(
            Issue("focus", False,
                  "Dot edge looks soft (~%0.0f px)" % edge,
                  "Focus on the dot (Autofocus option)"))
    ret["ok"] = not any(i.fatal for i in issues)
    return ret


def annotate(im, metrics, move_fraction=MOVE_FRACTION):
    """
    BGR image showing the thresholded dot, centroid and the region the dot
    must stay inside during the scale moves
    """
    gray = _to_u8(_as_gray(im))
    out = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    h, w = gray.shape
    thick = max(1, w // 400)
    _ret, im_bin = cv2.threshold(gray, metrics.get("threshold", THRESHOLD),
                                 255, cv2.THRESH_BINARY)
    contours, _h = cv2.findContours(im_bin, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
    color = (0, 200, 0) if metrics.get("ok") else (0, 0, 255)
    cv2.drawContours(out, contours, -1, color, thick)
    move = int(move_fraction * w)
    cv2.rectangle(out, (move, move), (w - 1 - move, h - 1 - move),
                  (255, 160, 0), thick)
    if "centroid" in metrics:
        cx, cy = metrics["centroid"]
        r = max(5, w // 80)
        cv2.line(out, (int(cx - r), int(cy)), (int(cx + r), int(cy)), color,
                 thick)
        cv2.line(out, (int(cx), int(cy - r)), (int(cx), int(cy + r)), color,
                 thick)
    return out
