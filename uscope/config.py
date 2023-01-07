import json5
import os
from collections import OrderedDict
from uscope.util import writej, readj
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
    def __init__(self, j=None):
        """
        j: usj["imager"]
        """
        self.j = j
        if not "width" in j or not "height" in j:
            raise ValueError("width/height required")

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


class USCPlanner:
    def __init__(self, j=None):
        """
        j: usj["planner"]
        """
        self.j = j

    def step(self):
        """
        ideal faction of image to move between images
        Default: 0.7 => only overlap adjacent image by 30%
        """
        return float(self.j.get("overlap", 0.7))

    def border(self):
        """
        Automatically add this many mm to the edges of a panorama
        """
        return float(self.j.get("border", 0.0))


class USCMotion:
    def __init__(self, j=None):
        """
        j: usj["motion"]
        """
        self.j = j
        # See set_axes() for more fine grained control
        # Usually this is a reasonable approximation
        # Dict to reserve for future metadata if needed
        self.axes_meta = OrderedDict([("x", {}), ("y", {}), ("z", {})])

    def set_axes_meta(self, axes_meta):
        """
        Strictly speaking the axes are controlled by the Motion interface
        However you can tune them here if needed
        """
        self.axes_meta = OrderedDict(axes_meta)

    def backlash(self):
        """
        Return a dictionary, one key per axis, of the possible axes
        Backlash ("slop") defines the amount of motion needed in one axis to engage motion
        if previously going the other direction
        """
        default_backlash = 0.0
        backlash = self.j.get("backlash", None)
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

        for k in self.axes_meta:
            if not k in ret:
                ret[k] = default_backlash

        for k in ret:
            if k not in self.axes_meta:
                raise ValueError("Unexpected axis %s in motion config" % (k, ))

        return ret


# Microscope usj config parser
class USC:
    def __init__(self, usj=None):
        if usj is None:
            usj = get_usj()
        self.usj = usj
        self.planner = USCPlanner(self.usj.get("planner"))
        self.motion = USCMotion(self.usj.get("motion"))
        self.imager = USCImager(self.usj.get("imager"))
        self.apps = {}

    def app_register(self, name, cls):
        """
        Register app name with class cls
        """
        j = self.usj.get("apps", {}).get(name, {})
        self.apps[name] = cls(j=j)

    def app(self, name):
        return self.apps[name]


def get_usc(usj=None):
    global usc

    if usc is None:
        usc = USC(usj=usj)
    return usc
