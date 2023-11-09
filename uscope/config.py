import json
import json5
import os
from collections import OrderedDict
from uscope.util import writej, readj
from pathlib import Path
from uscope import jsond
'''
There is a config directory with two primary config files:
-microscope.j5: inherent config that doesn't really change
-imager_calibration.j5: for different modes (ex: w/ and w/o polarizer)



A few general assumptions:
-Camera is changed rarely.  Therefore only one camera per config file
-Objectives are changed reasonably often
    They cannot changed during a scan
    They can be changed in the GUI
'''
"""
defaults = {
    "out_dir": "out",
    "imager": {
        "hal": 'mock',
        "snapshot_dir": "snapshot",
        "width": 3264,
        "height": 2448,
        "scalar": 0.5,
    },
    "motion": {
        # Good for testing and makes usable to systems without CNC
        "hal": "mock",
        "startup_run": False,
        "startup_run_exit": False,
        "overwrite": False,
        "backlash": 0.0,
    }
}
"""

# microscope.j5
usj = None
usc = None
config_dir = None
default_microscope_name_cache = None
"""
Calibration broken out into separate file to allow for easier/safer frequent updates
Ideally we'd also match on S/N or something like that
"""


class SystemNotFound(Exception):
    pass


def has_default_microscope_name():
    return bool(default_microscope_name_cache)


def default_microscope_name(name=None):
    global default_microscope_name_cache

    if name:
        default_microscope_name_cache = name
        return name
    if default_microscope_name_cache:
        return default_microscope_name_cache
    name = os.getenv("PYUSCOPE_MICROSCOPE")
    if name:
        default_microscope_name_cache = name
        return name
    raise Exception("Must specify microscope")


def cal_fn_microscope(name=None):
    return os.path.join(get_config_dir(name=name), "imager_calibration.j5")


def get_microscope_data_dir(name=None, mkdir=True):
    name = default_microscope_name(name)
    microscopes_dir = os.path.join(get_data_dir(mkdir=mkdir), "microscopes")
    if mkdir and not os.path.exists(microscopes_dir):
        os.mkdir(microscopes_dir)
    microscope_dir = os.path.join(microscopes_dir, name)
    if mkdir and not os.path.exists(microscope_dir):
        os.mkdir(microscope_dir)
    return microscope_dir


def cal_fn_data(name=None, mkdir=True):
    microscope_dir = get_microscope_data_dir(name=name, mkdir=mkdir)
    return os.path.join(microscope_dir, "imager_calibration.j5")


def cal_load(source=None, name=None, load_data_dir=True):
    def load_config(fn):
        if not fn:
            return {}
        if not os.path.exists(fn):
            return {}
        configj = readj(fn)
        configs = configj["configs"]
        if type(configs) is list:
            raise ValueError(
                "Old style calibration, please update from list to dict")
        config = configs["default"]
        if source and config["source"] != source:
            raise ValueError("Source mismatches in config file")
        assert "properties" in config
        return config["properties"]

    # configs/ls-hvy-1/imager_calibration.j5
    microscopej = load_config(cal_fn_microscope(name=name))
    if not load_data_dir:
        return microscopej
    # Take defaults from dataj, the user directory
    # data/microscopes/ls-hvy-1/imager_calibration.j5
    dataj = load_config(cal_fn_data(name))
    for k, v in dataj.items():
        microscopej[k] = v
    return microscopej


def cal_save_to_data(source, properties, mkdir=False):
    if mkdir and not os.path.exists(get_data_dir()):
        os.mkdir(get_data_dir())
    jout = {
        "configs": {
            "default": {
                "source": source,
                "properties": properties
            }
        }
    }
    writej(cal_fn_data(), jout)


