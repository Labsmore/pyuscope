"""
Imager property model (exposure, white balance, etc)
Qt free: used by both Argus (uscope/gui/control_scroll.py renders it)
and headless sessions

There are two forms properties are used:
-Raw: the underlying property name + value
    ex: auto_flgs value 1 means auto exposure is disabled
-Disp: "as displayed". A human friendly form
    ex: "Auto-exposure" value False
Calibration files and image metadata store properties in disp form

Property config keys (as given by plugins):
    prop_name: raw name
    disp_name: displayed name (default: prop_name)
    type: "int" or "bool"
    min / max: required for int
    default: camera default (None: take first value read)
    conv: raw <=> disp converter name (see CONVERTERS)
    ro: read only, never written by the user
    gui_driven: the user (front end) owns the value
        True: user changes are written to the camera, read back values are ignored
        False: the camera owns the value (ex: auto exposure active) and it is polled
    optional: may be silently missing from calibration files / this camera
    visible: show in normal (non dev mode) GUI
    virtual: not a camera property, see VirtualProperty

Thread safe: reads and writes may come from any thread
Front ends observe changes with add_listener()
"""

from collections import OrderedDict
import threading

import piexif


class BoolConverter:
    """0 vs 1 int value as a bool"""
    def raw2disp(self, val):
        return bool(val)

    def disp2raw(self, val):
        return int(bool(val))


class IdentityConverter:
    def raw2disp(self, val):
        return val

    def disp2raw(self, val):
        return val


"""
enum  v4l2_exposure_auto_type {
    V4L2_EXPOSURE_AUTO = 0,
    V4L2_EXPOSURE_MANUAL = 1,
    V4L2_EXPOSURE_SHUTTER_PRIORITY = 2,
    V4L2_EXPOSURE_APERTURE_PRIORITY = 3
};
Cameras seen so far only do manual (1) and aperture priority (3)
"""
V4L2_AUTO_EXPOSURE_VAL = 3


class V4L2AutoExposureConverter:
    def raw2disp(self, val):
        return val == V4L2_AUTO_EXPOSURE_VAL

    def disp2raw(self, val):
        return V4L2_AUTO_EXPOSURE_VAL if val else 1


CONVERTERS = {
    "identity": IdentityConverter(),
    "bool": BoolConverter(),
    "v4l2_auto_exposure": V4L2AutoExposureConverter(),
}


class VirtualProperty:
    """
    A property not backed by the camera
    ex: software auto exposure
    """
    def __init__(self, name=None, value=None, session=None):
        self.name = name
        self.value = value
        self.session = session

    def read(self):
        return self.value

    def write(self, value):
        self.value = value


class AutoExposureSoftwareVP(VirtualProperty):
    def read(self):
        return self.session.imager_control_thread.auto_exposure()

    def write(self, value):
        return self.session.imager_control_thread.set_auto_exposure(value)


class AutoExposureSoftwareTargetVP(VirtualProperty):
    def read(self):
        return self.session.imager_control_thread.auto_exposure_target100()

    def write(self, value):
        return self.session.imager_control_thread.set_auto_exposure_target100(
            value)


def normalize_property(prop):
    """
    Fill in defaults for a templated property config
    """
    if isinstance(prop, dict):
        ret = dict(prop)
    else:
        ret = {"prop_name": prop}

    ret.setdefault("disp_name", ret["prop_name"])
    assert "type" in ret, ret
    assert "default" in ret, ret
    ret.setdefault("ro", False)
    ret.setdefault("gui_driven", not ret["ro"])
    if ret["type"] == "int":
        assert "min" in ret, ret
        assert "max" in ret, ret
        ret.setdefault("conv", "identity")
    elif ret["type"] == "bool":
        ret.setdefault("conv", "bool")
    else:
        raise ValueError(f"bad property type {ret['type']} in {ret}")
    if ret["conv"] not in CONVERTERS:
        raise ValueError(f"bad property converter {ret['conv']} in {ret}")
    return ret


