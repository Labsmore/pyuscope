"""Flat field from a grid of images of a uniform target."""

import os

import numpy as np
from PIL import Image

from uscope.cal.analysis.illumination import (
    IlluminationTables,
    gain_map,
    stack_median,
)
from uscope.cal.base import Calibration, Check, Quality, Result, ScopeKind
from uscope.cal.store import CalEntry


class FlatFieldCal(Calibration):
    name = "flat_field"
    title = "Flat field"
    description = "Grid-average a uniform target into a per-pixel gain map."
    scope_kind = ScopeKind.OBJECTIVE_IMAGER
    soft_requires = ("dark_frame", )
    needs_flat = True

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        return 80 if quality == Quality.QUICK else 250

    def preflight(self, ctx):
        obj = ctx.objective_config()
        return [
            Check("Objective selected", bool(obj),
                  obj.get("name") if obj else "Select an objective"),
            Check("FF target", True,
                  "Place a uniform white/gray target in the field",
                  blocking=False),
        ]

    def collect(self, ctx, ds, quality=Quality.FULL):
        obj = ctx.objective_config()
        sites = 5 if quality == Quality.QUICK else 10
        x_pitch = obj["x_view"] * 0.7
        y_pitch = obj["y_view"] * 0.7
        ds.manifest["params"] = {
            "x_sites": sites,
            "y_sites": sites,
            "x_pitch": x_pitch,
            "y_pitch": y_pitch,
        }
        ds.manifest["objective"] = obj
        ds.save_manifest()
        origin = ctx.save_origin()
        total = sites * sites
        i = 0
        for row in range(sites):
            for col in range(sites):
                ctx.check_running()
                pos = {
                    "x": origin["x"] + col * x_pitch,
                    "y": origin["y"] + (sites - row - 1) * y_pitch,
                }
                ctx.move_absolute(pos)
                basename = "c%03u_r%03u.tif" % (col, row)
                ctx.image_captured(mode="raw").save(ds.capture_path(basename))
                ds.manifest["captures"].append({
                    "fn": "images/%s" % basename,
                    "col": col,
                    "row": row,
                    "pos": pos,
                })
                i += 1
                ctx.progress(i / total, "FF %u/%u" % (i, total))
        ds.save_manifest()

    def analyze(self, ds, log=None):
        log = log or (lambda s: None)
        recs = ds.captures()
        if len(recs) < 2:
            return Result(self.name, ok=False, message="Need at least 2 frames")
        images = [Image.open(ds.capture_abspath(r)) for r in recs]
        try:
            flat = stack_median(images)
        finally:
            for im in images:
                im.close()
        dark = self._load_dark(ds)
        gain = gain_map(flat, dark=dark, reference="percentile", percentile=90)
        if dark is not None:
            residual = np.maximum(flat - dark, 0)
        else:
            residual = flat
        # Preview
        prev = residual
        if prev.ndim == 3:
            prev = prev.mean(axis=2)
        mx = prev.max() or 1
        Image.fromarray(
            np.clip(prev / mx * 255, 0, 255).astype(np.uint8)).save(
                os.path.join(ds.path, "ff_preview.png"))
        tables = IlluminationTables(gain=gain, dark=dark)
        npz = os.path.join(ds.path, "ff.npz")
        tables.save(npz)
        ratio = float(np.percentile(prev, 90) / max(np.percentile(prev, 10), 1e-6))
        log("FF 90/10 ratio %0.2f" % ratio)
        scope = {}
        obj = (ds.manifest.get("objective") or {}).get("name")
        if obj:
            scope["objective"] = obj
        imager = ds.manifest.get("imager") or {}
        if imager.get("width") and imager.get("height"):
            scope["wh"] = [imager["width"], imager["height"]]
        entry = CalEntry(
            key="imager.illumination",
            value={"ratio_90_10": ratio},
            scope=scope,
            extra={"dataset_file": npz},
        )
        ok = 1.0 <= ratio < 8.0
        return Result(self.name,
                      values={"ratio_90_10": ratio},
                      ok=ok,
                      message="90/10 illumination ratio %0.2f" % ratio,
                      entries=[entry],
                      extra={"tables": tables, "npz": npz})

    def _load_dark(self, ds):
        sibling = os.path.join(os.path.dirname(ds.path), "dark_frame", "dark.npz")
        if os.path.exists(sibling):
            t = IlluminationTables.load(sibling)
            return t.dark
        return None

    def apply(self, result, store, source=None):
        if not result.entries:
            return []
        tables = result.extra.get("tables")
        if tables is None:
            return []
        # Merge store dark if we didn't have one
        if tables.dark is None:
            dark_e = store.get("imager.dark", result.entries[0].scope)
            if dark_e is not None:
                path = store.illumination_abspath(dark_e)
                if path and os.path.exists(path):
                    dt = IlluminationTables.load(path)
                    tables = IlluminationTables(gain=tables.gain,
                                                dark=dt.dark,
                                                mask=dt.mask)
        rel = self._store_name(result)
        dest = os.path.join(store.dir, rel)
        tables.save(dest)
        entry = result.entries[0]
        entry.file = rel
        entry.source = source
        store.set(entry)
        return [entry]

    def _store_name(self, result):
        scope = result.entries[0].scope or {}
        obj = scope.get("objective") or "any"
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(obj))
        wh = scope.get("wh")
        suffix = "%sx%s" % (wh[0], wh[1]) if wh else "default"
        return os.path.join("illumination", "ff_%s_%s.npz" % (safe, suffix))
