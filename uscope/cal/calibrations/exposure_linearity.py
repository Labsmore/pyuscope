"""Prototype exposure-response sweep and fit."""

import os

import numpy as np
from PIL import Image

from uscope.cal.analysis.linearity import fit_response
from uscope.cal.base import Calibration, Check, Quality, Result, ScopeKind
from uscope.cal.store import CalEntry


class ExposureLinearityCal(Calibration):
    name = "exposure_linearity"
    title = "Exposure linearity"
    description = "Sweep exposure on a fixed scene and fit v = a t^γ + b."
    scope_kind = ScopeKind.OBJECTIVE_IMAGER

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        return 45 if quality == Quality.QUICK else 120

    def preflight(self, ctx):
        return [
            Check("Fixed scene", True,
                  "Use a static target; auto-exposure should be off",
                  blocking=False),
        ]

    def collect(self, ctx, ds, quality=Quality.FULL):
        n = 8 if quality == Quality.QUICK else 16
        repeats = 2 if quality == Quality.QUICK else 3
        current = ctx.get_exposure() or 100
        # Log-spaced around current, covering ~1/8x to 8x (clamped)
        lo = max(1, current / 8.0)
        hi = current * 8.0
        exposures = np.unique(
            np.round(np.geomspace(lo, hi, n)).astype(int)).tolist()
        ds.manifest["params"] = {
            "exposures": exposures,
            "repeats": repeats,
            "start_exposure": current,
        }
        ds.save_manifest()
        try:
            for i, exp in enumerate(exposures):
                ctx.check_running()
                ctx.set_exposure(int(exp))
                ctx.sleep(0.15)
                ctx.wait_imaging_ok()
                for r in range(repeats):
                    cap = ctx.image_captured(mode="raw")
                    basename = "e%06d_r%02u.tif" % (int(exp), r)
                    cap.save(ds.capture_path(basename))
                    ds.manifest["captures"].append({
                        "fn": "images/%s" % basename,
                        "exposure": int(exp),
                        "repeat": r,
                    })
                ctx.progress((i + 1) / len(exposures),
                             "Exposure %s" % exp)
        finally:
            try:
                ctx.set_exposure(int(current))
            except Exception:
                pass
        ds.save_manifest()

    def analyze(self, ds, log=None):
        log = log or (lambda s: None)
        recs = ds.captures()
        if len(recs) < 3:
            return Result(self.name, ok=False, message="Need more samples")
        by_exp = {}
        for r in recs:
            im = Image.open(ds.capture_abspath(r))
            try:
                arr = np.array(im)
                # Center ROI, normalize 0-1
                h, w = arr.shape[:2]
                roi = arr[h // 3:2 * h // 3, w // 3:2 * w // 3]
                maxv = 65535.0 if roi.dtype == np.uint16 else 255.0
                mean = float(np.mean(roi)) / maxv
            finally:
                im.close()
            by_exp.setdefault(r["exposure"], []).append(mean)
        exps = sorted(by_exp)
        means = [float(np.median(by_exp[e])) for e in exps]
        model = fit_response(exps, means)
        log("gamma=%0.3f black=%0.3f rms=%0.4f" %
            (model["gamma"], model["black_level"], model["rms"]))
        self._plot(ds, exps, means, model)
        scope = {}
        obj = (ds.manifest.get("objective") or {}).get("name")
        if obj:
            scope["objective"] = obj
        imager = ds.manifest.get("imager") or {}
        if imager.get("pixel_format"):
            scope["pixel_format"] = imager["pixel_format"]
        entry = CalEntry(key="imager.response",
                         value=model,
                         scope=scope,
                         extra={"linearize": False})
        ok = model["rms"] < 0.08 and 0.5 < model["gamma"] < 1.6
        return Result(self.name,
                      values={
                          "gamma": model["gamma"],
                          "black_level": model["black_level"],
                          "rms": model["rms"],
                          "linear_max": model["linear_max"],
                      },
                      ok=ok,
                      message="γ=%0.3f, black=%0.3f, rms=%0.4f" %
                      (model["gamma"], model["black_level"], model["rms"]),
                      stats=model,
                      entries=[entry])

    def _plot(self, ds, exps, means, model):
        try:
            import matplotlib.pyplot as plt
            t = np.asarray(exps, dtype=float)
            pred = model["a"] * np.power(np.maximum(t, 1e-9),
                                         model["gamma"]) + model["black_level"]
            fig, ax = plt.subplots()
            ax.plot(exps, means, "o", label="measured")
            ax.plot(exps, pred, label="fit")
            ax.set_xlabel("Exposure")
            ax.set_ylabel("Mean (0-1)")
            ax.set_title("γ=%0.3f" % model["gamma"])
            ax.legend()
            fig.savefig(os.path.join(ds.path, "linearity.png"))
            plt.close(fig)
        except Exception:
            pass