class ImagerProperties:
    """
    Base class: subclasses implement _raw_prop_read() / _raw_prop_write()
    and optionally template_property() to fill in configs from the device

    groups: {group_name: [property config, ...]}
    """
    def __init__(self, session, plugin=None, groups=None):
        self.session = session
        self.microscope = session.microscope
        self.plugin = plugin
        self.verbose = False
        self.groups_config = groups if groups is not None else OrderedDict()
        # {group_name: OrderedDict(prop_name: config)}, filled by setup()
        self.groups = OrderedDict()
        # NOTE: both are expected to always contain the same set of properties
        self.disp2prop = OrderedDict()
        self.raw2prop = OrderedDict()
        self.optional_disp_props = set()
        self.virtual_properties = {}
        # Used for saving / restoring state
        # In particular to restore if the camera disconnects
        self.raw_cache = {}
        self.disp_cache = {}
        self.listeners = []
        self.lock = threading.RLock()
        self.is_setup = False

    def log(self, msg):
        self.microscope.log(msg)

    def add_virtual_property(self, vp):
        self.virtual_properties[vp.name] = vp

    def add_listener(self, listener):
        """
        listener may implement any of (called from any thread):
            properties_set(vals): disp properties were written
            properties_read(vals): disp properties were polled
            gui_driven_changed(disp_name, val)
        """
        self.listeners.append(listener)

    def _notify(self, method, *args):
        for listener in self.listeners:
            f = getattr(listener, method, None)
            if f:
                f(*args)

    """
    Setup
    """

    def template_property(self, prop_entry):
        """
        Fill in missing config (ex: range, default) from the device
        """
        ret = {"default": None}
        ret.update(prop_entry)
        return ret

    def validate_prop_config(self, prop_config):
        """
        Return True if should keep
        Return False if should drop (optional / not on this system)
        Throw exception if inherently bad (not optional and not found)
        """
        return True

    def setup(self):
        """
        Call once the imager is running
        Some backends need the device open to describe properties
        """
        with self.lock:
            for group_name, properties in self.groups_config.items():
                propdict = OrderedDict()
                for prop_entry in properties:
                    if isinstance(prop_entry, str):
                        prop_entry = {"prop_name": prop_entry}
                    if prop_entry.get("virtual"):
                        prop = dict(prop_entry)
                    else:
                        prop = self.template_property(prop_entry)
                    if prop.get("optional", True):
                        self.optional_disp_props.add(
                            prop.get("disp_name", prop["prop_name"]))
                    if not self.validate_prop_config(prop):
                        continue
                    prop = normalize_property(prop)
                    self._index_property(prop)
                    propdict[prop["prop_name"]] = prop
                self.groups[group_name] = propdict
            self.is_setup = True

    def _index_property(self, prop):
        disp_name = prop["disp_name"]
        prop_name = prop["prop_name"]
        assert disp_name not in self.disp2prop, disp_name
        assert prop_name not in self.raw2prop, prop_name
        self.disp2prop[disp_name] = prop
        self.raw2prop[prop_name] = prop

    def properties(self):
        """
        Iterate (group_name, property config)
        """
        for group_name, props in self.groups.items():
            for prop in props.values():
                yield group_name, prop

    def get_property_config(self, disp_name):
        return self.disp2prop[disp_name]

    def is_disp_prop_optional(self, disp_prop):
        return disp_prop in self.optional_disp_props

    def validate_disp_names(self, disp_names):
        for disp_name in disp_names:
            if disp_name not in self.disp2prop:
                raise ValueError("Invalid property %s" % (disp_name, ))

    """
    User vs camera ownership
    """

    def is_gui_driven(self, disp_name):
        return self.disp2prop[disp_name]["gui_driven"]

    def set_gui_driven(self, val, disp_names=None):
        """
        val
            True: the user owns the value. Writes go to the device
            False: the device owns the value. Values are polled
        disp_names
            None: all values
            iterable: only these
        """
        val = bool(val)
        if disp_names:
            self.validate_disp_names(disp_names)
        changed = []
        with self.lock:
            for disp_name, prop in self.disp2prop.items():
                if disp_names and disp_name not in disp_names:
                    continue
                if prop["gui_driven"] != val:
                    prop["gui_driven"] = val
                    changed.append(disp_name)
        for disp_name in changed:
            self._notify("gui_driven_changed", disp_name, val)

    """
    Device access
    """

    def _raw_prop_write(self, name, value):
        raise NotImplementedError("Required")

    def _raw_prop_read(self, name):
        raise NotImplementedError("Required")

    def _rawvirt_prop_write(self, name, val):
        if name in self.virtual_properties:
            self.virtual_properties[name].write(val)
        else:
            self._raw_prop_write(name, val)

    def _rawvirt_prop_read(self, name):
        if name in self.virtual_properties:
            return self.virtual_properties[name].read()
        else:
            return self._raw_prop_read(name)

    def _converter(self, prop):
        return CONVERTERS[prop["conv"]]

    def _mark_imager_changed(self):
        imager = self.microscope.imager
        if imager:
            imager.properties_changed()

    def raw_prop_write(self, name, value):
        with self.lock:
            self.verbose and print(f"raw_prop_write() {name} = {value}")
            self._rawvirt_prop_write(name, value)
            self.raw_cache[name] = value
            # Includes writes that bypass set_disp_properties()
            # (ex: GUI sliders, crash recovery)
            self._mark_imager_changed()
            prop = self.raw2prop.get(name)
            if prop:
                self.disp_prop_was_rw(prop["disp_name"],
                                      self._converter(prop).raw2disp(value))

    def raw_prop_read(self, name):
        with self.lock:
            ret = self._rawvirt_prop_read(name)
            self.verbose and print(f"raw_prop_read() {name} = {ret}")
            self.raw_cache[name] = ret
            prop = self.raw2prop.get(name)
            if prop:
                self.disp_prop_was_read(prop["disp_name"],
                                        self._converter(prop).raw2disp(ret))
            return ret

    def disp_prop_read(self, disp_name):
        with self.lock:
            prop = self.disp2prop[disp_name]
            raw = self.raw_prop_read(prop["prop_name"])
            ret = self._converter(prop).raw2disp(raw)
            self.disp_cache[disp_name] = ret
            return ret

    def coerce_disp_value(self, prop, val):
        """
        Value of the property's type
        Lets clients that only have JSON numbers (ex: web) set int / bool properties
        Floats are rounded (ex: computed exposures)
        """
        if prop["type"] == "int":
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(
                    f"Property {prop['disp_name']}: expected int, got {val!r}")
            return int(round(val))
        if prop["type"] == "bool":
            if val not in (True, False, 0, 1):
                raise ValueError(
                    f"Property {prop['disp_name']}: expected bool, got {val!r}")
            return bool(val)
        return val

    def disp_prop_write(self, disp_name, disp_val):
        with self.lock:
            prop = self.disp2prop[disp_name]
            disp_val = self.coerce_disp_value(prop, disp_val)
            raw_val = self._converter(prop).disp2raw(disp_val)
            self.raw_prop_write(prop["prop_name"], raw_val)
            self.disp_cache[disp_name] = disp_val

    def disp_prop_was_read(self, name, value):
        prop = self.disp2prop.get(name)
        # Ignore incoming data when the user owns the value
        # Among things this prevents race conditions when changing state
        if prop and prop["gui_driven"]:
            return
        self.disp_prop_was_rw(name, value)

    def disp_prop_was_rw(self, name, value):
        """
        Called after a property is read or written
        Allows setting more advanced control behaviors
        Ex: when auto-exposure is enabled the camera owns exposure
        """
        pass

    """
    High level API
    """

    def get_disp_properties(self):
        """
        Read all properties from the device, indexed by display name
        """
        ret = {}
        with self.lock:
            for disp_name, prop in self.disp2prop.items():
                val = self.disp_prop_read(disp_name)
                ret[disp_name] = val
                # If we don't have a default take first value
                if prop["default"] is None:
                    prop["default"] = val
        return ret

    def get_disp_properties_ts(self):
        """
        Most recently read / written values (doesn't touch the device)
        """
        with self.lock:
            return dict(self.disp_cache)

    def get_disp_property_ts(self, k):
        with self.lock:
            return self.disp_cache[k]

    def set_disp_properties(self, vals):
        """
        Set properties indexed by display name
        Unknown optional properties are ignored
        (ex: calibration from a different camera)
        """
        written = {}
        with self.lock:
            for disp_name, val in vals.items():
                if disp_name not in self.disp2prop:
                    if disp_name in self.optional_disp_props:
                        continue
                    raise ValueError(
                        f"Unknown imager property {disp_name}. Have: {list(self.disp2prop.keys())}"
                    )
                self.disp_prop_write(disp_name, val)
                written[disp_name] = self.disp_cache[disp_name]
        self._notify("properties_set", written)

    def update_by_reading(self):
        """
        Poll the device. Front ends call this periodically
        Returns the read values or None on a device error
        """
        try:
            vals = self.get_disp_properties()
        # 2023-12-17
        # VM1 / UVC issue trying to chase down
        # Can we recover or do we need to re-open the camera?
        except OSError:
            self.log("WARNING: camera bad file descriptor on read")
            return None
        self._notify("properties_read", vals)
        return vals

    def cam_defaults(self):
        return {
            disp_name: prop["default"]
            for disp_name, prop in self.disp2prop.items()
            if prop["default"] is not None and not prop["ro"]
        }

    def apply_cam_defaults(self):
        self.set_disp_properties(self.cam_defaults())

    def apply_microscope_defaults(self):
        self.apply_cam_defaults()
        # Then override microscope specific properties
        self.cal_load(load_data_dir=False)

    def cal_load(self, load_data_dir=True):
        try:
            j = self.microscope.usc.imager.cal_load(
                load_data_dir=load_data_dir)
        except Exception as e:
            self.log("WARNING: Failed to load cal: %s" % (e, ))
            return False
        if not j:
            return False
        self.set_disp_properties(j)
        return True

    def cal_save(self):
        self.microscope.usc.imager.cal_save_to_data(
            source=self.source_name(),
            disp_properties=self.get_disp_properties(),
            mkdir=True)

    def source_name(self):
        if self.plugin:
            return self.plugin.name()
        return self.microscope.usc.imager.source()

    def get_prop_cache(self):
        with self.lock:
            return {
                "disp": dict(self.disp_cache),
                "raw": dict(self.raw_cache),
            }

    def recover_video_crash(self, prop_cache):
        """
        Restore properties after the device was re-opened
        Assumes that only disp properties are needed
        """
        for disp_name, disp_val in prop_cache["disp"].items():
            if disp_name in self.disp2prop:
                self.disp_prop_write(disp_name, disp_val)

    def capture_format_changed(self, fmt):
        """
        The capture pixel format was changed (see uscope.imager.pixel_format)
        """
        pass

    """
    Exposure
    Plugins override the property names
    """

    def get_exposure_disp_property(self):
        return None

    def get_auto_exposure_disp_property(self):
        return None

    def auto_exposure_enabled(self):
        disp = self.get_auto_exposure_disp_property()
        if disp is None or disp not in self.disp2prop:
            return False
        return bool(self.disp_prop_read(disp))

    def auto_color_enabled(self):
        return False

    def set_exposure(self, n):
        self.set_disp_properties({self.get_exposure_disp_property(): n})

    def get_exposure(self):
        return self.disp_prop_read(self.get_exposure_disp_property())

    def get_meta_exposure_seconds(self, meta):
        """
        FIXME: adjust this for toupcamsrc and v4l2src
        I think both report in us though?
        """
        disp = self.get_exposure_disp_property()
        val = meta.get("disp_properties", {}).get(disp)
        if val is None:
            return None
        return val / 1e6

    def captured_image_exposure(self, captured_image):
        disp = self.get_exposure_disp_property()
        return captured_image.meta.get("disp_properties", {}).get(disp)

    def add_captured_image_meta(self, captured_image):
        # Used for saving image
        self.prepare_exif_bytes(captured_image)

    def prepare_exif_bytes(self, captured_image):
        """
        Thread safe
        Called from image processing thread
        """
        exposure_seconds = self.get_meta_exposure_seconds(captured_image.meta)
        if exposure_seconds is None:
            return
        exif = {}
        exp_tuple = (int(exposure_seconds * 1e6), int(1e6))
        exif["Exif"] = {
            # manual exposure
            34850: 1,
            # exposure time as rational
            33434: exp_tuple,
            # ISO
            34855: 1,
            # f number
            33437: (1, 1),
        }
        captured_image.set_exif_bytes(piexif.dump(exif))