class USCImager:
    """
    Rough pipeline for typical Touptek camera:
    -Set esize + w/h to configure sensor size
    -Optionally set crop to reduce the incoming image size
    -scalar to reduce output width/height
    """

    valid_keys = {"source", "width", "height", "crop"}

    def __init__(self, j=None):
        """
        j: usj["imager"]
        """
        self.j = j
        #if not "width" in j or not "height" in j:
        #    raise ValueError("width/height required")

    def source(self):
        return self.j.get("source", "auto")

    def raw_wh(self):
        """
        The actual sensor size
        Following are not applied yet: crop, scaling
        """
        w = int(self.j['width'])
        h = int(self.j['height'])
        return w, h

    def cropped_wh(self):
        """
        The intended w/h after expected crop (if any) is applied
        Usually we use the full sensor but sometimes its cropped
        Scaling, if any, is not yet applied
        (ex: if too large a sensor is used)
        """
        w = int(self.j['width'])
        h = int(self.j['height'])

        crop = self.crop_tblr()
        if crop:
            w = w - crop["left"] - crop["right"]
            h = h - crop["top"] - crop["bottom"]

        if w <= 0 or h <= 0:
            raise ValueError("Bad cropped w/h")
        return w, h

    def crop_tblr(self):
        """
        Crop properties
        Intended for gstreamer "videocrop"
        top/bottom/left/right

        Returns either None or a dict with 4 keys
        """
        assert not "crop" in self.j, "Obsolete crop in config. Please update to crop_fractions"
        # Explicit config by pixels
        ret = {
            "top": 0,
            "bottom": 0,
            "left": 0,
            "right": 0,
        }
        tmp = self.j.get("crop_pixels", {})
        if tmp:
            for k in list(tmp.keys()):
                if k not in ("top", "bottom", "left", "right"):
                    raise ValueError("Unexpected key" % (k, ))
                ret[k] = int(tmp.get(k, 0))
            return ret
        # Convert config based on fraction of sensor size
        tmp = self.j.get("crop_fractions", {})
        if tmp:
            w, h = self.raw_wh()
            for k in list(ret.keys()):
                if k in ("top", "bottom"):
                    ret[k] = int(tmp.get(k, 0.0) * h)
                elif k in ("left", "right"):
                    ret[k] = int(tmp.get(k, 0.0) * w)
                else:
                    raise ValueError("Unexpected key" % (k, ))
            return ret
        return None

    def final_wh(self):
        """
        Final expected width and height in pixels
        Should be the same for snapshots and scans
        """
        crop = self.crop_tblr() or {}
        width, height = self.raw_wh()
        width -= crop.get("left", 0) + crop.get("right", 0)
        height -= crop.get("top", 0) + crop.get("bottom", 0)
        width *= self.scalar()
        height *= self.scalar()
        width = int(width)
        height = int(height)
        return width, height

    def source_properties(self):
        """
        A dict of configuration specific parameters to apply to the imager
        Usually these are gstreamer properties
        ex: use this to set hflip
        """
        return self.j.get("source_properties", {})

    def source_properties_mod(self):
        """
        A way to change ranges based on application specific environments
        ex: can limit exposure range to something you like better

        "source_properties_mod": {
            //In us. Can go up to 15 sec which is impractical for typical usage
            "expotime": {
                "max": 200000
            },
        },
        """
        return self.j.get("source_properties_mod", {})

    def scalar(self):
        """
        Scale image by given factor
        Ex: a 640x480 image (after crop) with scalar 0.5 will be output 320x240
        A return value of None is equivalent to 1.0
        """
        return float(self.j.get("scalar", 1.0))

    def save_extension(self):
        """
        Used by PIL to automagically save files

        Used by:
        -Planner output
        -Argus snapshot
        """
        return self.j.get("save_extension", ".jpg")

    def save_quality(self):
        """
        When .jpg output, determines the saved compression level

        Used by:
        -Planner output
        -Argus snapshot
        """
        return self.j.get("save_quality", 95)

    def ff_cal_fn(self):
        return get_microscope_data_dir() + "/imager_calibration_ff.tif"

    def has_ff_cal(self):
        return os.path.exists(self.ff_cal_fn())

    def videoflip_method(self):
        return self.j.get("videoflip_method", None)


