"""
Fiducial check: verify the bright dot target before the fiducial based
calibrations (imager scale, XY backlash, XY repeatability)

Optionally sets exposure for the dot and autofocuses on it. Nothing is
applied to the calibration store
"""

import os

import cv2
import numpy as np

from uscope.cal.analysis import fiducial as fid
from uscope.cal.base import CalOption, Calibration, Check, Quality, Result, ScopeKind

TARGET_HINT = ("White dot on a black background, about 1/6 to 1/3 of the "
               "image width, centered in view")


def fiducial_preflight(ctx):
    """
    Preflight check for calibrations that track the fiducial
    Without a live image (GUI preflight) recommend running the Fiducial check
    """
    if not ctx.can_image():
        return Check(
            "Fiducial", False,
            "Not checked here. Run Fiducial check first: it verifies "
            "contrast, centering and focus and can set exposure / focus",
            blocking=False)
    try:
        metrics = fid.analyze_image(ctx.image())
    except Exception as e:
        return Check("Fiducial", False, str(e))
    if metrics["ok"]:
        return Check(
            "Fiducial", True, "dot %0.0f, background %0.0f, centroid "
            "%0.1f, %0.1f" % (metrics["dot_level"], metrics["bg_level"],
                              *metrics["centroid"]))
    fatal = [i for i in metrics["issues"] if i.fatal]
    return Check("Fiducial", False,
                 "%s. Run Fiducial check for details" % fatal[0].text)


def _clamp(v, rng):
    if rng is None:
        return max(1, v)
    return max(rng[0], min(rng[1], v))