class MockImagerProperties(ImagerProperties):
    def _raw_prop_write(self, name, value):
        pass

    def _raw_prop_read(self, name):
        return 0


def gst_value_to_python(val):
    """
    Plain (JSON serializable) value for a gstreamer property value
    Enums / flags => int (accepted back by set_property())
    """
    # Deferred: mock sessions don't need gstreamer
    from gi.repository import GObject
    if isinstance(val, (GObject.GEnum, GObject.GFlags)):
        return int(val)
    return val


class GstImagerProperties(ImagerProperties):
    """
    Properties on the gstreamer source element (ex: toupcamsrc)
    """
    def source(self):
        return self.session.vidpip.source

    def flatten_hack(self, prop):
        pass

    def template_property(self, prop_entry):
        prop_name = prop_entry["prop_name"]
        ret = {}
        ps = self.source().find_property(prop_name)
        if ps is None:
            raise ValueError(f"Property '{prop_name}' not found")
        ret["prop_name"] = prop_name
        ret["default"] = gst_value_to_python(ps.default_value)

        if prop_entry.get("fill_type", True):
            if ps.value_type.name == "gint":
                """
                Ex:
                "source_properties_mod": {
                    //In us. Can go up to 15 sec which is impractical for typical usage
                    "expotime": {
                        "max": 200000
                    },
                },
                """
                spm = self.microscope.usc.imager.source_properties_mod()
                pconfig = spm.get(prop_name, {}) if spm else {}
                ret["min"] = pconfig.get("min", ps.minimum)
                ret["max"] = pconfig.get("max", ps.maximum)
                ret["type"] = "int"
            elif ps.value_type.name == "gboolean":
                ret["type"] = "bool"
            elif hasattr(ps, "enum_class"):
                values = ps.enum_class.__enum_values__
                ret["min"] = min(values)
                ret["max"] = max(values)
                ret["type"] = "int"
            else:
                raise ValueError(
                    f"Property '{prop_name}': unsupported type {ps.value_type.name}"
                )

        ret.update(prop_entry)
        self.flatten_hack(ret)
        return ret

    def _raw_prop_write(self, name, val):
        self.source().set_property(name, val)

    def _raw_prop_read(self, name):
        return gst_value_to_python(self.source().get_property(name))


