"""
XY backlash from a fiducial, compensation disabled

Run the Fiducial check first: backlash is a few um, so centroid noise from a
low contrast or out of focus dot dominates the result
"""

import os

import numpy as np

from uscope.cal.analysis.centroid import find_centroid_fn
from uscope.cal.calibrations.fiducial import fiducial_preflight
from uscope.cal.analysis.fitting import distance, invert_linear, linear_fit
from uscope.cal.base import Calibration, Check, Quality, Result, ScopeKind
from uscope.cal.store import CalEntry


class BacklashXYCal(Calibration):
    name = "backlash_xy"
    title = "XY backlash"
    description = "Measure XY backlash using a fiducial with compensation off."
    scope_kind = ScopeKind.UNIT
    requires = ("scale", )
    soft_requires = ("fiducial", )
    needs_fiducial = True

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        return 90 if quality == Quality.QUICK else 400

    def preflight(self, ctx):
        obj = ctx.objective_config()
        return [
            Check("Objective selected", bool(obj),
                  obj.get("name") if obj else "Select an objective"),
            fiducial_preflight(ctx),
        ]

    def collect(self, ctx, ds, quality=Quality.FULL):
        eps = ctx.motion_epsilon()
        if quality == Quality.QUICK:
            n_moves, repeat = 6, 3
            step = {a: max(eps.get(a, 0.00125) * 2, 0.002) for a in "xy"}
        else:
            n_moves, repeat = 15, 8
            step = {a: max(eps.get(a, 0.00125), 0.00125) for a in "xy"}
        obj = ctx.objective_config()
        ds.manifest["params"] = {
            "n_moves": n_moves,
            "n_repeat": repeat,
            "step_size": step,
        }
        ds.manifest["objective"] = obj
        ds.save_manifest()
        origin = ctx.save_origin()
        ctx.backlash_disable()
        try:
            for axis in "xy":
                max_back = n_moves * step[axis]
                for movei in range(n_moves):
                    this_move = step[axis] * movei
                    for repeati in range(repeat):
                        ctx.check_running()
                        ctx.move_absolute(
                            {axis: origin[axis] + max_back})
                        ctx.move_absolute({axis: origin[axis]})
                        ctx.move_relative({axis: +this_move})
                        if quality != Quality.QUICK:
                            ctx.sleep(0.2)
                        basename = "%s_%03u_r%02u.tif" % (axis, movei, repeati)
                        ctx.image_captured().save(ds.capture_path(basename))
                        ds.manifest["captures"].append({
                            "fn": "images/%s" % basename,
                            "axis": axis,
                            "move": this_move,
                            "stepi": movei,
                            "repeat": repeati,
                        })
                    ctx.progress((movei + (0 if axis == "x" else n_moves)) /
                                 (2 * n_moves),
                                 "%s %u/%u" % (axis, movei + 1, n_moves))
        finally:
            ctx.backlash_enable()
        ds.save_manifest()

    def analyze(self, ds, log=None):
        log = log or (lambda s: None)
        obj = ds.manifest.get("objective") or {}
        um_per_pix = obj.get("um_per_pixel") or 1.0
        params = ds.manifest.get("params") or {}
        step = params.get("step_size") or {}
        pj = {}
        entries = []
        for axis in "xy":
            recs = ds.captures(axis=axis)
            if not recs:
                return Result(self.name,
                              ok=False,
                              message="No captures for axis %s" % axis)
            # Group by stepi
            by_step = {}
            for r in recs:
                by_step.setdefault(r.get("stepi", 0), []).append(r)
            xs, ys, keys = [], [], []
            for stepi in sorted(by_step):
                cxs, cys = [], []
                for r in by_step[stepi]:
                    try:
                        x, y = find_centroid_fn(ds.capture_abspath(r))
                    except ValueError:
                        continue
                    cxs.append(x * um_per_pix)
                    cys.append(-y * um_per_pix)
                if not cxs:
                    continue
                xs.append(float(np.median(cxs)))
                ys.append(float(np.median(cys)))
                keys.append(1000.0 * float(step.get(axis, 0)) * stepi)
            if len(xs) < 4:
                return Result(self.name,
                              ok=False,
                              message="Not enough points on %s" % axis)
            xs = [x - min(xs) for x in xs]
            ys = [y - min(ys) for y in ys]
            origin = (max(xs), max(ys))
            ds_um = [distance((x, y), origin) for x, y in zip(xs, ys)]
            reg_d = ds_um[len(ds_um) // 3:]
            reg_x = keys[len(keys) // 3:]
            m, b = linear_fit(reg_x, reg_d)
            backlash = invert_linear(m, b, 0.0)
            pj[axis] = {
                "backlash_um": backlash,
                "slope": m,
                "intercept": b,
            }
            log("%s backlash: %0.3f um" % (axis, backlash))
            entries.append(
                CalEntry(key="motion.backlash",
                         value=backlash / 1000.0,
                         scope={"axis": axis},
                         unit="mm",
                         extra={"compensation": -1}))
            self._plot(ds, axis, keys, ds_um, reg_x, m, b, backlash)

        ok = all(0 <= pj[a]["backlash_um"] < 200 for a in "xy")
        values = {
            "x_um": pj["x"]["backlash_um"],
            "y_um": pj["y"]["backlash_um"],
        }
        return Result(self.name,
                      values=values,
                      ok=ok,
                      message="X %0.2f um, Y %0.2f um" %
                      (values["x_um"], values["y_um"]),
                      stats=pj,
                      entries=entries)

    def _plot(self, ds, axis, keys, ds_um, reg_x, m, b, backlash):
        try:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots()
            ax.plot(keys, ds_um, label="measured")
            ax.plot(reg_x, [m * x + b for x in reg_x], label="fit")
            ax.set_title("%s backlash %0.2f um" % (axis, backlash))
            ax.set_xlabel("Commanded (um)")
            ax.set_ylabel("Observed (um)")
            ax.legend()
            fig.savefig(os.path.join(ds.path, "backlash_%s.png" % axis))
            plt.close(fig)
        except Exception:
            pass
