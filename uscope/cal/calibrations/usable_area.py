"""Largest usable rectangle from a flat-field illumination map."""

import os

import numpy as np
from PIL import Image

from uscope.cal.analysis.illumination import IlluminationTables, usable_rect
from uscope.cal.base import Calibration, Check, Quality, Result, ScopeKind
from uscope.cal.store import CalEntry


class UsableAreaCal(Calibration):
    name = "usable_area"
    title = "Usable sensor area"
    description = "Suggest a crop from the flat-field image circle."
    scope_kind = ScopeKind.OBJECTIVE
    soft_requires = ("flat_field", )

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        return 15

    def preflight(self, ctx):
        return [
            Check("Flat field", True,
                  "Uses the flat-field dataset or a few frames of a uniform target",
                  blocking=False),
        ]

    def collect(self, ctx, ds, quality=Quality.FULL):
        # Prefer reusing a sibling flat_field dataset
        sibling = os.path.join(os.path.dirname(ds.path), "flat_field")
        if os.path.exists(os.path.join(sibling, "ff.npz")):
            ds.manifest["params"] = {"from": sibling}
            ds.save_manifest()
            return
        obj = ctx.objective_config()
        ds.manifest["objective"] = obj
        ds.manifest["params"] = {"n": 3}
        ds.save_manifest()
        for i in range(3):
            ctx.check_running()
            cap = ctx.image_captured(mode="raw")
            basename = "frame_%03u.tif" % i
            cap.save(ds.capture_path(basename))
            ds.manifest["captures"].append({
                "fn": "images/%s" % basename,
                "index": i
            })
            ctx.sleep(0.2)
        ds.save_manifest()

    def analyze(self, ds, log=None):
        log = log or (lambda s: None)
        tables = self._tables(ds)
        if tables is None:
            return Result(self.name,
                          ok=False,
                          message="No flat-field data to measure")
        # relative illumination ≈ 1/gain (clipped)
        rel = 1.0 / np.maximum(tables.gain, 1e-3)
        if rel.ndim == 3:
            rel2 = rel.mean(axis=2)
        else:
            rel2 = rel
        crop = usable_rect(rel2, threshold=0.5, margin=8, even=True)
        if crop is None:
            return Result(self.name,
                          ok=False,
                          message="Could not find a usable rectangle")
        h, w = rel2.shape
        used = (w - crop["left"] - crop["right"]) * (h - crop["top"] -
                                                     crop["bottom"])
        frac = used / float(w * h)
        log("Suggested crop %s (kept %0.1f%%)" % (crop, 100 * frac))
        try:
            prev = np.clip(rel2 / (rel2.max() or 1) * 255, 0, 255).astype(
                np.uint8)
            im = Image.fromarray(prev).convert("RGB")
            from PIL import ImageDraw
            d = ImageDraw.Draw(im)
            d.rectangle([
                crop["left"], crop["top"], w - crop["right"] - 1,
                h - crop["bottom"] - 1
            ],
                        outline=(255, 0, 0),
                        width=3)
            im.save(os.path.join(ds.path, "usable_area.png"))
        except Exception:
            pass
        scope = {}
        obj = (ds.manifest.get("objective") or {}).get("name")
        if obj:
            scope["objective"] = obj
        entry = CalEntry(key="imager.crop_pixels",
                         value=crop,
                         scope=scope,
                         needs_restart=True)
        ok = frac > 0.4
        return Result(self.name,
                      values={
                          "crop": crop,
                          "kept_frac": frac
                      },
                      ok=ok,
                      message="crop %s (kept %0.0f%%)" % (crop, 100 * frac),
                      entries=[entry])

    def _tables(self, ds):
        sibling = (ds.manifest.get("params") or {}).get("from")
        if sibling:
            npz = os.path.join(sibling, "ff.npz")
            if os.path.exists(npz):
                return IlluminationTables.load(npz)
        npz = os.path.join(os.path.dirname(ds.path), "flat_field", "ff.npz")
        if os.path.exists(npz):
            return IlluminationTables.load(npz)
        recs = ds.captures()
        if recs:
            from uscope.cal.analysis.illumination import stack_median, gain_map
            images = [Image.open(ds.capture_abspath(r)) for r in recs]
            try:
                flat = stack_median(images)
            finally:
                for im in images:
                    im.close()
            return IlluminationTables(gain=gain_map(flat))
        return None