class USCMotion:
    def __init__(self, j=None):
        """
        j: usj["motion"]
        """
        self.j = j
        # See set_axes() for more fine grained control
        # Usually this is a reasonable approximation
        # Iterate (list, dict, etc) to reserve for future metadata if needed
        self.axes_meta = OrderedDict([("x", {}), ("y", {}), ("z", {})])
        # hmm pconfig tries to overlay on USCMotion
        # not a good idea?
        # assert "hal" in self.j

    def format_position(self, axis, position, digit_spaces=True):
        """
        This was intended to be a simple way to display high precision numbers
        but is a bit of a mess

        Goals:
        -Allow displaying high precision measurements in an easy to to read way
        -Display rounded values to avoid precision floor() issues making inaccurate display
        """
        if self.j.get("z_format6") and axis == "z" or self.j.get(
                "xyz_format6"):
            if position >= 0:
                sign = "+"
            else:
                sign = "-"
            if digit_spaces:
                digit_space = " "
            else:
                digit_space = ""
            position = abs(position)
            whole = int(position)
            position3f = (position - whole) * 1000
            position3 = int(position3f)
            position6 = int(round((position3f - position3) * 1000))
            # Fixes when rounds up
            if position6 >= 1000:
                position6 -= 1000
                position3 += 1
                if position3 >= 1000:
                    position3 -= 1000
                    whole += 1
            return "%c%u.%03u%s%03u" % (sign, whole, position3, digit_space,
                                        position6)
        else:
            return "%0.3f" % position

    def format_positions(self, position):
        ret = ""
        for axis, this_pos in sorted(position.items()):
            if ret:
                ret += " "
            ret += "%c%s" % (axis.upper(),
                             self.format_position(
                                 axis, this_pos, digit_spaces=False))
        return ret

    def validate_axes_dict(self, axes):
        # FIXME
        pass

    def set_axes_meta(self, axes_meta):
        self.axes_meta = axes_meta

    def hal(self):
        """
        Which movement engine to use
        Sample values:
        grbl: use GRBL controller
        mock: use an emulatd controller

        Note: there is also a family of LinuxCNC (machinekit) HALs
        However they are not currently maintained / supported
        """
        ret = self.j["hal"]
        if ret not in ("mock", "grbl-ser", "lcnc-rpc", "lcnc-arpc", "lcnc-py"):
            raise ValueError("Invalid hal: %s" % (ret, ))
        return ret

    def raw_scalars(self):
        """
        WARNING: this is without system specific tweaks applied

        Scale each user command by given amount when driven to physical system
        Return a dictionary, one key per axis, of the possible axes
        Or None if should not be scaled

        Ex GRBL controller with:
        "scalars": {
            "x": 4.0,
            "y": 4.0,
            "z": 20.0,
        GUI move x relative 2 => move GRBL x by 8
        GUI move z relative 3 => move GRBL x by 60
        """
        ret = self.j.get("scalars", {})
        self.validate_axes_dict(ret)
        return ret

    def backlash(self):
        """
        Return a dictionary, one key per known axis, of the possible axes
        Backlash ("slop") defines the amount of motion needed in one axis to engage motion
        if previously going the other direction
        """
        default_backlash = 0.0
        backlash = self.j.get("backlash", {})
        ret = {}
        if backlash is None:
            pass
        elif type(backlash) in (float, int):
            default_backlash = float(backlash)
        elif type(backlash) in (dict, OrderedDict):
            for axis, v in backlash.items():
                ret[axis] = float(v)
        else:
            raise Exception("Invalid backlash: %s" % (backlash, ))

        # If axes are known validate and add defaults
        if self.axes_meta:
            # If axes were registered, set defaults
            for k in self.axes_meta:
                if not k in ret:
                    ret[k] = default_backlash

        self.validate_axes_dict(ret)
        return ret

    def backlash_compensation(self):
        """
        +1: move negative along axis then positive to final position
        0 => none
        -1: move positive along axis then negative to final position
        """

        backlashes = self.backlash()

        default_comp = None
        backlash = self.j.get("backlash_compensation", {})
        ret = {}
        if backlash is None:
            pass
        elif type(backlash) is int:
            default_comp = backlash
            assert default_comp in (-1, +1), default_comp
        elif type(backlash) in (dict, OrderedDict):
            for axis, v in backlash.items():
                ret[axis] = int(v)
        else:
            raise Exception("Invalid backlash compensation: %s" % (backlash, ))

        # If axes are known validate and add defaults
        if self.axes_meta:
            # If axes were registered, set defaults
            for k in self.axes_meta:
                if not k in ret:
                    # If there is backlash, assign default compensation
                    if backlashes.get(k):
                        ret[k] = default_comp if default_comp is not None else -1
                    else:
                        ret[k] = 0
                assert ret[k] in (-1, 0, +1), ret

        return ret

    def origin(self):
        """
        Where the coordinate system starts from
        Primarily used by planner and related

        CNC industry standard coordinate system is lower left
        However, image typical coordinate system is upper left
        There are also other advantages for some fixturing to have upper left
        As such support a few choices here
        """
        ret = self.j.get("origin", "ll")
        if ret not in ("ll", "ul"):
            raise ValueError("Invalid coordinate origin: %s" % (ret, ))
        return ret

    def soft_limits(self):
        """
        Do not allow travel beyond given values
        Return a dictionary, one key per axis, of the possible axes

        Useful if your system doesn't easily support soft or hard limits
        """
        raw = self.j.get("soft_limits", None)
        if raw is None:
            return None

        ret = {}
        for axis in self.axes_meta:
            axmin = raw.get(axis + "min")
            axmax = raw.get(axis + "max")
            if axmin is not None or axmax is not None:
                axmin = axmin if axmin else 0.0
                axmax = axmax if axmax else 0.0
                ret[axis] = (axmin, axmax)
        self.validate_axes_dict(ret)
        return ret

    def use_wcs_offsets(self):
        return bool(self.j.get("use_wcs_offsets", 0))

    def limit_switches(self):
        """
        Used to be extra careful to avoid homing systems without limit switches
        """

        v = self.j.get("limit_switches")
        if v is None:
            return None
        else:
            return bool(v)

    def axes(self):
        return set(self.j.get("axes", "xyz"))


