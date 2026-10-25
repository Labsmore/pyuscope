"""
Catalog of calibration parameters for front ends

Describes each known calibration store key: what it means, how to edit it,
which scopes (axis, objective) it is normally set per and how to resolve its
nominal (microscope.j5) and effective (what the system uses now) values

CalRow is one (key, scope) the user can look at / edit. Rows are listed for
every known parameter even if not calibrated so nominal values are visible
"""

import copy
import json

from uscope.cal.store import CalEntry, SCOPE_FIELDS, _scope_tuple
from uscope.util import utcnow


class CalParam:
    key = None
    group = "Other"
    title = None
    unit = None
    help = ""
    # One row per axis / objective. None => one unscoped row
    scope_field = None
    # Value can't be edited by hand (file backed / measured models)
    editable = True
    needs_restart = False
    # (extra key, label, [(value, label), ...]) stored in CalEntry.extra
    extras = ()

    def __init__(self, key=None):
        if key is not None:
            self.key = key
        if self.title is None:
            self.title = self.key

    def scopes(self, microscope):
        if self.scope_field == "axis":
            return [{"axis": axis} for axis in sorted(microscope.usc.motion.axes())]
        if self.scope_field == "objective":
            return [{"objective": name}
                    for name in microscope.objective_state.names()]
        return [{}]

    def nominal(self, microscope, scope):
        """microscope.j5 / built in value. None => no correction"""
        return None

    def nominal_extra(self, microscope, scope, name):
        return None

    def effective(self, microscope, scope):
        """Value the system currently uses for scope"""
        entry = microscope.cal_store.get(self.key, scope)
        if entry is not None:
            return entry.value
        return self.nominal(microscope, scope)

    def format(self, value):
        if value is None:
            return ""
        if isinstance(value, float):
            return "%0.6g" % value
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True)
        return str(value)

    def format_unit(self, value):
        s = self.format(value)
        if value is not None and s and self.unit:
            s += " " + self.unit
        return s


class FloatParam(CalParam):
    minimum = None
    maximum = None

    def parse(self, text):
        val = float(text)
        if self.minimum is not None and val < self.minimum:
            raise ValueError("%s must be >= %s" % (self.title, self.minimum))
        if self.maximum is not None and val > self.maximum:
            raise ValueError("%s must be <= %s" % (self.title, self.maximum))
        return val


class FieldsParam(CalParam):
    """
    A few named numbers
    fields: [(name, label, type)]
    """
    fields = ()

    def get_fields(self, value):
        """value => {name: number}"""
        value = value or {}
        return {name: value.get(name) for name, _label, _type in self.fields}

    def set_fields(self, value, fields):
        """Return value with fields replaced (other keys preserved)"""
        ret = copy.deepcopy(value) if isinstance(value, dict) else {}
        for name, _label, type_ in self.fields:
            ret[name] = type_(fields[name])
        return ret

    def format(self, value):
        if value is None:
            return ""
        fields = self.get_fields(value)
        parts = []
        for name, label, _type in self.fields:
            v = fields.get(name)
            parts.append("%s %s" % (label, "?" if v is None else
                                    ("%0.4g" % v if isinstance(v, float) else v)))
        return ", ".join(parts)


class InfoParam(CalParam):
    """Measured model / file backed data: view and enable / disable only"""
    editable = False

    def scopes(self, microscope):
        return []


"""
Optics
"""


class UmPerPixelParam(FloatParam):
    key = "optics.um_per_pixel_raw_1x"
    group = "Optics"
    title = "Scale at 1x (raw pixel)"
    unit = "um/pix"
    help = ("Size of one raw (unscaled, uncropped) sensor pixel referenced to "
            "a 1x objective. Each objective's um/pixel and field of view are "
            "derived from this, the objective magnification and the global "
            "magnification scalar. Measured by Imager scale.")
    minimum = 1e-9

    def nominal(self, microscope, scope):
        try:
            return microscope.usc.optics.um_per_pixel_raw_1x_nominal()
        except Exception:
            return None

    def effective(self, microscope, scope):
        return microscope.usc.optics.um_per_pixel_raw_1x()


class ZOffsetParam(FloatParam):
    key = "optics.z_offset"
    group = "Optics"
    title = "Parfocal Z offset"
    unit = "mm"
    scope_field = "objective"
    help = ("Z offset of this objective versus the reference objective. When "
            "enabled, Z moves by the difference whenever the objective is "
            "changed. Measured by Parfocality.")

    def nominal(self, microscope, scope):
        return 0.0

    def effective(self, microscope, scope):
        entry = microscope.cal_store.get(self.key, scope)
        if entry is None:
            return None
        return entry.value

    def format(self, value):
        if value is None:
            return "off"
        return super().format(value)


"""
Motion
"""


