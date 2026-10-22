"""Dark frame + hot/stuck pixel mask."""

import os

import numpy as np
from PIL import Image

from uscope.cal.analysis.illumination import IlluminationTables, dark_stats, stack_median
from uscope.cal.base import Calibration, Check, Quality, Result, ScopeKind
from uscope.cal.store import CalEntry


class DarkFrameCal(Calibration):
    name = "dark_frame"
    title = "Dark frame"
    description = "Median stack with light blocked; flags hot and stuck pixels."
    scope_kind = ScopeKind.OBJECTIVE_IMAGER
    needs_dark = True

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        return 20 if quality == Quality.QUICK else 60

    def preflight(self, ctx):
        return [
            Check("Light blocked", True,
                  "Block the light path (cap or illumination off) before collect",
                  blocking=False),
        ]

    def collect(self, ctx, ds, quality=Quality.FULL):
        n = 8 if quality == Quality.QUICK else 24
        ans = ctx.prompt(
            "Block the light",
            "Cover the objective or turn illumination fully off, then continue.",
            buttons=("Continue", "Skip"))
        if ans == "Skip":
            ds.manifest["params"] = {"skipped": True, "n": 0}
            ds.save_manifest()
            return
        ds.manifest["params"] = {"n": n}
        ds.save_manifest()
        for i in range(n):
            ctx.check_running()
            ctx.progress((i + 1) / n, "Dark %u/%u" % (i + 1, n))
            cap = ctx.image_captured(mode="raw")
            basename = "dark_%03u.tif" % i
            cap.save(ds.capture_path(basename))
            ds.manifest["captures"].append({
                "fn": "images/%s" % basename,
                "index": i,
                "exposure": cap.exposure() if hasattr(cap, "exposure") else None,
            })
        ds.save_manifest()

    def analyze(self, ds, log=None):
        log = log or (lambda s: None)
        if (ds.manifest.get("params") or {}).get("skipped"):
            return Result(self.name,
                          ok=True,
                          message="Skipped",
                          entries=[])
        recs = ds.captures()
        if len(recs) < 2:
            return Result(self.name, ok=False, message="Need at least 2 frames")
        images = [Image.open(ds.capture_abspath(r)) for r in recs]
        try:
            dark = stack_median(images)
        finally:
            for im in images:
                im.close()
        mean, std, mask = dark_stats(dark)
        n_hot = int(mask.sum())
        log("Dark mean %0.1f std %0.2f hot/stuck %u" % (mean, std, n_hot))
        tables = IlluminationTables(gain=np.ones(dark.shape, dtype=np.float32),
                                    dark=dark,
                                    mask=mask)
        out_fn = os.path.join(ds.path, "dark.npz")
        tables.save(out_fn)
        # Also a preview
        try:
            preview = dark
            if preview.ndim == 3:
                preview = preview.mean(axis=2)
            mx = preview.max() or 1
            Image.fromarray(
                np.clip(preview / mx * 255, 0, 255).astype(np.uint8)).save(
                    os.path.join(ds.path, "dark_preview.png"))
        except Exception:
            pass
        scope = {}
        obj = (ds.manifest.get("objective") or {}).get("name")
        if obj:
            scope["objective"] = obj
        imager = ds.manifest.get("imager") or {}
        if imager.get("width") and imager.get("height"):
            scope["wh"] = [imager["width"], imager["height"]]
        fmt = imager.get("pixel_format")
        if fmt:
            scope["pixel_format"] = fmt
        entry = CalEntry(
            key="imager.dark",
            value={
                "mean": mean,
                "std": std,
                "n_hot": n_hot,
            },
            scope=scope,
            file="dark.npz" if False else None,
            n=len(recs),
        )
        # Store as illumination fragment; apply merges with FF later.
        # Keep a dedicated key so FF can find it.
        entry.file = os.path.relpath(out_fn, os.path.dirname(ds.path) and ds.path
                                     or ".")
        # file relative to dataset; apply() will copy into store dir
        entry.extra["dataset_file"] = out_fn
        entry.file = None
        ok = n_hot < 0.01 * mask.size
        return Result(self.name,
                      values={
                          "mean": mean,
                          "std": std,
                          "n_hot": n_hot
                      },
                      ok=ok,
                      message="mean %0.1f, %u hot/stuck" % (mean, n_hot),
                      entries=[entry],
                      extra={"tables": tables, "npz": out_fn})

    def apply(self, result, store, source=None):
        if not result.entries:
            return []
        tables = result.extra.get("tables")
        npz = result.extra.get("npz")
        if tables is None and npz:
            tables = IlluminationTables.load(npz)
        if tables is None:
            return super().apply(result, store, source=source)
        rel = self._store_illum_name(result)
        dest = os.path.join(store.dir, rel)
        tables.save(dest)
        entry = result.entries[0]
        entry.key = "imager.dark"
        entry.file = rel
        entry.source = source
        store.set(entry)
        return [entry]

    def _store_illum_name(self, result):
        entry = result.entries[0]
        obj = (entry.scope or {}).get("objective") or "any"
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in obj)
        return os.path.join("illumination", "dark_%s.npz" % safe)