class FiducialCal(Calibration):
    name = "fiducial"
    title = "Fiducial check"
    description = ("Set up and verify the dot target used by imager scale and "
                   "XY backlash / repeatability: contrast, size, centering, "
                   "focus.")
    scope_kind = ScopeKind.UNIT
    needs_fiducial = True
    evaluate_only = True
    options = (
        CalOption(
            "auto_exposure", "Exposure", True,
            "Set exposure so the dot is (just) saturated and the background "
            "stays dark. Turns off the camera's auto exposure, which targets "
            "average brightness and would blow out the dot"),
        CalOption("autofocus", "Focus", True,
                  "Autofocus on the dot before checking (needs Z)"),
    )

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        return 10 if quality == Quality.QUICK else 30

    def preflight(self, ctx):
        checks = [Check("Target", True, TARGET_HINT, blocking=False)]
        obj = ctx.objective_config()
        checks.append(
            Check("Objective selected", bool(obj),
                  obj.get("name") if obj else
                  "Select an objective (needed by the dependent calibrations)",
                  blocking=False))
        if self.option(ctx, "autofocus") and not ctx.has_z():
            checks.append(
                Check("Z axis", False, "Autofocus selected but no Z axis",
                      blocking=False))
        if ctx.can_image():
            checks.append(fiducial_preflight(ctx))
        return checks

    def _settled_image(self, ctx):
        ctx.wait_imaging_ok()
        ctx.sleep(0.3)
        # The frame in flight may predate the last setting change
        ctx.image()
        return ctx.image()

    def auto_expose(self, ctx, max_iter=8):
        """
        Adjust exposure until the dot is saturated (or as bright as the
        background allows). Returns the final exposure or None
        """
        exp = ctx.exposure()
        if exp is None:
            if ctx.get_auto_exposure() is not None:
                ctx.log("Camera has no manual exposure: using its auto "
                        "exposure (the dot may be blown out)")
                ctx.set_auto_exposure(True)
                ctx.sleep(2.0)
            else:
                ctx.log("Camera has no exposure control: skipping")
            return None
        if ctx.get_auto_exposure():
            ctx.log("Turning off camera auto exposure")
            ctx.set_auto_exposure(False)
        rng = ctx.exposure_range()
        for i in range(max_iter):
            ctx.check_running()
            dot, bg, peak = fid.dot_levels(self._settled_image(ctx))
            ctx.log("  exposure %s: dot %s, background %0.0f" %
                    (exp, "-" if dot is None else "%0.0f" % dot, bg))
            if dot is None:
                factor = min(4.0, max(1.5, fid.DOT_TARGET / max(peak, 1.0)))
            elif dot >= fid.DOT_SATURATED:
                if bg <= fid.BG_MAX:
                    break
                factor = max(0.25, 0.7 * fid.BG_MAX / max(bg, 1.0))
            else:
                factor = min(4.0, max(0.25, fid.DOT_TARGET / max(dot, 1.0)))
                if bg > 0 and factor > 0.9 * fid.BG_MAX / bg:
                    # Background limited: as bright as it can go
                    factor = 0.9 * fid.BG_MAX / bg
                    if abs(factor - 1.0) < 0.05:
                        ctx.log("  background limits exposure: improve "
                                "target contrast")
                        break
            new = int(round(_clamp(exp * factor, rng)))
            if new == exp:
                ctx.log("  exposure limit reached")
                break
            ctx.set_exposure(new)
            exp = new
        else:
            ctx.log("  exposure did not converge")
        return exp

    def collect(self, ctx, ds, quality=Quality.FULL):
        opts = self.option_values(ctx)
        n = 3 if quality == Quality.QUICK else 10
        ds.manifest["params"] = {"n_images": n, "options": opts}
        ds.manifest["objective"] = ctx.objective_config()
        ds.manifest["exposure_start"] = ctx.exposure()
        ds.save_manifest()

        if opts["auto_exposure"]:
            ctx.progress(0.1, "Auto exposure")
            self.auto_expose(ctx)
        if opts["autofocus"]:
            if ctx.has_z():
                ctx.progress(0.4, "Autofocus")
                ctx.autofocus(block=True)
                # Keep the new focus when the session restores the origin
                ctx.origin = ctx.pos()
                if opts["auto_exposure"]:
                    # A focused dot is brighter
                    ctx.progress(0.6, "Auto exposure (refine)")
                    self.auto_expose(ctx, max_iter=4)
            else:
                ctx.log("No Z axis: skipping autofocus")

        ctx.wait_imaging_ok()
        for i in range(n):
            ctx.check_running()
            ctx.progress(0.7 + 0.3 * i / n, "Image %u/%u" % (i + 1, n))
            basename = "fiducial_%03u.tif" % i
            cap = ctx.image_captured()
            cap.save(ds.capture_path(basename))
            ds.manifest["captures"].append({
                "fn": "images/%s" % basename,
                "i": i
            })
            if i == 0:
                metrics = fid.analyze_image(cap.image)
                ctx.show_image(fid.annotate(cap.image, metrics),
                               title="Fiducial check")
        ds.manifest["exposure"] = ctx.exposure()
        ds.save_manifest()

    def analyze(self, ds, log=None):
        log = log or (lambda s: None)
        frames = []
        first = None
        for rec in ds.captures():
            im = cv2.imread(ds.capture_abspath(rec), cv2.IMREAD_GRAYSCALE)
            if im is None:
                continue
            m = fid.analyze_image(im)
            frames.append(m)
            if first is None:
                first = (im, m)
        if not frames:
            return Result(self.name, ok=False, message="No images")
        im0, m0 = first

        issues = {}
        for m in frames:
            for issue in m["issues"]:
                issues.setdefault(issue.name, issue)
        values = {
            "exposure": ds.manifest.get("exposure"),
        }
        for k in ("dot_level", "bg_level", "contrast", "diameter_frac",
                  "edge_width_px"):
            vals = [m[k] for m in frames if m.get(k) is not None]
            if vals:
                values[k] = float(np.median(vals))
        cents = np.array([m["centroid"] for m in frames if "centroid" in m])
        if len(cents) >= 2:
            jitter = float(np.max(np.std(cents, axis=0)))
            values["centroid_jitter_px"] = jitter
            if jitter > 0.5:
                issues.setdefault(
                    "jitter",
                    fid.Issue(
                        "jitter", False,
                        "Centroid moves %0.2f px between frames" % jitter,
                        "Check for vibration, flicker or a noisy image"))
        if "centroid" in m0:
            values["centroid_x"], values["centroid_y"] = m0["centroid"]

        ok = not any(i.fatal for i in issues.values())
        m0 = dict(m0, ok=ok)
        try:
            cv2.imwrite(os.path.join(ds.path, "fiducial.png"),
                        fid.annotate(im0, m0))
        except Exception:
            pass

        if "dot_level" in values:
            summary = ("dot %0.0f, background %0.0f (threshold %u), "
                       "contrast %0.2f, diameter %0.0f%% of width" %
                       (values["dot_level"], values["bg_level"],
                        fid.THRESHOLD, values["contrast"],
                        values["diameter_frac"] * 100))
        else:
            summary = "no dot found"
        log("Fiducial: " + summary)
        for issue in sorted(issues.values(), key=lambda i: not i.fatal):
            log("  " + issue.line())
        if ok:
            msg = "Ready: " + summary
            if issues:
                msg += " (%u warning(s))" % len(issues)
        else:
            fatal = [i for i in issues.values() if i.fatal]
            msg = "Not ready: %s. %s" % (fatal[0].text, fatal[0].hint)
        return Result(self.name,
                      values=values,
                      ok=ok,
                      message=msg,
                      extra={"issues": [i.line() for i in issues.values()]})