class BacklashParam(FloatParam):
    key = "motion.backlash"
    group = "Motion"
    title = "Backlash"
    unit = "mm"
    scope_field = "axis"
    help = ("Lost motion when an axis reverses direction. Compensation picks "
            "the direction moves finish in. Measured by XY backlash / Z "
            "backlash.")
    minimum = 0.0
    extras = (("compensation", "Compensation", [
        (-1, "-1: approach from +"),
        (0, "0: off"),
        (1, "+1: approach from -"),
    ]), )

    def nominal(self, microscope, scope):
        return microscope.usc.motion.backlash(nominal=True).get(scope["axis"])

    def nominal_extra(self, microscope, scope, name):
        if name == "compensation":
            return microscope.usc.motion.backlash_compensation(
                nominal=True).get(scope["axis"])
        return None

    def effective(self, microscope, scope):
        return microscope.usc.motion.backlash().get(scope["axis"])


class SettleParam(FloatParam):
    key = "kinematics.tsettle_motion"
    group = "Motion"
    title = "Settle time after move"
    unit = "s"
    scope_field = "objective"
    help = ("Wait after a move before capturing. Nominal value is scaled "
            "from the objective NA. Measured by Motion settle.")
    minimum = 0.0

    def nominal(self, microscope, scope):
        return microscope.objective_state.tsettle_motion_nominal(
            scope["objective"])

    def effective(self, microscope, scope):
        config = microscope.objective_state.full_config().get(
            scope["objective"])
        if config is None:
            return None
        return config.get("tsettle_motion")


"""
Imager
"""


class CropParam(FieldsParam):
    key = "imager.crop_pixels"
    group = "Imager"
    title = "Crop (usable sensor area)"
    unit = "pix"
    help = ("Pixels removed from each edge of the raw image (ex: dark corners "
            "outside the image circle). Takes effect after restarting. "
            "Measured by Usable sensor area.")
    needs_restart = True
    fields = (("top", "T", int), ("bottom", "B", int), ("left", "L", int),
              ("right", "R", int))

    def nominal(self, microscope, scope):
        return microscope.usc.imager.crop_tblr(nominal=True) or {
            "top": 0,
            "bottom": 0,
            "left": 0,
            "right": 0
        }

    def set_fields(self, value, fields):
        for name, _label, _type in self.fields:
            if int(fields[name]) < 0:
                raise ValueError("Crop can't be negative")
        return super().set_fields(value, fields)


class WhiteBalanceParam(FieldsParam):
    key = "imager.white_balance"
    group = "Imager"
    title = "Software white balance"
    help = ("Per channel gains applied to snapshots and scan tiles. Intended "
            "for cameras without RGB gain control. Measured by White "
            "balance.")
    fields = (("r", "R", float), ("g", "G", float), ("b", "B", float))

    def get_fields(self, value):
        gains = (value or {}).get("gains") or [None, None, None]
        return dict(zip(("r", "g", "b"), gains))

    def set_fields(self, value, fields):
        ret = copy.deepcopy(value) if isinstance(value, dict) else {}
        gains = [float(fields[k]) for k in ("r", "g", "b")]
        if min(gains) <= 0:
            raise ValueError("Gains must be > 0")
        ret["gains"] = gains
        return ret

    def nominal(self, microscope, scope):
        return {"gains": [1.0, 1.0, 1.0]}

    def effective(self, microscope, scope):
        if not microscope.usc.imager.software_white_balance():
            return None
        return super().effective(microscope, scope)

    def format(self, value):
        if value is None:
            return "off (microscope config)"
        return super().format(value)


class SoftwareGainParam(FloatParam):
    key = "imager.software_gain"
    group = "Imager"
    title = "Software gain"
    help = ("Scalar gain applied to snapshots and scan tiles along with white "
            "balance. 1.0 => off.")
    minimum = 0.01
    maximum = 100.0

    def nominal(self, microscope, scope):
        return microscope.usc.imager.software_gain(nominal=True)

    def effective(self, microscope, scope):
        return microscope.usc.imager.software_gain()


class ResponseParam(InfoParam):
    key = "imager.response"
    group = "Imager"
    title = "Exposure response"
    help = "Fitted v = a t^gamma + b model. Measured by Exposure linearity."


class DarkParam(InfoParam):
    key = "imager.dark"
    group = "Imager"
    title = "Dark frame"
    help = "Dark level and hot pixel map. Measured by Dark frame."


class IlluminationParam(InfoParam):
    key = "imager.illumination"
    group = "Imager"
    title = "Flat field (illumination)"
    help = "Per pixel gain map. Measured by Flat field."


PARAMS = [
    UmPerPixelParam(),
    ZOffsetParam(),
    BacklashParam(),
    SettleParam(),
    CropParam(),
    WhiteBalanceParam(),
    SoftwareGainParam(),
    ResponseParam(),
    DarkParam(),
    IlluminationParam(),
]
GROUPS = ("Optics", "Motion", "Imager", "Other")


