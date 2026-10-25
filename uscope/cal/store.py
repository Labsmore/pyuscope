"""
Per-microscope calibration store.

Precedence when resolving a value:
  1. User store (data/microscopes/<sn>/calibration.j5), enabled entries
  2. Factory store (configs/<model>/calibration.j5), enabled entries
  3. Nominal values from microscope.j5 / code defaults (caller supplies)

An empty or missing store is valid: the system runs on nominals.
"""

import copy
import json
import os
import threading

from uscope.util import readj, writej, utcnow
from uscope import version

SCHEMA_VERSION = 1

# Keys used for scope matching, in increasing specificity
SCOPE_FIELDS = ("axis", "objective", "pixel_format", "wh", "gain")


def _cal_store(microscope):
    """Return the microscope's CalStore, or None."""
    if microscope is None:
        return None
    return getattr(microscope, "cal_store", None)


def _scope_tuple(scope):
    if not scope:
        return ()
    return tuple(sorted((k, _freeze(v)) for k, v in scope.items()
                        if k in SCOPE_FIELDS and v is not None))


def _freeze(v):
    if isinstance(v, list):
        return tuple(v)
    if isinstance(v, dict):
        return tuple(sorted((k, _freeze(x)) for k, x in v.items()))
    return v


def _scope_matches(entry_scope, query_scope):
    """True if every key in entry_scope is present and equal in query_scope.

    An empty entry scope matches any query (least specific).
    A query that omits a key does not match an entry that requires that key.
    """
    entry_scope = entry_scope or {}
    query_scope = query_scope or {}
    for k, v in entry_scope.items():
        if k not in SCOPE_FIELDS:
            continue
        if k not in query_scope:
            return False
        if _freeze(query_scope[k]) != _freeze(v):
            return False
    return True


def _scope_specificity(scope):
    if not scope:
        return 0
    return sum(1 for k in SCOPE_FIELDS if k in scope and scope[k] is not None)


class CalEntry:
    def __init__(self,
                 key,
                 value,
                 scope=None,
                 unit=None,
                 std=None,
                 n=None,
                 enabled=True,
                 measured_at=None,
                 pyuscope_version=None,
                 source=None,
                 notes="",
                 file=None,
                 needs_restart=False,
                 extra=None):
        self.key = key
        self.value = value
        self.scope = dict(scope or {})
        self.unit = unit
        self.std = std
        self.n = n
        self.enabled = bool(enabled)
        self.measured_at = measured_at or utcnow().isoformat()
        self.pyuscope_version = pyuscope_version or _pyuscope_version()
        self.source = source
        self.notes = notes or ""
        self.file = file
        self.needs_restart = bool(needs_restart)
        self.extra = dict(extra or {})

    def to_j(self):
        j = {
            "key": self.key,
            "scope": copy.deepcopy(self.scope),
            "value": copy.deepcopy(self.value),
            "enabled": self.enabled,
            "measured_at": self.measured_at,
            "pyuscope_version": self.pyuscope_version,
        }
        if self.unit is not None:
            j["unit"] = self.unit
        if self.std is not None:
            j["std"] = self.std
        if self.n is not None:
            j["n"] = self.n
        if self.source:
            j["source"] = self.source
        if self.notes:
            j["notes"] = self.notes
        if self.file:
            j["file"] = self.file
        if self.needs_restart:
            j["needs_restart"] = True
        if self.extra:
            j.update(self.extra)
        return j

    @classmethod
    def from_j(cls, j):
        known = {
            "key", "scope", "value", "unit", "std", "n", "enabled",
            "measured_at", "pyuscope_version", "source", "notes", "file",
            "needs_restart"
        }
        extra = {k: v for k, v in j.items() if k not in known}
        return cls(key=j["key"],
                   value=j.get("value"),
                   scope=j.get("scope") or {},
                   unit=j.get("unit"),
                   std=j.get("std"),
                   n=j.get("n"),
                   enabled=j.get("enabled", True),
                   measured_at=j.get("measured_at"),
                   pyuscope_version=j.get("pyuscope_version"),
                   source=j.get("source"),
                   notes=j.get("notes") or "",
                   file=j.get("file"),
                   needs_restart=j.get("needs_restart", False),
                   extra=extra)

    def identity(self):
        return (self.key, _scope_tuple(self.scope))

    def scope_label(self):
        if not self.scope:
            return "(unit)"
        parts = []
        for k in SCOPE_FIELDS:
            if k in self.scope and self.scope[k] is not None:
                parts.append("%s=%s" % (k, self.scope[k]))
        return ", ".join(parts) if parts else "(unit)"


def _pyuscope_version():
    try:
        meta = version.get_meta()
        return meta.get("description") or "4.7.0"
    except Exception:
        return "4.7.0"


