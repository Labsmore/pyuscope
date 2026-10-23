"""Software white balance from a white target."""

import cv2
import numpy as np

from uscope.cal.analysis.color import compute_white_balance
from uscope.cal.base import Calibration, Check, Quality, Result, ScopeKind
from uscope.cal.store import CalEntry

# Camera controls that change color: gains are only valid for these values
COLOR_PROPERTIES = (
    "Auto White Balance Temperature",
    "White Balance Temperature",
    "Auto white balance",
    "Auto white balance (AWB)",
    "White balance (red)",
    "White balance (green)",
    "White balance (blue)",
)


def load_rgb(fn):
    """8 or 16 bit RGB numpy array (PIL can't read 16 bit RGB)."""
    arr = cv2.imread(fn, cv2.IMREAD_UNCHANGED)
    if arr is None:
        raise ValueError("Failed to read %s" % (fn, ))
    if arr.ndim == 3:
        arr = cv2.cvtColor(arr[:, :, 0:3], cv2.COLOR_BGR2RGB)
    return arr


def average_frames(arrs):
    arrs = [np.asarray(a) for a in arrs]
    dtype = arrs[0].dtype
    mean = np.mean(np.stack([a.astype(np.float64) for a in arrs]), axis=0)
    if np.issubdtype(dtype, np.integer):
        mean = np.rint(mean)
    return mean.astype(dtype)


class WhiteBalanceCal(Calibration):
    name = "white_balance"
    title = "White balance"
    description = ("Per-channel software gains from a white target. "
                   "For cameras without RGB gain control (ex: UVC).")
    scope_kind = ScopeKind.UNIT
    needs_flat = True

    roi_fraction = 0.5
    normalize = "green"

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        return 5 if quality == Quality.QUICK else 15

    def preflight(self, ctx):
        return [
            Check("White target", True,
                  "Center a white target, set exposure to avoid saturation, "
                  "and fix the camera color temperature (auto WB off)",
                  blocking=False),
        ]

    def collect(self, ctx, ds, quality=Quality.FULL):
        n = 3 if quality == Quality.QUICK else 8
        props = ctx.get_disp_properties() or {}
        ds.manifest["params"] = {
            "n": n,
            "imager_properties":
            {k: props[k]
             for k in COLOR_PROPERTIES if k in props},
        }
        ds.save_manifest()
        for i in range(n):
            ctx.check_running()
            ctx.progress((i + 1) / n, "White balance %u/%u" % (i + 1, n))
            cap = ctx.image_captured(mode="raw")
            basename = "white_%03u.tif" % i
            fn = ds.capture_path(basename)
            data = getattr(cap, "data", None)
            if data is not None and data.ndim == 2:
                # Raw bayer mosaic: use the demosaiced image
                cap.image.save(fn)
            else:
                cap.save(fn)
            ds.manifest["captures"].append({
                "fn": "images/%s" % basename,
                "index": i,
            })
        ds.save_manifest()

    def analyze(self, ds, log=None):
        log = log or (lambda s: None)
        recs = ds.captures()
        if not recs:
            return Result(self.name, ok=False, message="No frames")
        arr = average_frames([load_rgb(ds.capture_abspath(r)) for r in recs])
        if arr.ndim != 3:
            return Result(self.name,
                          ok=False,
                          message="Need color frames, got grayscale")
        try:
            wb = compute_white_balance(arr,
                                       roi_fraction=self.roi_fraction,
                                       normalize=self.normalize)
        except ValueError as e:
            # ex: a black channel
            return Result(self.name,
                          ok=False,
                          message="Bad white target image: %s" % (e, ))
        gains = wb["gains"]
        log("White balance levels %s => gains %s" %
            (["%0.1f" % x for x in wb["levels"]], ["%0.3f" % x
                                                   for x in gains]))
        problems = []
        if wb["saturated_fraction"] > 0.01:
            problems.append("%0.1f%% of target saturated: reduce exposure" %
                            (100 * wb["saturated_fraction"], ))
        if min(wb["levels_frac"]) < 0.05:
            problems.append("target too dark: increase exposure")
        if max(gains) / min(gains) > 8:
            problems.append("extreme gain ratio: is the target white?")
        value = {
            "gains": gains,
            "levels": wb["levels"],
            "normalize": wb["normalize"],
            "roi_fraction": wb["roi_fraction"],
            "imager_properties": (ds.manifest.get("params")
                                  or {}).get("imager_properties", {}),
        }
        entry = CalEntry(key="imager.white_balance", value=value, n=len(recs))
        values = {"gain_r": gains[0], "gain_g": gains[1], "gain_b": gains[2]}
        message = "gains R %0.3f G %0.3f B %0.3f" % tuple(gains)
        if problems:
            message += " (" + "; ".join(problems) + ")"
        return Result(self.name,
                      values=values,
                      ok=not problems,
                      message=message,
                      stats={
                          "levels": wb["levels"],
                          "saturated_fraction": wb["saturated_fraction"]
                      },
                      entries=[entry])
