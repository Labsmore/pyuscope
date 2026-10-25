import copy
import threading
from collections import OrderedDict
"""
Sample usage:
with StopEvent(microscope) as se:
    do_stuff()
    se.poll()
    do_stuff()
"""


def objective_name(objective, objectivei):
    """
    Display name of a (DB resolved) objective
    Also its calibration key, so renaming orphans calibration entries
    """
    if "name" in objective:
        return objective["name"]
    if "magnification" in objective:
        if "series" in objective:
            return "%s %uX" % (objective["series"],
                               objective["magnification"])
        else:
            return "%uX" % objective["magnification"]
    else:
        return "Objective %u" % objectivei


class MicroscopeObjectives:
    def __init__(self, microscope):
        self.microscope = microscope
        """
        Return objectives w/ automatic sizing (if applicable) applied

        returns:
        x_view: in final scaled image how many mm wide
        um_per_pixel: in final scaled image how many micrometers each pixel represents
        magnification: optional
        na: optional
        """
        self.magnification = 1.0
        # override value or can be None
        # If none objectives must all be fully specified individually
        self.um_per_pixel_raw_1x = self.microscope.usc.optics.um_per_pixel_raw_1x(
        )
        # Why the user lineup isn't in use (None if it is or there is none)
        self.lineup_error = None
        self.reload_inputs()

    def uses_lineup(self):
        """True if objectives come from the user lineup, not microscope.j5"""
        return self._uses_lineup

    def reload_inputs(self):
        """
        Re-read the objective configuration (ex: user lineup changed)
        Updates in place so existing references stay valid
        A broken user lineup falls back to the microscope defaults
        """
        usc = self.microscope.usc
        self.lineup_error = None
        lineup = bool(usc.objective_lineup())
        # Copy so we can start filling in data
        self.objectives_in = copy.deepcopy(
            usc.get_uncalibrated_objectives(microscope=self.microscope))
        self._uses_lineup = lineup
        if not lineup:
            self.recalculate_db()
            return
        try:
            self.recalculate_db()
        except Exception as e:
            self.lineup_error = str(e)
            print("WARNING: objectives: your lineup is unusable (%s), using microscope defaults" % (e, ))
            self.objectives_in = copy.deepcopy(
                usc.get_uncalibrated_objectives(microscope=self.microscope,
                                                lineup=False))
            self._uses_lineup = False
            self.recalculate_db()

    def scale_objectives_1x(self):
        # In raw sensor pixels before scaling
        # That way can adjust scaling w/o adjusting
        # This is the now preferred way to set configuration
        if not self.um_per_pixel_raw_1x:
            return

        # crop_w, _crop_h = self.imager.cropped_wh()
        final_w, final_h = self.microscope.usc.imager.final_wh()
        # Objectives must support magnification to scale
        for objective in self.objectives.values():
            if "um_per_pixel" not in objective:
                objective[
                    "um_per_pixel"] = self.um_per_pixel_raw_1x / objective[
                        "magnification"] / self.microscope.usc.imager.scalar()
            if "x_view" not in objective:
                # um to mm
                objective[
                    "x_view"] = final_w * self.um_per_pixel_raw_1x / self.microscope.usc.imager.scalar(
                    ) / objective["magnification"] / 1000
            if "y_view" not in objective:
                # um to mm
                objective[
                    "y_view"] = final_h * self.um_per_pixel_raw_1x / self.microscope.usc.imager.scalar(
                    ) / objective["magnification"] / 1000

    def apply_objective_tsettle(self):
        reference_tsettle_motion = self.microscope.usc.kinematics.tsettle_motion_na1(
        )
        reference_na = 1.0
        store = getattr(self.microscope, "cal_store", None)
        # name => tsettle_motion without calibration store overrides
        self.tsettle_motion_nominal = {}
        # Objectives must support magnification to scale
        for name, objective in self.objectives.items():
            if "tsettle_motion" in objective:
                tsettle_motion = objective["tsettle_motion"]
            else:
                # Ex: 2.0 sec sleep at 100x 1.0 NA => 20x 0.42 NA => 0.84 sec sleep
                # Assume conservative NA (high power oil objective) if not specified
                HIGHEST_NA = 1.4
                tsettle_motion = reference_tsettle_motion * objective.get(
                    "na", HIGHEST_NA) / reference_na
            self.tsettle_motion_nominal[name] = tsettle_motion
            if store:
                entry = store.get("kinematics.tsettle_motion",
                                  {"objective": name})
                if entry is not None:
                    tsettle_motion = float(entry.value)
            objective["tsettle_motion"] = tsettle_motion

    def names(self):
        return [objective["name"] for objective in self.objectives.values()]

    def get_full_config(self):
        """
        Return JSON config structure for all objectives
        """
        return self.objectives

    def get_config(self, name):
        """
        Return JSON config structure for given objectives
        """
        return self.objectives[name]

    def default_name(self):
        # First name
        return list(self.objectives.keys())[0]

    def check_db(self):
        # Sanity check required parameters
        names = set()
        for objectivei, objective in enumerate(self.objectives.values()):
            # last ditch name
            if "name" not in objective:
                if "magnification" in objective:
                    if "series" in objective:
                        objective["name"] = "%s %uX" % (
                            objective["series"], objective["magnification"])
                    else:
                        objective["name"] = "%uX" % objective["magnification"]
                else:
                    objective["name"] = "Objective %u" % objectivei
            assert "name" in objective, objective
            assert objective[
                "name"] not in names, f"Duplicate objective name {objective}"
            names.add(objective["name"])
            assert "x_view" in objective, objective
            assert "um_per_pixel" in objective, objective
            assert "tsettle_motion" in objective, objective

    def fill_um_per_pixel(self):
        final_w, final_h = self.microscope.usc.imager.final_wh()
        for objective in self.objectives.values():
            if "um_per_pixel" not in objective:
                if "x_view" not in objective:
                    raise Exception(
                        "Failed to calculate objective um_per_pixel: need x_view. Microscope missing um_per_pixel_raw_1x?"
                    )
                # mm to um
                objective[
                    "um_per_pixel"] = objective["x_view"] / final_w * 1000

    def scale_mag(self):
        """
        This is tacked on the end due to history on how um_per_pixel is calculated
        """
        for objective in self.objectives.values():
            # Higher magnification means each pixel sees fewer um
            objective["um_per_pixel"] /= self.magnification
            # Similarly field of view is reduced
            objective["x_view"] /= self.magnification
            objective["y_view"] /= self.magnification

    def name_objective(self, objective, objectivei):
        return objective_name(objective, objectivei)

    def recalculate_db(self):
        objectives_list = copy.deepcopy(self.objectives_in)
        # Start by filling in missing metadata from DB
        # Adds enough background to name them
        self.microscope.usc.bc.objective_db.set_defaults_list(objectives_list)

        # Assign names and move into final form
        self.objectives = OrderedDict()
        for objectivei, objective in enumerate(objectives_list):
            name = self.name_objective(objective, objectivei)
            if name in self.objectives:
                raise ValueError(f"Objective '{name}' duplicated. Check there isn't another objective with the same Series and Magnification, or the same name.")
            self.objectives[name] = objective

        # Now apply system specific sizing / calibration
        self.scale_objectives_1x()
        # Derive kinematics parameters
        # (ie slower settling at higher mag)
        self.apply_objective_tsettle()
        self.fill_um_per_pixel()

        self.check_db()
        self.scale_mag()
        self.check_db()

    def set_global_scalar(self, magnification, recalculate=True):
        """
        Set a magnification factor
        Intended to:
        -Support barlow lens
        -Support swapping relay lens
        -Correcting a systematic offset

        In the future we will probably also support per objective correction
        """
        assert magnification
        self.magnification = magnification
        if recalculate:
            self.recalculate_db()

    def set_um_per_pixel_raw_1x(self, value, recalculate=True):
        """
        Set the base calibration value used to rescale the system
        """
        self.um_per_pixel_raw_1x = value
        if recalculate:
            self.recalculate_db()


