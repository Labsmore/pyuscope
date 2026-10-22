"""Calibration ABC, results, preflight checks."""

import os


class ScopeKind:
    UNIT = "unit"
    OBJECTIVE = "objective"
    OBJECTIVE_IMAGER = "objective_imager"


class Quality:
    QUICK = "quick"
    FULL = "full"


class Check:
    def __init__(self, name, ok, hint="", blocking=True):
        self.name = name
        self.ok = bool(ok)
        self.hint = hint
        self.blocking = blocking

    def __repr__(self):
        return "Check(%s, ok=%s)" % (self.name, self.ok)


class Result:
    def __init__(self,
                 name,
                 values=None,
                 ok=True,
                 message="",
                 plots=None,
                 stats=None,
                 entries=None,
                 extra=None):
        self.name = name
        self.values = values or {}
        self.ok = bool(ok)
        self.message = message
        self.plots = plots or []  # list of image paths or matplotlib figs
        self.stats = stats or {}
        self.entries = entries or []  # CalEntry list ready to apply
        self.extra = extra or {}

    def summary_lines(self):
        lines = ["%s: %s" % (self.name, "PASS" if self.ok else "FAIL")]
        if self.message:
            lines.append("  " + self.message)
        for k, v in self.values.items():
            if isinstance(v, float):
                lines.append("  %s: %0.6g" % (k, v))
            else:
                lines.append("  %s: %s" % (k, v))
        return lines


class Calibration:
    name = "unnamed"
    title = "Unnamed"
    description = ""
    scope_kind = ScopeKind.UNIT
    requires = ()
    soft_requires = ()
    needs_z = False
    needs_fiducial = False
    needs_dark = False
    needs_flat = False

    def preflight(self, ctx):
        return []

    def estimate_seconds(self, ctx, quality=Quality.FULL):
        return 60.0

    def collect(self, ctx, ds, quality=Quality.FULL):
        raise NotImplementedError

    def analyze(self, ds, log=None):
        raise NotImplementedError

    def apply(self, result, store, source=None):
        applied = []
        for entry in result.entries:
            if source and not entry.source:
                entry.source = source
            store.set(entry)
            applied.append(entry)
        return applied

    def default_scope(self, ctx):
        scope = {}
        if self.scope_kind in (ScopeKind.OBJECTIVE, ScopeKind.OBJECTIVE_IMAGER):
            obj = ctx.objective_config() if ctx else None
            if obj:
                scope["objective"] = obj.get("name")
        if self.scope_kind == ScopeKind.OBJECTIVE_IMAGER and ctx:
            fmt = ctx.capture_format()
            if fmt:
                scope["pixel_format"] = fmt
            wh = ctx.imager_wh()
            if wh:
                scope["wh"] = list(wh)
        return scope


def session_root(microscope):
    root = os.path.join(microscope.bc.get_data_dir(), "cal",
                        microscope.model_serial_string())
    os.makedirs(root, exist_ok=True)
    return root
