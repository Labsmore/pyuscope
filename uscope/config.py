import json5
import os
from collections import OrderedDict
from uscope.util import writej, readj
from pathlib import Path

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
"""
Calibration broken out into separate file to allow for easier/safer frequent updates
Ideally we'd also match on S/N or something like that
"""


def cal_fn(mkdir=False):
    if not config_dir:
        return None
    if mkdir and not os.path.exists(config_dir):
        os.mkdir(config_dir)
    return os.path.join(config_dir, "imager_calibration.j5")


def cal_load(source):
    fn = cal_fn()
    if fn is None or not os.path.exists(fn):
        return {}
    configj = readj(fn)
    configs = configj["configs"]
    for config in configs:
        if config["source"] == source:
            return config["properties"]
    return {}


def cal_load_all(source):
    fn = cal_fn()
    if not os.path.exists(fn):
        return
    configj = readj(fn)
    configs = configj["configs"]
    for config in configs:
        if config["source"] == source:
            return config
    return None


def cal_save(source, j):
    fn = cal_fn(mkdir=True)
    if not os.path.exists(fn):
        configj = {"configs": []}
    else:
        configj = readj(fn)

    configs = configj["configs"]

    jout = {"source": source, "properties": j}

    # Replace old config if exists
    for configi, config in enumerate(configs):
        if config["source"] == source:
            configs[configi] = jout
            break
    # Otherwise create new config
    else:
        configs.append(jout)

    print("Saving cal to %s" % fn)
    writej(fn, configj)


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
        """
        w = int(self.j['width'])
        h = int(self.j['height'])
        return w, h

    def cropped_wh(self):
        """
        The intended w/h after expected crop (if any) is applied
        Usually we use the full sensor but sometimes its cropped
        (ex: if too large a sensor is used)
        """
        w = int(self.j['width'])
        h = int(self.j['height'])

        if "crop" in usj["imager"]:
            crop = usj["imager"]["crop"]
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
        ret = self.j.get("crop", {})
        if not ret:
            return None
        for k in list(ret.keys()):
            if k not in ("top", "bottom", "left", "right"):
                raise ValueError("Unexpected key" % (k, ))
            ret[k] = int(ret.get(k, 0))
        return ret

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


class USCPlanner:
    def __init__(self, j=None):
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

    def tsettle(self):
        """
        How much time to wait after moving to take an image
        A factor of vibration + time to clear a frame
        """
        return float(self.j.get("tsettle", 0.0))

    def hdr_tsettle(self):
        """
        How much time to wait after moving to take an image
        A factor of vibration + time to clear a frame
        """
        return float(self.j.get("hdr_tsettle", self.tsettle()))


# Microscope usj config parser
class USC:
    def __init__(self, usj=None):
        if usj is None:
            usj = get_usj()
        self.usj = usj
        self.imager = USCImager(self.usj.get("imager"))
        self.motion = USCMotion(self.usj.get("motion"))
        self.planner = USCPlanner(self.usj.get("planner"))
        self.apps = {}

    def app_register(self, name, cls):
        """
        Register app name with class cls
        """
        j = self.usj.get("apps", {}).get(name, {})
        self.apps[name] = cls(j=j)

    def app(self, name):
        return self.apps[name]


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
    usc.planner.tsettle()
    usc.planner.hdr_tsettle()


def set_usj(j):
    global usj
    usj = j


def get_usj(config_dir=None, name=None):
    global usj

    if usj is not None:
        return usj

    if not config_dir:
        if not name:
            name = os.getenv("PYUSCOPE_MICROSCOPE")
        if name:
            config_dir = "configs/" + name
        # Maybe just throw an exception at this point?
        else:
            config_dir = "config"
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


"""
Planner configuration
"""


class PC:
    def __init__(self, j=None):
        self.j = j
        self.imager = PCImager(self.j.get("imager"))
        self.motion = PCMotion(self.j.get("motion", {}))
        self.apps = {}

    def tsettle(self):
        return self.j.get("tsettle", 0.0)

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

    def image_scalar(self):
        """Multiplier to go from Imager image size to output image size"""
        return float(self.j.get("imager", {}).get("scalar", 1.0))

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

def get_bcj():
    with open(os.path.join(Path.home(), ".pyuscope")) as f:
        j = json5.load(f, object_pairs_hook=OrderedDict)
    return j

bc = None
def get_bc(j=None):
    global bc

    if bc is None:
        if j is None:
            j = get_bcj()
        bc = BaseConfig(j=j)
    return bc
