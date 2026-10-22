"""Z backlash from focus-metric hysteresis."""

import os

import numpy as np

from uscope.cal.analysis.fitting import peak_quadratic
from uscope.cal.analysis.focus import focus_score
from uscope.cal.base import Calibration, Check, Quality, Result, ScopeKind
from uscope.cal.store import CalEntry


class BacklashZCal(Calibration):
    name = "backlash_z"
    title = "Z backlash"
    description = "Hysteresis between up and down focus peaks."
    scope_kind = ScopeKind.UNIT
    needs_z = True

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        return 50 if quality == Quality.QUICK else 140

    def preflight(self, ctx):
        checks = [
            Check("Has Z", ctx.has_z(), "Microscope needs a Z axis"),
        ]
        obj = ctx.objective_config()
        checks.append(
            Check("Objective selected", bool(obj),
                  obj.get("name") if obj else "Select an objective"))
        return checks

    def collect(self, ctx, ds, quality=Quality.FULL):
        obj = ctx.objective_config()
        repeats = 2 if quality == Quality.QUICK else 4
        # Span roughly a coarse autofocus window
        step = 0.002
        n_pm = 6 if quality == Quality.QUICK else 10
        ds.manifest["params"] = {
            "step": step,
            "n_pm": n_pm,
            "repeats": repeats,
        }
        ds.manifest["objective"] = obj
        ds.save_manifest()
        ctx.save_origin()
        ctx.autofocus()
        z0 = ctx.pos()["z"]
        ds.manifest["params"]["z0"] = z0
        ctx.backlash_disable()
        try:
            for rep in range(repeats):
                ctx.check_running()
                # Approach from below, sweep up
                z_lo = z0 - n_pm * step
                z_hi = z0 + n_pm * step
                ctx.move_absolute({"z": z_lo - 3 * step})
                for i, z in enumerate(np.arange(z_lo, z_hi + step / 2, step)):
                    ctx.move_absolute({"z": float(z)})
                    ctx.sleep(0.05)
                    im = ctx.image()
                    basename = "up_r%02u_%03u.tif" % (rep, i)
                    im.save(ds.capture_path(basename))
                    ds.manifest["captures"].append({
                        "fn": "images/%s" % basename,
                        "dir": "up",
                        "repeat": rep,
                        "z": float(z),
                    })
                # Approach from above, sweep down
                ctx.move_absolute({"z": z_hi + 3 * step})
                for i, z in enumerate(
                        np.arange(z_hi, z_lo - step / 2, -step)):
                    ctx.move_absolute({"z": float(z)})
                    ctx.sleep(0.05)
                    im = ctx.image()
                    basename = "dn_r%02u_%03u.tif" % (rep, i)
                    im.save(ds.capture_path(basename))
                    ds.manifest["captures"].append({
                        "fn": "images/%s" % basename,
                        "dir": "down",
                        "repeat": rep,
                        "z": float(z),
                    })
                ctx.progress((rep + 1) / repeats, "Repeat %u/%u" %
                             (rep + 1, repeats))
        finally:
            ctx.backlash_enable()
            ctx.move_absolute({"z": z0})
        ds.save_manifest()

    def analyze(self, ds, log=None):
        log = log or (lambda s: None)
        recs = ds.captures()
        if not recs:
            return Result(self.name, ok=False, message="No captures")
        from PIL import Image
        peaks = {"up": [], "down": []}
        series = {}
        for r in recs:
            im = Image.open(ds.capture_abspath(r))
            try:
                score = focus_score(im)
            finally:
                im.close()
            key = (r["dir"], r["repeat"])
            series.setdefault(key, {"z": [], "s": []})
            series[key]["z"].append(r["z"])
            series[key]["s"].append(score)
        for (direction, _rep), d in series.items():
            z_peak, _s = peak_quadratic(d["z"], d["s"])
            peaks[direction].append(z_peak)
        if not peaks["up"] or not peaks["down"]:
            return Result(self.name, ok=False, message="Missing up/down peaks")
        # Backlash = down_peak - up_peak when approaching from opposite sides
        deltas = []
        n = min(len(peaks["up"]), len(peaks["down"]))
        for i in range(n):
            deltas.append(peaks["down"][i] - peaks["up"][i])
        backlash = float(np.median(np.abs(deltas)))
        sign = float(np.median(deltas))
        compensation = -1 if sign >= 0 else +1
        log("Z backlash %0.4f mm (sign %0.4f)" % (backlash, sign))
        self._plot(ds, series, backlash)
        entry = CalEntry(key="motion.backlash",
                         value=backlash,
                         scope={"axis": "z"},
                         unit="mm",
                         std=float(np.std(np.abs(deltas))) if n > 1 else None,
                         n=n,
                         extra={"compensation": compensation})
        ok = 0 <= backlash < 0.2
        return Result(self.name,
                      values={
                          "backlash_mm": backlash,
                          "sign": sign,
                          "compensation": compensation,
                      },
                      ok=ok,
                      message="Z backlash %0.1f um" % (backlash * 1000),
                      entries=[entry])

    def _plot(self, ds, series, backlash):
        try:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots()
            for (direction, rep), d in series.items():
                ax.plot(d["z"], d["s"], label="%s r%u" % (direction, rep))
            ax.set_xlabel("Z (mm)")
            ax.set_ylabel("Focus score")
            ax.set_title("Z backlash %0.1f um" % (backlash * 1000))
            ax.legend()
            fig.savefig(os.path.join(ds.path, "backlash_z.png"))
            plt.close(fig)
        except Exception:
            pass