class USCPlanner:
    def __init__(self, j={}):
        """
        j: usj["planner"]
        """
        self.j = j

    def overlap(self):
        """
        ideal faction of image that overlaps to each adjacent image
        Default: 0.3 => overlap adjacent image by 30% on each side (40% unique)
        """
        return float(self.j.get("overlap", 0.3))

    def border(self):
        """
        Automatically add this many mm to the edges of a panorama
        """
        return float(self.j.get("border", 0.0))


class USCKinematics:
    def __init__(self, j={}):
        """
        j: usj["kinematics"]
        """
        self.j = j

    """
    Full motion delay: NA / tsettle_motion_na1() + tsettle_motion()

    Ex:
    tsettle_motion = 0.1
    tsettle_motion_na1 = 0.8
    NA = 0.50
    min delay: 0.50 / 0.8 + 0.1 = 0.725 sec
    """

    def tsettle_autofocus(self):
        return float(self.j.get("tsettle_autofocus", 0.1))

    def tsettle_motion_base(self):
        """
        How much *minimum* time to wait after moving to take an image
        This is a base constant added to the NA scalar below
        A factor of vibration + time to clear a frame
        """
        # Set a semi-reasonable default
        return float(self.j.get("tsettle_motion_base", 0.25))

    def tsettle_motion_na1(self):
        """
        How much delay added to tsettle_motion for a 1.0 numerical aperture objective
        Ex: a 0.50 NA objective will wait half as long
        """
        # Set a semi-reasonable default
        return float(self.j.get("tsettle_motion_na1", 0.5))

    def tsettle_motion_max(self):
        NA_MAX = 1.4
        return self.tsettle_motion_base() + self.tsettle_motion_na1() * NA_MAX

    def tsettle_hdr(self):
        """
        How much time to wait after moving to take an image
        A factor of vibration + time to clear a frame
        """
        # Set a semi-reasonable default
        return float(self.j.get("tsettle_hdr", 0.2))


class USCOptics:
    def __init__(self, j=None):
        self.j = j

    def um_per_pixel_raw_1x(self):
        return self.j.get("um_per_pixel_raw_1x", None)

    def diffusion(self):
        """
        "diffusion": {
            "red": 2.0,
            "green": 2.0,
            "blue": 4.0,
        }
        """
        return self.j.get("optics", None)


class USCImageProcessingPipeline:
    def __init__(self, j={}):
        self.j = j

    def pipeline_first(self):
        return self.j.get("pipeline_first", [])

    def pipeline_last(self):
        """
        Get image processing pipeline configuration
        "ipp": [
            {"plugin": "correct-sharp1"},
            {"plugin": "correct-vm1v1", "config": {"kernel_width": 3}},
        ],
        """
        return self.j.get("pipeline_last", [])

    # plugin specific options
    def get_plugin(self, name):
        return self.j.get("plugins", {}).get(name, {})


