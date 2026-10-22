"""Motion settling time from centroid / focus after a typical move."""

import os
import time

import numpy as np

from uscope.cal.analysis.centroid import find_centroid
from uscope.cal.analysis.focus import focus_score, settle_time
from uscope.cal.base import Calibration, Check, Quality, Result, ScopeKind
from uscope.cal.store import CalEntry


class SettleCal(Calibration):
    name = "settle"
    title = "Motion settle"
    description = "Time after a move until the image stops changing."
    scope_kind = ScopeKind.OBJECTIVE

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        return 40 if quality == Quality.QUICK else 90

    def preflight(self, ctx):
        obj = ctx.objective_config()
        return [
            Check("Objective selected", bool(obj),
                  obj.get("name") if obj else "Select an objective"),
        ]

    def collect(self, ctx, ds, quality=Quality.FULL):
        obj = ctx.objective_config()
        repeats = 2 if quality == Quality.QUICK else 4
        window = 1.0 if quality == Quality.QUICK else 2.0
        axes = "xy"
        if ctx.has_z():
            axes += "z"
        pitch = {
            "x": obj.get("x_view", 1.0) * 0.7,
            "y": obj.get("y_view", 1.0) * 0.7,
            "z": 0.01,
        }
        ds.manifest["params"] = {
            "repeats": repeats,
            "window_s": window,
            "pitch": pitch,
            "axes": axes,
        }
        ds.manifest["objective"] = obj
        ds.save_manifest()
        origin = ctx.save_origin()
        total = len(axes) * repeats
        n = 0
        for axis in axes:
            for rep in range(repeats):
                ctx.check_running()
                ctx.move_absolute({axis: origin[axis]})
                ctx.sleep(0.3)
                ctx.move_relative({axis: pitch[axis]})
                t0 = time.time()
                i = 0
                while time.time() - t0 < window:
                    cap = ctx.image_captured(wait_imaging_ok=False)
                    dt = time.time() - t0
                    basename = "%s_r%02u_%03u.tif" % (axis, rep, i)
                    cap.save(ds.capture_path(basename))
                    ds.manifest["captures"].append({
                        "fn": "images/%s" % basename,
                        "axis": axis,
                        "repeat": rep,
                        "t": dt,
                    })
                    i += 1
                n += 1
                ctx.progress(n / total, "%s repeat %u" % (axis, rep + 1))
                ctx.move_absolute({axis: origin[axis]})
        ds.save_manifest()

    def analyze(self, ds, log=None):
        log = log or (lambda s: None)
        recs = ds.captures()
        if not recs:
            return Result(self.name, ok=False, message="No captures")
        from PIL import Image
        by = {}
        for r in recs:
            im = Image.open(ds.capture_abspath(r))
            try:
                if r["axis"] == "z":
                    val = focus_score(im)
                else:
                    x, y = find_centroid(im)
                    val = x if r["axis"] == "x" else y
            except Exception:
                im.close()
                continue
            im.close()
            by.setdefault((r["axis"], r["repeat"]),
                          {"t": [], "v": []})["t"].append(r["t"])
            by[(r["axis"], r["repeat"])]["v"].append(val)

        per_axis = {}
        for (axis, _rep), d in by.items():
            if len(d["t"]) < 3:
                continue
            if axis == "z":
                span = max(np.ptp(d["v"]), 1e-6)
                tol = 0.02 * span
            else:
                tol = 0.5  # pixels
            t_s, ok = settle_time(d["t"], d["v"], tol)
            per_axis.setdefault(axis, []).append(t_s)

        values = {}
        entries = []
        obj = (ds.manifest.get("objective") or {}).get("name")
        scope = {"objective": obj} if obj else {}
        worst = 0.0
        for axis, ts in per_axis.items():
            t = float(np.max(ts))
            values["%s_s" % axis] = t
            worst = max(worst, t)
            log("%s settle %0.3f s" % (axis, t))
        if worst:
            entries.append(
                CalEntry(key="kinematics.tsettle_motion",
                         value=worst,
                         scope=scope,
                         unit="s"))
        self._plot(ds, by)
        ok = worst < 5.0 and bool(per_axis)
        return Result(self.name,
                      values=values,
                      ok=ok,
                      message="settle %0.3f s (worst axis)" % worst,
                      stats=per_axis,
                      entries=entries)

    def _plot(self, ds, by):
        try:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots()
            for (axis, rep), d in by.items():
                v = np.asarray(d["v"], dtype=float)
                v = v - v[-1]
                ax.plot(d["t"], v, label="%s r%u" % (axis, rep))
            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Offset from final")
            ax.legend()
            fig.savefig(os.path.join(ds.path, "settle.png"))
            plt.close(fig)
        except Exception:
            pass
