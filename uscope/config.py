import json5
import os
from collections import OrderedDict
from uscope.util import writej, readj
from pathlib import Path
import copy
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
defaults = {}

# microscope.j5
usj = None
usc = None
config_dir = None
default_microscope_name_cache = None
"""
Calibration broken out into separate file to allow for easier/safer frequent updates
Ideally we'd also match on S/N or something like that
"""


def default_microscope_name(name):
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


def cal_fn_data(name=None, mkdir=True):
    name = default_microscope_name(name)
    microscopes_dir = os.path.join(get_data_dir(mkdir=mkdir), "microscopes")
    if mkdir and not os.path.exists(microscopes_dir):
        os.mkdir(microscopes_dir)
    microscope_dir = os.path.join(microscopes_dir, name)
    if mkdir and not os.path.exists(microscope_dir):
        os.mkdir(microscope_dir)
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
        ret = os.getenv("PYUSCOPE_SAVE_EXTENSION")
        if ret:
            return ret
        return self.j.get("save_extension", ".jpg")

    def save_quality(self):
        """
        When .jpg output, determines the saved compression level

        Used by:
        -Planner output
        -Argus snapshot
        """
        return self.j.get("save_quality", 95)


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

    def scalars(self):
        """
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

        default_backlash = 0
        backlash = self.j.get("backlash_compensation", {})
        ret = {}
        if backlash is None:
            pass
        elif type(backlash) is int:
            default_backlash = backlash
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
                    ret[k] = default_backlash

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

    def tsettle_motion(self):
        """
        How much time to wait after moving to take an image
        A factor of vibration + time to clear a frame
        """
        return float(self.j.get("tsettle_motion", 0.0))

    def tsettle_hdr(self):
        """
        How much time to wait after moving to take an image
        A factor of vibration + time to clear a frame
        """
        return float(self.j.get("tsettle_hdr", 0.0))


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
        self.apps = {}

    def app_register(self, name, cls):
        """
        Register app name with class cls
        """
        j = self.usj.get("apps", {}).get(name, {})
        self.apps[name] = cls(j=j)

    def app(self, name):
        return self.apps[name]

    def get_scaled_objective(self, index):
        """
        Return objective w/ automatic sizing (if applicable) applied
        """
        return self.get_scaled_objectives()[index]

    def get_scaled_objectives(self):
        """
        Return objectives w/ automatic sizing (if applicable) applied

        returns:
        x_view: in final scaled image how many mm wide
        um_per_pixel: in final scaled image how many micrometers each pixel represents
        magnification: optional
        na: optional
        """
        objectives = copy.deepcopy(self.usj['objectives'])

        # Assume easiest to measure at lowest mag
        reference_objective = objectives[0]
        # In raw sensor pixels before scaling
        # That way can adjust scaling w/o adjusting
        # This is the now preferred way to set configuration
        reference_raw_um_per_pix = reference_objective.get("um_per_pixel_raw")
        reference_final_um_per_pix = reference_objective.get("um_per_pixel")
        # raw_w, _raw_h = self.imager.raw_wh()
        final_w, _final_h = self.imager.final_wh()

        def scale_reference(ref_um_per_pix, ref_w_pix, scalar):
            # Objectives must support magnification to scale
            for objective in objectives:
                mag_scalar = reference_objective["magnification"] / objective[
                    "magnification"]
                objective[
                    "um_per_pixel"] = ref_um_per_pix * mag_scalar * scalar
                # um to mm
                objective[
                    "x_view"] = ref_w_pix * objective["um_per_pixel"] / 1000

        if reference_raw_um_per_pix:
            """
            image scalar 0.5 => multiply effective pixel um by 2
            """
            scale_reference(reference_raw_um_per_pix, final_w, 1.0)
        elif reference_final_um_per_pix:
            scale_reference(reference_final_um_per_pix, final_w,
                            1 / usc.imager.scalar())
        # x_view is in the actual observed field of view after cropping
        # Doesn't care about scaling
        # x_view should be specified for each then
        # Calculate um_per_pix
        else:
            final_w, _final_h = self.imager.final_wh()
            for objective in objectives:
                objective[
                    "um_per_pixel"] = objective["x_view"] * 1000 / final_w

        # Sanity check
        for objective in objectives:
            assert "x_view" in objective

        # tsettle is referenced at the highest mag objective / worst case
        reference_objective = objectives[-1]
        # Amount of time to settle after moving
        # Scale per NA. ie a lower resolution objective requires proportionately less settling time
        reference_tsettle_motion = reference_objective.get("tsettle_motion")
        reference_na = reference_objective.get("na")
        # Objectives must support magnification to scale
        for objective in objectives:
            # Ex: 2.0 sec sleep at 100x 1.0 NA => 20x 0.42 NA => 0.84 sec sleep
            if reference_tsettle_motion and reference_na and "na" in objective:
                tsettle_motion = reference_tsettle_motion * objective[
                    "na"] / reference_na
            else:
                tsettle_motion = 0.0
            objective["tsettle_motion"] = tsettle_motion
        """
        if 0:
            import json
            print("objectives")
            print(json.dumps(objectives, sort_keys=True, indent=4, separators=(",", ": ")))
        """
        return objectives


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
    return "configs/"


def get_config_dir(name=None):
    global config_dir

    if config_dir:
        return config_dir
    microscope_name = default_microscope_name(name)

    config_dir = get_configs_dir() + microscope_name
    return config_dir


def get_usj(config_dir=None, name=None):
    global usj

    if usj is not None:
        return usj
    if not config_dir:
        config_dir = get_config_dir(name=name)
    globals()["config_dir"] = config_dir

    fn = os.path.join(config_dir, "microscope.j5")
    if not os.path.exists(fn):
        fn = os.path.join(config_dir, "microscope.json")
        if not os.path.exists(fn):
            raise Exception("couldn't find microscope.j5 in %s" % config_dir)
    with open(fn) as f:
        j = json5.load(f, object_pairs_hook=OrderedDict)

    def default(rootj, rootd):
        for k, v in rootd.items():
            if not k in rootj:
                rootj[k] = v
            elif type(v) is dict:
                default(rootj[k], v)

    default(j, defaults)
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


"""
Configuration more related to machine / user than a specific microscope
"""


class BaseConfig:
    def __init__(self, j=None):
        self.j = j

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

    def argus_joystick_cfg(self):
        """
        Get joystick config, or return default base config.

        When specifying configuration for joysticks in the config file,
        we expect it to be in this format:

        { "joystick": {
          "scan_secs": (float) in seconds, to query/run the joystick actions.
          "fn_map": {
            "func_from_joystick_file": dict(keyword args for the function),
          }
        }

        The function names specified in "fn_map" are the functions to be
        mapped/triggered, and correspond to a function exposed within
        uscope.motion.joystick. The value for the function name is
        a dictionary of key:vals corresponding to the required arguments
        for the chosen function.

        See the docs in joystick file for details on available functions.
        """
        default_joystick_cfg = {
           'scan_secs': 0.35,
           'fn_map': {
               'axis_set_jog_slider_value': {'id': 3},
               'btn_capture_image': {'id': 0},
               'axis_move_x': {'id': 0},
               'axis_move_y': {'id': 1},
               'hat_move_z': {'id': 0, 'idx': 1}
           },
        }
        return self.j.get("joystick", default_joystick_cfg)

    def dev_mode(self):
        """
        Display unsightly extra information
        """
        return self.j.get("dev_mode", False)


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