class ObjectiveDB:
    def __init__(self, fn=None, strict=None):
        if fn is None:
            fn = os.path.join(get_configs_dir(), "objectives.j5")
        with open(fn) as f:
            self.j = json5.load(f, object_pairs_hook=OrderedDict)
        # Index by (vendor, model)
        self.db = OrderedDict()
        for entry in self.j["objectives"]:
            assert entry["vendor"]
            assert entry["model"]
            # na, magnification is highly encouraged but not required?
            k = (entry["vendor"].upper(), entry["model"].upper())
            self.db[k] = entry
        # hack in case needed to bypass short term
        if strict is None:
            strict = os.getenv("PYUSCOPE_STRICT_OBJECTIVEDB", "Y") == "Y"
        self.strict = strict

    def get(self, vendor, model):
        return self.db[(vendor.upper(), model.upper())]

    def set_defaults(self, objectivejs):
        for objectivej in objectivejs:
            self.set_default(objectivej)

    def set_default(self, objectivej):
        """
        if vendor/model is found in db fill in default values from db
        """

        # experimental shorthand
        # vendor, model is required to match
        # other fields can be specified to make readable
        # however they are optional and just checked for consistency
        if "db_find" not in objectivej:
            return
        """
        "db_find": "vendor: Mitutoyo, model: 46-145, magnification: 20, na: 0.28",
        """
        fields = {}
        for entry in objectivej["db_find"].split(","):
            try:
                parts = entry.split(":")
                if len(parts) != 2:
                    raise Exception(
                        f"Fields must have key:value pairs: {entry}")
                k, v = parts
                k = k.strip()
                v = v.strip()
                if k == "magnification":
                    v = int(v)
                if k == "na":
                    v = float(v)
                fields[k] = v
            except:
                print(f"Failed to parse field: {entry}")
                raise
        # Required
        vendor = fields["vendor"]
        model = fields["model"]
        db_entry = self.db.get((vendor.upper(), model.upper()))
        if not db_entry:
            raise ValueError(f"Objective {vendor} {model} not found in db")
        # Validate consistency on optional keys
        for k, v in fields.items():
            db_has = db_entry[k]
            if db_has != v:
                raise ValueError(
                    f"db_find {vendor} {model}: config has {v} but db has {db_has}"
                )
        for k, v in db_entry.items():
            # Anything user has already set
            if k not in objectivej:
                objectivej[k] = v


# Microscope usj config parser
class USC:
    def __init__(self, usj=None):
        if usj is None:
            usj = get_usj()
        self.usj = usj
        self.imager = USCImager(self.usj.get("imager"))
        self.motion = USCMotion(self.usj.get("motion"))
        self.planner = USCPlanner(self.usj.get("planner", {}))
        self.kinematics = USCKinematics(self.usj.get("kinematics", {}))
        self.optics = USCOptics(self.usj.get("optics", {}))
        self.ipp = USCImageProcessingPipeline(self.usj.get("ipp", {}))
        self.apps = {}
        self.bc = get_bc()

    def app_register(self, name, cls):
        """
        Register app name with class cls
        """
        j = self.usj.get("apps", {}).get(name, {})
        self.apps[name] = cls(j=j)

    def app(self, name):
        return self.apps[name]

    def find_system(self, microscope=None):
        """
        Look for system specific configuration by matching camera S/N
        In the future we might use other info
        Expect file to have a default entry with null key or might consie
        """
        if microscope and microscope.imager:
            camera_sn = microscope.imager.get_sn()
        else:
            camera_sn = None
        # Provide at least a very basic baseline
        default_system = {
            "objectives_db": [
                "vendor: Mock, model: 5X",
                "vendor: Mock, model: 10X",
                "vendor: Mock, model: 20X",
            ],
        }
        for system in self.usj.get("systems", []):
            if system["camera_sn"] == camera_sn:
                return system
            if not system["camera_sn"]:
                default_system = system
        return default_system
        """
        raise SystemNotFound(
            f"failed to either match system or find default for camera S/N {camera_sn}"
        )
        """

    def get_uncalibrated_objectives(self, microscope=None):
        """
        Get baseline objective configuration without system specific scaling applied
        """
        system = self.find_system(microscope)
        # Shorthand notation?

        ret = []
        # XXX: order between these?
        # Generally if you have both the DB are the normal and objectives is the custom
        # So put the custom at the end
        if "objectives_db" in system:
            for entry in system["objectives_db"]:
                ret.append({"db_find": entry})
        if "objectives" in system:
            for objective in system["objectives"]:
                ret.append(objective)
        if len(ret) == 0:
            raise ValueError(
                "Found system but missing objective configuration")
        return ret

    def get_motion_scalars(self, microscope):
        """
        Get scalars after applying system level tweaks
        Ex: model trim w/ a higher ratio gearbox
        """
        scalars = dict(self.motion.raw_scalars())
        system = self.find_system(microscope)
        scalars_scalar = system.get("motion", {}).get("scalars_scalar", {})
        for k, v in scalars_scalar.items():
            scalars[k] = scalars[k] * v
        return scalars