class CalStore:
    def __init__(self, path, factory_path=None, microscope=None):
        self.path = path
        self.factory_path = factory_path
        self.microscope = microscope
        self.dir = os.path.dirname(path) if path else None
        self._user = []
        self._factory = []
        self._lock = threading.RLock()
        self._listeners = []

    @classmethod
    def for_microscope(cls, microscope):
        data_dir = microscope.usc.get_microscope_data_dir()
        user_fn = os.path.join(data_dir, "calibration.j5")
        factory_fn = os.path.join(microscope.usc.get_config_dir(),
                                  "calibration.j5")
        store = cls(user_fn, factory_path=factory_fn, microscope=microscope)
        store.load()
        store.migrate_legacy()
        store.migrate_crop_pixels()
        return store

    def subscribe(self, callback):
        """callback(store, keys) after a mutating save."""
        self._listeners.append(callback)

    def _notify(self, keys):
        for cb in list(self._listeners):
            try:
                cb(self, keys)
            except Exception:
                pass

    def load(self):
        with self._lock:
            self._user = self._load_file(self.path)
            self._factory = self._load_file(self.factory_path)

    @staticmethod
    def _load_file(path):
        if not path or not os.path.exists(path):
            return []
        try:
            j = readj(path)
        except Exception as e:
            print("WARNING: failed to load calibration %s: %s" % (path, e))
            return []
        entries = []
        for ej in j.get("entries") or []:
            try:
                entries.append(CalEntry.from_j(ej))
            except Exception as e:
                print("WARNING: skipped calibration entry in %s: %s" %
                      (path, e))
        return entries

    def save(self):
        with self._lock:
            if not self.path:
                return
            parent = os.path.dirname(self.path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent)
            j = {
                "schema_version": SCHEMA_VERSION,
                "microscope": self._microscope_meta(),
                "entries": [e.to_j() for e in self._user],
            }
            tmp = self.path + ".tmp"
            writej(tmp, j)
            os.replace(tmp, self.path)

    def _microscope_meta(self):
        if not self.microscope:
            return {}
        return {
            "model": self.microscope.model(),
            "serial": self.microscope.serial(),
        }

    def entries(self, include_factory=True, include_disabled=True):
        """User entries first, then factory entries not shadowed by user."""
        with self._lock:
            out = list(self._user)
            if include_factory:
                user_ids = {e.identity() for e in self._user}
                for e in self._factory:
                    if e.identity() not in user_ids:
                        out.append(e)
            if not include_disabled:
                out = [e for e in out if e.enabled]
            return out

    def get(self, key, scope=None, include_disabled=False):
        """Best matching entry for key+scope, or None.

        Among matching enabled entries, the most specific scope wins.
        User entries beat factory entries at the same specificity.
        """
        scope = scope or {}
        best = None
        best_spec = -1
        best_user = False
        with self._lock:
            layered = [(e, True) for e in self._user] + [
                (e, False) for e in self._factory
            ]
        for e, is_user in layered:
            if e.key != key:
                continue
            if not include_disabled and not e.enabled:
                continue
            if not _scope_matches(e.scope, scope):
                continue
            spec = _scope_specificity(e.scope)
            if spec > best_spec or (spec == best_spec and is_user
                                    and not best_user):
                best = e
                best_spec = spec
                best_user = is_user
        return best

    def get_value(self, key, scope=None, default=None):
        e = self.get(key, scope=scope)
        if e is None:
            return default
        return e.value

    def find_exact(self, key, scope=None, include_factory=False,
                   include_user=True):
        scope_t = _scope_tuple(scope)
        with self._lock:
            pools = []
            if include_user:
                pools.append(self._user)
            if include_factory:
                pools.append(self._factory)
            for pool in pools:
                for e in pool:
                    if e.key == key and _scope_tuple(e.scope) == scope_t:
                        return e
        return None

    def set(self, entry, save=True, notify=True):
        """Insert or replace a user entry with the same key+scope."""
        if not isinstance(entry, CalEntry):
            entry = CalEntry.from_j(entry)
        with self._lock:
            ident = entry.identity()
            self._user = [e for e in self._user if e.identity() != ident]
            self._user.append(entry)
        if save:
            self.save()
        if notify:
            self._notify([entry.key])
        return entry

    def delete(self, key, scope=None, save=True, notify=True):
        scope_t = _scope_tuple(scope)
        with self._lock:
            before = len(self._user)
            self._user = [
                e for e in self._user
                if not (e.key == key and _scope_tuple(e.scope) == scope_t)
            ]
            changed = len(self._user) != before
        if changed and save:
            self.save()
        if changed and notify:
            self._notify([key])
        return changed

    def set_enabled(self, key, scope, enabled, save=True, notify=True):
        e = self.find_exact(key, scope)
        if e is None:
            # Promote a factory entry to user so we can disable it
            e = self.find_exact(key, scope, include_factory=True)
            if e is None:
                return False
            e = CalEntry.from_j(e.to_j())
            self.set(e, save=False, notify=False)
        e.enabled = bool(enabled)
        if save:
            self.save()
        if notify:
            self._notify([key])
        return True

    def objective_entries(self, name, include_factory=False):
        """Entries scoped to objective name"""
        return [
            e for e in self.entries(include_factory=include_factory)
            if e.scope.get("objective") == name
        ]

    def rename_objective(self, old, new, save=True, notify=True):
        """
        Move user entries scoped to objective old to objective new
        Entries already stored for new are replaced. Returns the number moved
        """
        with self._lock:
            moving = [
                e for e in self._user if e.scope.get("objective") == old
            ]
            if not moving:
                return 0
            for e in moving:
                e.scope = dict(e.scope, objective=new)
            idents = {e.identity() for e in moving}
            self._user = [
                e for e in self._user
                if e in moving or e.identity() not in idents
            ]
        if save:
            self.save()
        if notify:
            self._notify(sorted({e.key for e in moving}))
        return len(moving)

    def as_dict(self):
        """Planner / scan metadata view of currently enabled values."""
        ret = {"schema_version": SCHEMA_VERSION, "entries": []}
        for e in self.entries(include_disabled=False):
            ret["entries"].append(e.to_j())
        return ret

    def migrate_legacy(self):
        """Import a legacy flat-field TIFF if no illumination entry exists."""
        if not self.microscope:
            return
        try:
            ff_fn = self.microscope.usc.imager.ff_cal_fn()
        except Exception:
            return
        if not os.path.exists(ff_fn):
            return
        if self.get("imager.illumination", include_disabled=True) is not None:
            return
        rel = os.path.relpath(ff_fn, self.dir) if self.dir else ff_fn
        self.set(CalEntry(
            key="imager.illumination",
            value={"legacy_tif": True},
            file=rel,
            source="legacy:imager_calibration_ff.tif",
            notes="Imported existing flat-field TIFF",
            enabled=True,
        ),
                 save=True,
                 notify=False)

    def _measured_wh(self, entry):
        """
        Raw resolution a calibration entry was measured at
        From the entry itself or its dataset manifest (source), None if unknown
        """
        wh = entry.scope.get("wh")
        if wh:
            return int(wh[0]), int(wh[1])
        if not entry.source:
            return None
        path = entry.source
        if not os.path.isabs(path):
            if not self.dir:
                return None
            path = os.path.join(self.dir, path)
        fn = os.path.join(path, "manifest.json")
        if not os.path.exists(fn):
            return None
        try:
            imagerj = readj(fn).get("imager") or {}
            return int(imagerj["width"]), int(imagerj["height"])
        except Exception:
            return None

    def migrate_crop_pixels(self, default_wh=None):
        """
        Replace imager.crop_pixels entries with imager.crop_fractions (same scope)
        Pixels are converted at the resolution they were measured at if known,
        else default_wh (default: native resolution)
        The old entry is kept but disabled. Factory entries are converted in memory
        Returns the number of entries migrated
        """
        from uscope.imager import crop
        if default_wh is None and self.microscope is not None:
            try:
                default_wh = self.microscope.usc.imager.native_wh()
            except Exception:
                default_wh = None
        migrated = []
        with self._lock:
            for pool in (self._user, self._factory):
                for e in list(pool):
                    if e.key != "imager.crop_pixels" or not e.enabled:
                        continue
                    scope = {
                        k: v
                        for k, v in e.scope.items() if k == "objective"
                    }
                    ident = ("imager.crop_fractions", _scope_tuple(scope))
                    if any(x.identity() == ident for x in pool):
                        e.enabled = False
                        migrated.append(e)
                        continue
                    wh = self._measured_wh(e) or default_wh
                    if wh is None:
                        print(
                            "WARNING: can't migrate imager.crop_pixels yet: resolution unknown"
                        )
                        continue
                    try:
                        value = crop.pixels_to_fractions(e.value, wh)
                    except Exception as ex:
                        print("WARNING: can't migrate imager.crop_pixels %s: %s" %
                              (e.value, ex))
                        continue
                    pool.append(
                        CalEntry(key="imager.crop_fractions",
                                 value=value,
                                 scope=scope,
                                 measured_at=e.measured_at,
                                 pyuscope_version=e.pyuscope_version,
                                 source=e.source,
                                 notes="Converted from crop_pixels %s at %ux%u"
                                 % (json.dumps(e.value, sort_keys=True), wh[0],
                                    wh[1])))
                    e.enabled = False
                    migrated.append(e)
            user_changed = any(e in self._user for e in migrated)
        if user_changed:
            self.save()
        if migrated:
            self._notify(["imager.crop_fractions"])
        return len(migrated)

    def migrate_um_per_pixel(self, value, source="legacy:argus.j5"):
        """One-time import of GUI-cache um_per_pixel_raw_1x."""
        if value is None:
            return False
        if self.get("optics.um_per_pixel_raw_1x",
                    include_disabled=True) is not None:
            return False
        self.set(CalEntry(
            key="optics.um_per_pixel_raw_1x",
            value=float(value),
            unit="um",
            source=source,
            notes="Imported from GUI cache",
        ))
        return True

    def illumination_abspath(self, entry):
        if entry is None or not entry.file:
            return None
        if os.path.isabs(entry.file):
            return entry.file
        if self.dir:
            return os.path.normpath(os.path.join(self.dir, entry.file))
        return entry.file
