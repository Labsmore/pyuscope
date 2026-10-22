"""Imager scale: um per pixel from a known stage move of a fiducial."""

import os

import numpy as np

from uscope.cal.analysis.centroid import find_centroid_fn
from uscope.cal.base import Calibration, Quality, Result, ScopeKind
from uscope.cal.store import CalEntry


def _mean_without_outliers(vals):
    vals = sorted(vals)
    return float(np.mean(vals[len(vals) // 4:3 * len(vals) // 4]))


class ScaleCal(Calibration):
    name = "scale"
    title = "Imager scale"
    description = "Measure um/pixel by moving a fiducial a known stage distance."
    scope_kind = ScopeKind.UNIT
    needs_fiducial = True

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        return 40 if quality == Quality.QUICK else 180

    def preflight(self, ctx):
        checks = []
        obj = ctx.objective_config()
        checks.append(
            type(self)._check_obj(obj))
        try:
            im = ctx.image()
            from uscope.cal.analysis.centroid import find_centroid
            x, y = find_centroid(im)
            checks.append(
                self._ok("Fiducial visible", True, "Centroid at %0.1f, %0.1f" %
                         (x, y)))
        except Exception as e:
            checks.append(
                self._ok("Fiducial visible", False, str(e)))
        return checks

    @staticmethod
    def _ok(name, ok, hint=""):
        from uscope.cal.base import Check
        return Check(name, ok, hint)

    @staticmethod
    def _check_obj(obj):
        from uscope.cal.base import Check
        if not obj:
            return Check("Objective selected", False, "Select an objective")
        return Check("Objective selected", True, obj.get("name", ""))

    def collect(self, ctx, ds, quality=Quality.FULL):
        obj = ctx.objective_config()
        moves = 10 if quality == Quality.QUICK else 50
        plus_minus = obj["x_view"] * 0.15
        ds.manifest["params"] = {
            "n_moves": moves,
            "plus_minus_mm": plus_minus,
            "delta_mm": plus_minus * 2,
        }
        ds.manifest["objective"] = obj
        ds.save_manifest()
        origin = ctx.save_origin()
        for axis in "xy":
            for movei in range(moves):
                ctx.check_running()
                ctx.progress((movei + (0 if axis == "x" else moves)) /
                             (2 * moves), "%s %u/%u" % (axis, movei + 1, moves))
                ctx.move_absolute({axis: origin[axis] + plus_minus})
                if quality != Quality.QUICK:
                    ctx.sleep(0.3)
                ctx.image_captured().save(
                    ds.capture_path("%s_%03u_plus.tif" % (axis, movei)))
                ds.manifest["captures"].append({
                    "fn": "images/%s_%03u_plus.tif" % (axis, movei),
                    "axis": axis,
                    "side": "plus",
                    "move": movei,
                })
                ctx.move_absolute({axis: origin[axis] - plus_minus})
                if quality != Quality.QUICK:
                    ctx.sleep(0.3)
                ctx.image_captured().save(
                    ds.capture_path("%s_%03u_minus.tif" % (axis, movei)))
                ds.manifest["captures"].append({
                    "fn": "images/%s_%03u_minus.tif" % (axis, movei),
                    "axis": axis,
                    "side": "minus",
                    "move": movei,
                })
        ds.save_manifest()

    def analyze(self, ds, log=None):
        log = log or (lambda s: None)
        j = ds.manifest
        obj = j.get("objective") or {}
        um_per_pix_old = obj.get("um_per_pixel")
        delta_um = float(j.get("params", {}).get("delta_mm") or
                         j.get("delta_mm") or 0) * 1000
        if not delta_um:
            return Result(self.name, ok=False, message="Missing delta_mm")

        axes = {}
        for axis in "xy":
            plus = {
                r["move"]: r
                for r in ds.captures(axis=axis, side="plus")
            }
            minus = {
                r["move"]: r
                for r in ds.captures(axis=axis, side="minus")
            }
            keys = sorted(set(plus) & set(minus))
            ums = []
            for k in keys:
                try:
                    xp, yp = find_centroid_fn(ds.capture_abspath(plus[k]))
                    xm, ym = find_centroid_fn(ds.capture_abspath(minus[k]))
                except ValueError:
                    continue
                delta_pix = ((xp - xm)**2 + (yp - ym)**2)**0.5
                if delta_pix <= 0:
                    continue
                ums.append(delta_um / delta_pix)
            if not ums:
                return Result(self.name,
                              ok=False,
                              message="No usable centroids on axis %s" % axis)
            um_new = _mean_without_outliers(ums)
            axes[axis] = {
                "um_per_pix": um_new,
                "n": len(ums),
                "std": float(np.std(ums)),
            }
            log("%s um/pix: %0.4f (n=%u)" % (axis, um_new, len(ums)))

        obj_um = (axes["x"]["um_per_pix"] + axes["y"]["um_per_pix"]) / 2
        mag = obj.get("magnification") or 1
        new_um_1x = obj_um * mag
        # raw = selected resolution (before software scalar)
        soft = 1.0
        try:
            from uscope.microscope import get_virtual_microscope
            from uscope.config import get_mconfig
            m = get_virtual_microscope(mconfig=get_mconfig(
                name=j.get("microscope", {}).get("model"),
                serial=j.get("microscope", {}).get("serial"),
            ))
            soft = m.usc.imager.scalar()
        except Exception:
            pass
        new_um_raw_1x = new_um_1x * soft

        values = {
            "um_per_pixel": obj_um,
            "um_per_pixel_raw_1x": new_um_raw_1x,
            "um_per_pixel_x": axes["x"]["um_per_pix"],
            "um_per_pixel_y": axes["y"]["um_per_pix"],
            "um_per_pixel_old": um_per_pix_old,
        }
        std = (axes["x"]["std"] + axes["y"]["std"]) / 2
        ok = True
        if um_per_pix_old and abs(obj_um - um_per_pix_old) / um_per_pix_old > 0.25:
            ok = False
        entry = CalEntry(
            key="optics.um_per_pixel_raw_1x",
            value=float(new_um_raw_1x),
            unit="um",
            std=float(std),
            n=int(axes["x"]["n"] + axes["y"]["n"]),
        )
        msg = "um/pixel %0.4f (raw 1x %0.4f)" % (obj_um, new_um_raw_1x)
        self._plot(ds, axes, um_per_pix_old)
        return Result(self.name,
                      values=values,
                      ok=ok,
                      message=msg,
                      stats=axes,
                      entries=[entry])

    def _plot(self, ds, axes, old):
        try:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots()
            ax.bar(["X", "Y"],
                   [axes["x"]["um_per_pix"], axes["y"]["um_per_pix"]])
            if old:
                ax.axhline(old, color="gray", linestyle="--", label="old")
                ax.legend()
            ax.set_ylabel("um / pixel")
            fn = os.path.join(ds.path, "scale.png")
            fig.savefig(fn)
            plt.close(fig)
        except Exception:
            pass