def validate_usj(usj):
    """
    Load all config parameters and ensure they appear to be valid

    strict
        True:
            Error on any keys not matching a valid directive
            If possible, error on duplicate keys
        False
            Allow keys like "!motion" to effectively comment a section out
    XXX: is there a generic JSON schema system?
    """
    # Good approximation for now
    axes = "xyz"
    usc = USC(usj=usj)

    # Imager
    usc.imager.source()
    usc.imager.raw_wh()
    usc.imager.cropped_wh()
    usc.imager.crop_tblr()
    usc.imager.source_properties()
    usc.imager.source_properties_mod()
    usc.imager.scalar()
    usc.imager.save_extension()
    usc.imager.save_quality()

    # Motion
    usc.motion.set_axes_meta(axes)
    # In case a plugin is registered validate here?
    usc.motion.hal()
    usc.motion.scalars()
    usc.motion.backlash()
    usc.motion.backlash_compensation()
    usc.motion.origin()
    usc.motion.soft_limits()

    # Planner
    usc.planner.step()
    usc.planner.border()
    usc.planner.tsettle_motion()
    usc.planner.tsettle_hdr()


def set_usj(j):
    global usj
    usj = j


def get_data_dir(mkdir=True):
    ret = os.getenv("PYUSCOPE_DATA_DIR", "data")
    if not os.path.exists(ret) and mkdir:
        os.mkdir(ret)
    return ret


def get_scan_dir(mkdir=True):
    ret = os.path.join(get_data_dir(mkdir=mkdir), "scan")
    if not os.path.exists(ret) and mkdir:
        os.mkdir(ret)
    return ret


def get_snapshot_dir(mkdir=True):
    ret = os.path.join(get_data_dir(mkdir=mkdir), "snapshot")
    if not os.path.exists(ret) and mkdir:
        os.mkdir(ret)
    return ret


def get_microscopes_dir(mkdir=True):
    ret = os.path.join(get_data_dir(mkdir=mkdir), "microscopes")
    if not os.path.exists(ret) and mkdir:
        os.mkdir(ret)
    return ret


def init_data_dir(microscope_name):
    microscope_name = default_microscope_name(microscope_name)
    data_dir = get_data_dir()

    if not os.path.exists(data_dir):
        os.mkdir(data_dir)
    scan_dir = os.path.join(data_dir, "scan")
    if not os.path.exists(scan_dir):
        os.mkdir(scan_dir)
    snapshot_dir = os.path.join(data_dir, "snapshot")
    if not os.path.exists(snapshot_dir):
        os.mkdir(snapshot_dir)
    microscopes_dir = os.path.join(data_dir, "microscopes")
    if not os.path.exists(microscopes_dir):
        os.mkdir(microscopes_dir)
    microscope_name_dir = os.path.join(microscopes_dir, microscope_name)
    if not os.path.exists(microscope_name_dir):
        os.mkdir(microscope_name_dir)


def get_configs_dir():
    # Assume for now its next to package
    return os.path.realpath(
        os.path.dirname(os.path.realpath(__file__)) + "/../configs")


def get_config_dir(name=None):
    global config_dir

    if config_dir:
        return config_dir
    microscope_name = default_microscope_name(name)

    config_dir = os.path.join(get_configs_dir(), microscope_name)
    return config_dir


