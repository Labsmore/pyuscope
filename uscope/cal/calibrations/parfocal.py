"""Z (and optional XY) offset of each objective relative to a reference."""

from uscope.cal.analysis.centroid import find_centroid
from uscope.cal.base import Calibration, Check, Quality, Result, ScopeKind
from uscope.cal.store import CalEntry


class ParfocalCal(Calibration):
    name = "parfocal"
    title = "Parfocality"
    description = "Z offset of each objective versus a reference. Auto-Z apply is off by default."
    scope_kind = ScopeKind.OBJECTIVE
    needs_z = True

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        n = 1
        try:
            n = max(1, len(ctx.objectives() or {}))
        except Exception:
            pass
        return 20 * n

    def preflight(self, ctx):
        return [
            Check("Has Z", ctx.has_z(), "Microscope needs a Z axis"),
            Check("Objectives", True,
                  "You will be prompted to swap objectives",
                  blocking=False),
        ]

    def collect(self, ctx, ds, quality=Quality.FULL):
        objs = ctx.objectives() or {}
        names = list(objs.keys()) if isinstance(objs, dict) else [
            o.get("name") for o in objs
        ]
        current = (ctx.objective_config() or {}).get("name")
        ref = current or (names[0] if names else None)
        ds.manifest["params"] = {
            "reference": ref,
            "objectives": names,
        }
        ds.save_manifest()
        if not names:
            return
        for i, name in enumerate(names):
            ctx.check_running()
            if name != current:
                ans = ctx.prompt(
                    "Swap objective",
                    "Install '%s' and select it in the GUI, then continue." %
                    name,
                    buttons=("Continue", "Skip remaining"))
                if ans != "Continue":
                    break
            ctx.autofocus()
            pos = ctx.pos()
            centroid = None
            try:
                centroid = list(find_centroid(ctx.image()))
            except Exception:
                pass
            cap = ctx.image_captured()
            basename = "obj_%02u.tif" % i
            cap.save(ds.capture_path(basename))
            ds.manifest["captures"].append({
                "fn": "images/%s" % basename,
                "objective": name,
                "pos": pos,
                "centroid": centroid,
            })
            ctx.progress((i + 1) / len(names), name)
            current = name
        ds.save_manifest()

    def analyze(self, ds, log=None):
        log = log or (lambda s: None)
        recs = ds.captures()
        if not recs:
            return Result(self.name, ok=False, message="No captures")
        ref_name = (ds.manifest.get("params") or {}).get("reference")
        ref = None
        for r in recs:
            if r.get("objective") == ref_name:
                ref = r
                break
        if ref is None:
            ref = recs[0]
            ref_name = ref.get("objective")
        z_ref = ref["pos"]["z"]
        values = {}
        entries = []
        for r in recs:
            name = r.get("objective")
            z = r["pos"]["z"]
            dz = z - z_ref
            values[name] = dz
            log("%s Δz=%0.4f mm" % (name, dz))
            # enabled defaults False: auto Z on objective change is opt-in
            entries.append(
                CalEntry(key="optics.z_offset",
                         value=float(dz),
                         scope={"objective": name},
                         unit="mm",
                         enabled=False,
                         extra={"reference_objective": ref_name}))
        return Result(self.name,
                      values=values,
                      ok=True,
                      message="reference %s" % ref_name,
                      entries=entries)
