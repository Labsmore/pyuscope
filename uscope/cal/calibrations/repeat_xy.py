"""XY return-to-origin repeatability."""

import os
import random

import numpy as np

from uscope.cal.analysis.centroid import find_centroid, find_centroid_fn
from uscope.cal.analysis.fitting import distance
from uscope.cal.base import Calibration, Check, Quality, Result, ScopeKind


class RepeatXYCal(Calibration):
    name = "repeat_xy"
    title = "XY repeatability"
    description = "How tightly the stage returns to a fiducial after random moves."
    scope_kind = ScopeKind.UNIT
    requires = ("scale", )
    needs_fiducial = True

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        return 60 if quality == Quality.QUICK else 400

    def preflight(self, ctx):
        checks = []
        obj = ctx.objective_config()
        checks.append(
            Check("Objective selected", bool(obj),
                  obj.get("name") if obj else "Select an objective"))
        try:
            x, y = find_centroid(ctx.image())
            checks.append(
                Check("Fiducial visible", True,
                      "Centroid at %0.1f, %0.1f" % (x, y)))
        except Exception as e:
            checks.append(Check("Fiducial visible", False, str(e)))
        return checks

    def collect(self, ctx, ds, quality=Quality.FULL):
        n = 10 if quality == Quality.QUICK else 100
        backlash = 0.1
        max_rand = 1.0
        obj = ctx.objective_config()
        ds.manifest["params"] = {
            "n_moves": n,
            "backlash": backlash,
            "max_random_offset": max_rand,
        }
        ds.manifest["objective"] = obj
        ds.save_manifest()
        origin = ctx.save_origin()
        for axis in "xy":
            for movei in range(n):
                ctx.check_running()
                this_move = backlash + max_rand * random.uniform(0.0, 1.0)
                ctx.move_relative({axis: +this_move})
                ctx.move_absolute({axis: origin[axis]})
                if quality != Quality.QUICK:
                    ctx.sleep(0.2)
                basename = "%s_%03u.tif" % (axis, movei)
                ctx.image_captured().save(ds.capture_path(basename))
                ds.manifest["captures"].append({
                    "fn": "images/%s" % basename,
                    "axis": axis,
                    "move": this_move,
                    "stepi": movei,
                })
            ctx.progress(0.5 if axis == "x" else 1.0, "%s done" % axis)
        ds.save_manifest()

    def analyze(self, ds, log=None):
        log = log or (lambda s: None)
        obj = ds.manifest.get("objective") or {}
        um_per_pix = obj.get("um_per_pixel") or 1.0
        pj = {}
        for axis in "xy":
            recs = ds.captures(axis=axis)
            xs, ys = [], []
            for r in recs:
                try:
                    x, y = find_centroid_fn(ds.capture_abspath(r))
                except ValueError:
                    continue
                xs.append(x * um_per_pix)
                ys.append(y * um_per_pix)
            if len(xs) < 3:
                return Result(self.name,
                              ok=False,
                              message="Not enough points on %s" % axis)
            ox, oy = float(np.mean(xs)), float(np.mean(ys))
            dxs = [x - ox for x in xs]
            dys = [y - oy for y in ys]
            ds_um = [distance((ox, oy), (x, y)) for x, y in zip(xs, ys)]
            pj[axis] = {
                "dxs": {
                    "median": float(np.median(dxs)),
                    "std": float(np.std(dxs)),
                    "max": float(np.max(np.abs(dxs))),
                },
                "dys": {
                    "median": float(np.median(dys)),
                    "std": float(np.std(dys)),
                    "max": float(np.max(np.abs(dys))),
                },
                "ds": {
                    "median": float(np.median(ds_um)),
                    "std": float(np.std(ds_um)),
                    "max": float(np.max(ds_um)),
                    "values": ds_um,
                },
            }
            log("%s XY std %0.3f um max %0.3f um" %
                (axis, pj[axis]["ds"]["std"], pj[axis]["ds"]["max"]))
            self._plot(ds, axis, ds_um)

        values = {
            "x_std_um": pj["x"]["ds"]["std"],
            "y_std_um": pj["y"]["ds"]["std"],
            "x_max_um": pj["x"]["ds"]["max"],
            "y_max_um": pj["y"]["ds"]["max"],
        }
        ok = values["x_std_um"] < 20 and values["y_std_um"] < 20
        return Result(self.name,
                      values=values,
                      ok=ok,
                      message="X σ=%0.2f um, Y σ=%0.2f um" %
                      (values["x_std_um"], values["y_std_um"]),
                      stats=pj,
                      entries=[])

    def _plot(self, ds, axis, values):
        try:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots()
            ax.plot(values)
            ax.set_ylabel("Offset (um)")
            ax.set_xlabel("Iteration")
            ax.set_title("Repeatability %s" % axis)
            fig.savefig(os.path.join(ds.path, "repeat_%s.png" % axis))
            plt.close(fig)
        except Exception:
            pass