def get_param(key):
    for param in PARAMS:
        if param.key == key:
            return param
    return InfoParam(key)


def scope_label(scope):
    if not scope:
        return "all"
    parts = []
    for k in SCOPE_FIELDS:
        v = scope.get(k)
        if v is None:
            continue
        if k in ("axis", "objective"):
            parts.append(str(v))
        elif k == "wh":
            parts.append("%sx%s" % tuple(v))
        else:
            parts.append("%s=%s" % (k, v))
    return ", ".join(parts)


class CalRow:
    """One (key, scope) as seen by the user"""
    def __init__(self, microscope, param, scope):
        self.microscope = microscope
        self.param = param
        self.scope = dict(scope or {})
        store = microscope.cal_store
        self.user_entry = store.find_exact(param.key, self.scope)
        # May be shadowed by user_entry
        self.factory_entry = store.find_exact(param.key,
                                              self.scope,
                                              include_factory=True,
                                              include_user=False)

    @property
    def key(self):
        return self.param.key

    @property
    def entry(self):
        """Entry stored at exactly this scope (user beats factory) or None"""
        return self.user_entry or self.factory_entry

    def status(self):
        entry = self.entry
        if entry is None:
            return "nominal"
        where = "user" if self.user_entry is not None else "factory"
        if not entry.enabled:
            ret = "disabled (%s)" % where
        else:
            ret = "calibrated (%s)" % where
        if self.orphaned():
            ret += ", objective not in lineup"
        return ret

    def orphaned(self):
        """Scoped to an objective that isn't currently configured"""
        name = self.scope.get("objective")
        if not name:
            return False
        try:
            return name not in self.microscope.objective_state.names()
        except Exception:
            return False

    def calibrated(self):
        return None if self.entry is None else self.entry.value

    def nominal(self):
        try:
            return self.param.nominal(self.microscope, self.scope)
        except Exception:
            return None

    def effective(self):
        try:
            return self.param.effective(self.microscope, self.scope)
        except Exception:
            return None

    def extra(self, name):
        """Effective extra value: entry, else nominal"""
        entry = self.entry
        if entry is not None and entry.enabled and entry.extra.get(
                name) is not None:
            return entry.extra[name]
        try:
            return self.param.nominal_extra(self.microscope, self.scope, name)
        except Exception:
            return None

    def scope_label(self):
        return scope_label(self.scope)


def rows(microscope):
    """
    All rows to show: known parameters at their usual scopes plus every
    stored entry (any scope, unknown keys)
    """
    store = microscope.cal_store
    ret = []
    seen = set()

    def add(param, scope):
        ident = (param.key, _scope_tuple(scope))
        if ident in seen:
            return
        seen.add(ident)
        ret.append(CalRow(microscope, param, scope))

    stored = store.entries(include_disabled=True) if store else []
    for param in PARAMS:
        try:
            scopes = param.scopes(microscope)
        except Exception:
            scopes = []
        for scope in scopes:
            add(param, scope)
        for entry in stored:
            if entry.key == param.key:
                add(param, entry.scope)
        # Show uncalibrated info params so the user knows they exist
        if not any(row.param is param for row in ret):
            add(param, {})
    for entry in stored:
        param = get_param(entry.key)
        if param in PARAMS:
            continue
        add(param, entry.scope)
    return ret


"""
Editing
"""


def set_value(store, row, value=None, extras=None, notes=None):
    """
    Save a hand edited value as a user entry at row's scope
    Starts from the existing user / factory entry so provenance is kept
    unless the value itself changes
    """
    entry = row.entry
    if entry is None:
        entry = CalEntry(key=row.key,
                         value=row.nominal() if value is None else value,
                         scope=row.scope,
                         unit=row.param.unit,
                         source="manual")
        changed = True
    else:
        entry = CalEntry.from_j(entry.to_j())
        changed = False
    if value is not None and value != entry.value:
        entry.value = value
        changed = True
    for k, v in (extras or {}).items():
        if entry.extra.get(k) != v:
            entry.extra[k] = v
            changed = True
    if notes is not None:
        entry.notes = notes
    if changed:
        # Measurement statistics no longer describe the value
        entry.source = "manual"
        entry.std = None
        entry.n = None
        entry.measured_at = utcnow().isoformat()
        entry.enabled = True
    if row.param.needs_restart:
        entry.needs_restart = True
    store.set(entry)
    return entry


def set_enabled(store, row, enabled):
    return store.set_enabled(row.key, row.scope, enabled)


def remove_user(store, row):
    """Drop the user entry: falls back to factory, then nominal"""
    return store.delete(row.key, row.scope)