def get_usj(config_dir=None, name=None):
    global usj

    if usj is not None:
        return usj
    if not config_dir:
        config_dir = get_config_dir(name=name)
    globals()["config_dir"] = config_dir

    # XXX: it would be possible to do an out of tree microscope config now using dconfig
    # might be useful for testing?

    # Check if user has patches
    dconfig = None
    bc_system = get_bc().get_system(microscope_name=name)
    if bc_system:
        dconfig = bc_system.get("dconfig", None)

    fn = os.path.join(config_dir, "microscope.j5")
    if not os.path.exists(fn):
        fn = os.path.join(config_dir, "microscope.json")
    if not os.path.exists(fn):
        if dconfig is not None:
            print(
                f"WARNING: failed to find microscope {name} but found dconfig. Out of tree configuration?"
            )
            j = {}
        else:
            raise Exception("couldn't find microscope.j5 in %s" % config_dir)
    else:
        with open(fn) as f:
            j = json5.load(f, object_pairs_hook=OrderedDict)

    if dconfig is not None:
        jsond.apply_update(j, dconfig)

    usj = j

    if name:
        init_data_dir(name)

    return usj


def get_usc(usj=None, config_dir=None, name=None):
    global usc

    if usc is None:
        if usj is None:
            usj = get_usj(config_dir=config_dir, name=name)
        usc = USC(usj=usj)
    return usc


"""
Planner configuration
"""


class PCImager:
    def __init__(self, j=None):
        self.j = j

        # self.save_extension = USCImager.save_extension
        # self.save_quality = USCImager.save_quality

    def save_extension(self, *args, **kwargs):
        return USCImager.save_extension(self, *args, **kwargs)

    def save_quality(self, *args, **kwargs):
        return USCImager.save_quality(self, *args, **kwargs)


class PCMotion:
    def __init__(self, j=None):
        self.j = j
        self.axes_meta = OrderedDict([("x", {}), ("y", {}), ("z", {})])

        # self.backlash = USCMotion.backlash
        # self.backlash_compensation = USCMotion.backlash_compensation
        # self.set_axes_meta = USCMotion.set_axes_meta

    def backlash(self, *args, **kwargs):
        return USCMotion.backlash(self, *args, **kwargs)

    def backlash_compensation(self, *args, **kwargs):
        return USCMotion.backlash_compensation(self, *args, **kwargs)

    def set_axes_meta(self, *args, **kwargs):
        return USCMotion.set_axes_meta(self, *args, **kwargs)

    def validate_axes_dict(self, *args, **kwargs):
        return USCMotion.validate_axes_dict(self, *args, **kwargs)


class PCKinematics:
    def __init__(self, j=None):
        self.j = j

    def tsettle_motion(self):
        return self.j.get("tsettle_motion", 0.0)

    def tsettle_hdr(self):
        return self.j.get("tsettle_hdr", 0.0)


"""
Planner configuration
"""


class PC:
    def __init__(self, j=None):
        self.j = j
        self.imager = PCImager(self.j.get("imager"))
        self.motion = PCMotion(self.j.get("motion", {}))
        self.kinematics = PCKinematics(self.j.get("kinematics", {}))
        self.apps = {}

    def exclude(self):
        return self.j.get('exclude', [])

    def end_at(self):
        return self.j.get("end_at", "start")

    def contour(self):
        return self.j["points-xy2p"]["contour"]

    def ideal_overlap(self, axis=None):
        # FIXME: axis option
        return self.j.get("overlap", 0.3)

    def border(self):
        """
        How much to add onto each side of the XY scan
        Convenience parameter to give a systematic fudge factor
        """
        return float(self.j.get("border", 0.0))

    def image_raw_wh_hint(self):
        return self.j.get("imager", {}).get("raw_wh_hint", None)

    def image_final_wh_hint(self):
        return self.j.get("imager", {}).get("final_wh_hint", None)

    def image_crop_tblr_hint(self):
        """
        Only used for loggin
        """
        return self.j.get("imager", {}).get("crop_tblr_hint", {})

    def image_scalar_hint(self):
        """
        Multiplier to go from Imager image size to output image size
        Only used for logging: the Imager itself is responsible for actual scaling
        """
        return float(self.j.get("imager", {}).get("scalar_hint", 1.0))

    def motion_origin(self):
        ret = self.j.get("motion", {}).get("origin", "ll")
        assert ret in ("ll", "ul"), "Invalid coordinate origin"
        return ret

    def x_view(self):
        return float(self.j["imager"]["x_view"])


def validate_pconfig(pj, strict=False):
    pass