class ObjectiveState:
    """
    Front end independent objective selection
    Owns which objective is active and the scalars applied to the objective DB
    Thread safe

    Listeners are called with the active objective config (dict) whenever it
    changes (selection, scalars, calibration). They may be called from any thread
    """

    # Calibration keys that change the computed objective DB
    CAL_KEYS = {"optics.um_per_pixel_raw_1x", "kinematics.tsettle_motion"}

    def __init__(self, microscope, log=None):
        self.microscope = microscope
        self._log = log
        self.lock = threading.RLock()
        self.objectives = None
        self.selected_name = None
        # User magnification correction (ex: barlow). None => 1.0
        self.global_scalar = None
        # Overrides microscope / calibration value. None => no override
        self.um_per_pixel_raw_1x = None
        self.listeners = []
        store = getattr(self.microscope, "cal_store", None)
        if store is not None:
            store.subscribe(self._on_cal_changed)

    def log(self, msg):
        if self._log:
            self._log(msg)
        else:
            self.microscope.log(msg)

    def add_listener(self, cb):
        self.listeners.append(cb)

    def _notify(self, config):
        for cb in list(self.listeners):
            cb(config)

    def _on_cal_changed(self, store, keys):
        if "optics.um_per_pixel_raw_1x" in (keys or []):
            # Store (else microscope.j5) is now the source of truth
            with self.lock:
                self.um_per_pixel_raw_1x = None
        # Microscope already applied these to its objectives
        if self.objectives is not None and set(keys or []) & self.CAL_KEYS:
            self._notify(self.config())

    """
    Objective DB
    """

    def reload(self, reread=False):
        """
        Recompute the objective DB with current scalars
        reread: also re-read the objective list (ex: user lineup changed)
        Keeps the selected objective if it still exists
        """
        with self.lock:
            self.objectives = self.microscope.get_objectives()
            if reread:
                self.objectives.reload_inputs()
            self.objectives.set_global_scalar(self.global_scalar or 1.0,
                                              recalculate=False)
            self.objectives.set_um_per_pixel_raw_1x(
                self.um_per_pixel_raw_1x
                or self.microscope.usc.optics.um_per_pixel_raw_1x(),
                recalculate=False)
            self.objectives.recalculate_db()
            if self.selected_name not in self.objectives.names():
                self.selected_name = self.objectives.default_name()
            config = self.config()
        self._notify(config)

    def _objectives(self):
        if self.objectives is None:
            self.reload()
        return self.objectives

    def names(self):
        with self.lock:
            return self._objectives().names()

    def name(self):
        """Active objective name. None until objectives are loaded"""
        return self.selected_name

    def config(self):
        """Active objective config (dict)"""
        with self.lock:
            return self._objectives().get_config(self.selected_name)

    def full_config(self):
        with self.lock:
            return self._objectives().get_full_config()

    def tsettle_motion_nominal(self, name):
        """Settle time (s) for name ignoring the calibration store"""
        with self.lock:
            return self._objectives().tsettle_motion_nominal.get(name)

    def view_um(self):
        """(width, height) of the final image in um"""
        config = self.config()
        im_w_pix, im_h_pix = self.microscope.usc.imager.cropped_wh()
        im_w_um = config["x_view"]
        return im_w_um, im_w_um * im_h_pix / im_w_pix

    """
    Selection
    """

    def select(self, name, parfocal=True):
        """
        Make name the active objective
        parfocal: move Z by the calibrated offset between the objectives (if enabled)
        """
        with self.lock:
            if name not in self._objectives().names():
                raise ValueError(f"Unknown objective {name}")
            old_name = self.selected_name
            if name == old_name:
                return
            self.selected_name = name
            config = self.config()
        self.log("Selected objective %s" % name)
        self._notify(config)
        if parfocal:
            self.go_parfocal_z(name, old_name=old_name)

    def parfocal_delta(self, new_name, old_name):
        """Z (mm) to move going from old_name to new_name. None if not enabled"""
        if not old_name or not new_name or old_name == new_name:
            return None
        store = getattr(self.microscope, "cal_store", None)
        if store is None:
            return None
        e_new = store.get("optics.z_offset", {"objective": new_name})
        if e_new is None or not e_new.enabled:
            return None
        e_old = store.get("optics.z_offset", {"objective": old_name})
        z_old = float(e_old.value) if e_old is not None else 0.0
        delta = float(e_new.value) - z_old
        if abs(delta) < 1e-6:
            return None
        return delta

    def go_parfocal_z(self, name=None, old_name=None):
        """Move Z by the stored offset for name versus old_name (default: current)"""
        name = name or self.name()
        if old_name is None:
            old_name = self.name()
        delta = self.parfocal_delta(name, old_name)
        if delta is None:
            return
        self.log("Parfocal: moving Z by %0.4f mm (%s -> %s)" %
                 (delta, old_name, name))
        self.microscope.motion_thread.move_relative({"z": delta})

    """
    Scalars
    """

    def set_global_scalar(self, val):
        """None => 1.0 (no correction)"""
        with self.lock:
            self.global_scalar = None if val is None else float(val)
        self.log(f"Setting global objective scalar {self.global_scalar or 1.0}")
        self.reload()

    def set_um_per_pixel_raw_1x(self, val):
        with self.lock:
            self.um_per_pixel_raw_1x = val
        self.reload()

    """
    Per microscope S/N cache (see Microscope.cache_sn_save)
    """

    def cache_sn_save(self, cachej):
        with self.lock:
            cachej["objective"] = {
                "name": self.selected_name,
                "global_scalar":
                "" if self.global_scalar is None else str(self.global_scalar),
                "um_per_pixel_raw_1x": self.um_per_pixel_raw_1x,
            }

    def cache_sn_load(self, cachej, migrate=True):
        """
        migrate: import a legacy um_per_pixel_raw_1x into the calibration store
        """
        j = cachej.get("objective", {})
        global_scalar = None
        s = str(j.get("global_scalar") or "").strip()
        if s:
            try:
                global_scalar = float(s)
            except ValueError:
                self.log("Failed to parse global image scalar")
        um_per_pixel_raw_1x = j.get("um_per_pixel_raw_1x", None)
        store = getattr(self.microscope, "cal_store", None)
        if store is not None:
            if um_per_pixel_raw_1x and migrate:
                store.migrate_um_per_pixel(um_per_pixel_raw_1x)
            v = store.get_value("optics.um_per_pixel_raw_1x")
            if v is not None:
                um_per_pixel_raw_1x = v
        with self.lock:
            self.global_scalar = global_scalar
            self.um_per_pixel_raw_1x = um_per_pixel_raw_1x
            name = j.get("name", None)
            if name:
                self.selected_name = name
        self.reload()