class V4L2ImagerProperties(ImagerProperties):
    """
    Acts on the v4l2src file descriptor directly via the v4l2 API
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_rw = None
        self.all_controls = set()

    def fd(self):
        return self.session.vidpip.source.get_property("device-fd")

    def setup(self):
        from uscope import v4l2_util
        self.control_rw = v4l2_util.get_control_rw(self.fd())
        self.all_controls = set(self.control_rw.ctrls())
        self.all_controls.update(set(self.virtual_properties.keys()))
        super().setup()

    def validate_prop_config(self, prop_config):
        if prop_config["prop_name"] in self.all_controls:
            return True
        if prop_config.get("optional", False):
            return False
        self.control_rw.dump_control_names()
        raise ValueError(f"Bad control name: {prop_config['prop_name']}")

    def _raw_prop_write(self, name, val):
        if self.control_rw is not None:
            self.control_rw.ctrl_set(name, val)

    def _raw_prop_read(self, name):
        if self.control_rw is None:
            return None
        return self.control_rw.ctrl_get(name)

    def get_auto_exposure_raw_name(self):
        for option in ("Exposure, Auto", "Auto Exposure"):
            if option in self.all_controls:
                return option
        raise ValueError("Failed to find auto exposure name")

    def get_auto_exposure_disp_property(self):
        return "Auto Exposure"

    def get_exposure_disp_property(self):
        return "Exposure"

    def auto_color_enabled(self):
        prop = "Auto White Balance Temperature"
        if prop not in self.disp2prop:
            return False
        return bool(self.disp_prop_read(prop))

    def disp_prop_was_rw(self, name, value):
        # Auto Exposure quickly fights with the user
        # Let the camera own exposure while its active
        if name == "Auto Exposure" and "Exposure" in self.disp2prop:
            self.set_gui_driven(not value, disp_names=["Exposure"])
        if name == "Auto White Balance Temperature" and "White Balance Temperature" in self.disp2prop:
            self.set_gui_driven(not value,
                                disp_names=["White Balance Temperature"])