class GUI(object):
    assets_dir = os.path.join(os.getcwd(), 'uscope', 'gui', 'assets')
    stylesheet_file = os.path.join(assets_dir, 'main.qss')
    icon_files = {}
    icon_files['gamepad'] = os.path.join(
        assets_dir, 'videogame_asset_FILL0_wght700_GRAD0_opsz48.png')
    icon_files['jog'] = os.path.join(
        assets_dir, 'directions_run_FILL1_wght700_GRAD0_opsz48.png')
    icon_files['NE'] = os.path.join(
        assets_dir, 'north_east_FILL1_wght700_GRAD0_opsz48.png')
    icon_files['SW'] = os.path.join(
        assets_dir, 'south_west_FILL1_wght700_GRAD0_opsz48.png')
    icon_files['NW'] = os.path.join(
        assets_dir, 'north_west_FILL0_wght700_GRAD0_opsz48.png')
    icon_files['SE'] = os.path.join(
        assets_dir, 'south_east_FILL0_wght700_GRAD0_opsz48.png')
    icon_files['camera'] = os.path.join(
        assets_dir, 'photo_camera_FILL1_wght400_GRAD0_opsz48.png')
    icon_files['go'] = os.path.join(
        assets_dir, 'smart_display_FILL1_wght400_GRAD0_opsz48.png')
    icon_files['stop'] = os.path.join(
        assets_dir, 'stop_circle_FILL1_wght400_GRAD0_opsz48.png')
    icon_files['logo'] = os.path.join(assets_dir, 'logo.png')


class BaseConfig:
    def __init__(self, j=None):
        self.j = j
        self.objective_db = ObjectiveDB()
        # self.joystick = JoystickConfig(jbc=self.j.get("joystick", {}))

    def labsmore_stitch_use_xyfstitch(self):
        """
        xyfstitch is the newer higher fidelity stitch engine
        It does more aggressive analysis to eliminate stitch errors
        and uses a very different algorithm to stitch vs stock
        """
        return bool(self.j.get("labsmore_stitch", {}).get("use_xyfstitch"))

    def labsmore_stitch_aws_access_key(self):
        return self.j.get("labsmore_stitch", {}).get("aws_access_key")

    def labsmore_stitch_aws_secret_key(self):
        return self.j.get("labsmore_stitch", {}).get("aws_secret_key")

    def labsmore_stitch_aws_id_key(self):
        return self.j.get("labsmore_stitch", {}).get("aws_id_key")

    def labsmore_stitch_notification_email(self):
        return self.j.get("labsmore_stitch", {}).get("notification_email")

    def argus_stitch_cli(self):
        """
        Call given program with the scan output directory as the argument
        """
        return self.j.get("argus_stitch_cli", None)

    def dev_mode(self):
        """
        Display unsightly extra information
        """
        return self.j.get("dev_mode", False)

    def script_dirs(self):
        """
        The path to the secondary script dir
        Allows quick access to pyuscope-kitchen, pyuscope-rhodium, your plugins
        """
        return self.j.get("script_dirs", {})

    def get_system(self, microscope_name):
        systems = self.j.get("systems", [])
        for system in systems:
            if system["microscope"] == microscope_name:
                return system
        return None

    def get_joystick(self, guid):
        systems = self.j.get("joysticks", [])
        for system in systems:
            if system["guid"] == guid:
                return system
        return None


def get_bcj():
    try:
        with open(os.path.join(Path.home(), ".pyuscope")) as f:
            j = json5.load(f, object_pairs_hook=OrderedDict)
        return j
    except FileNotFoundError:
        return {}


bc = None


def get_bc(j=None):
    global bc

    if bc is None:
        if j is None:
            j = get_bcj()
        bc = BaseConfig(j=j)
    return bc


def lazy_load_microscope_from_config(directory):
    """
    If user arguments haven't already set, set the default microscope from uscan.json
    Intended for CLI processing applications
    """
    if not has_default_microscope_name():
        scan_fn = os.path.join(directory, "uscan.json")
        if os.path.exists(scan_fn):
            with open(scan_fn) as f:
                scanj = json.load(f)
            microscope_name = scanj["pconfig"]["app"]["microscope"]
            print(f"Scan taken with microscope {microscope_name}")
            default_microscope_name(microscope_name)
