"""
New Microscope Wizard (./app/microforge.py) config generation

Qt free so it can be unit tested
params: flat dict, see default_params()
build_config(params) => usj (as json5.load() of microscope.j5 would give)
render_j5(params) => microscope.j5 text with comments

Most values are guesses intended to get a config that loads and runs
The calibration wizard (Argus => Advanced options => Calibration) measures
the real values and stores them in the calibration store (uscope.cal.store),
which takes priority over the nominal microscope.j5 values
"""

from collections import OrderedDict
import copy
import datetime
import json
import os
import re

CAL_GUESS = "Guess: run calibration wizard (Argus => Advanced options => Calibration)"

# Per imager source defaults
# width / height: None => detect from camera (gst-toupcamsrc only)
SOURCES = OrderedDict([
    ("gst-toupcamsrc", {
        "label": "ToupTek USB camera (most common)",
        "width": None,
        "height": None,
        "v4l2": False,
    }),
    ("gst-v4l2src", {
        "label": "Generic USB (UVC / V4L2) camera",
        "width": 1920,
        "height": 1080,
        "v4l2": True,
    }),
    ("gst-v4l2src-hy800b", {
        "label": "Hayear HY-800B (V4L2)",
        # Full resolution is < 1 fps uncompressed, see configs/lip-vm1
        "width": 1280,
        "height": 960,
        "native_width": 3840,
        "native_height": 2160,
        "native_pixel_pitch_um": 2.0,
        "v4l2_name": "Hayear Camera: HY-800B",
        "v4l2": True,
    }),
    ("gst-v4l2src-mu800", {
        "label": "AmScope MU800 (V4L2)",
        "width": 3264,
        "height": 2448,
        "v4l2": True,
    }),
    ("gst-v4l2src-yw500", {
        "label": "YW500 (V4L2)",
        # Max uncompressed resolution
        "width": 800,
        "height": 600,
        "v4l2": True,
    }),
    ("gst-v4l2src-yw500u3m", {
        "label": "YW500U3M (V4L2)",
        "width": 2592,
        "height": 1944,
        "v4l2": True,
    }),
    ("gst-libcamerasrc", {
        "label": "libcamera (ex: Raspberry Pi camera via GStreamer)",
        "width": 1920,
        "height": 1080,
        "v4l2": False,
    }),
    ("picam2src", {
        "label": "Raspberry Pi camera (picamera2)",
        # HQ camera 2x2 binned
        "width": 2028,
        "height": 1520,
        "v4l2": False,
    }),
    ("gst-videotestsrc", {
        "label": "Test pattern (no camera hardware)",
        "width": 800,
        "height": 750,
        "v4l2": False,
    }),
])

HALS = OrderedDict([
    ("grbl-ser", "GRBL controller over USB serial (most common)"),
    ("mock", "Emulated motion (no motion hardware)"),
])

DEFAULT_OBJECTIVES_DB = [
    "vendor: Mitutoyo, model: 46-143",
    "vendor: Mitutoyo, model: 46-144",
    "vendor: Mitutoyo, model: 46-145",
]

DEFAULT_SOFT_LIMITS = OrderedDict([
    ("xmin", 0.0),
    ("xmax", 100.0),
    ("ymin", 0.0),
    ("ymax", 100.0),
    ("zmin", -25.0),
    ("zmax", 0.0),
])

# USCKinematics defaults
KINEMATICS_DEFAULT = OrderedDict([
    ("tsettle_motion_base", 0.25),
    ("tsettle_motion_na1", 0.5),
    ("tsettle_hdr", 0.2),
    ("tsettle_autofocus", 0.1),
])
# Wobbly / lightweight stages
KINEMATICS_CONSERVATIVE = OrderedDict([
    ("tsettle_motion_base", 0.5),
    ("tsettle_motion_na1", 1.0),
    ("tsettle_hdr", 0.5),
    ("tsettle_autofocus", 0.3),
])

# No pixel pitch => in the ballpark of a typical value (as mock configs)
UM_PER_PIXEL_DEFAULT = 5.0
TUBE_LENGTH_DEFAULT = 200
LARGE_SENSOR_PIXELS = 10e6


def scalar_guess(width, height):
    """Scale down large sensors to keep the GUI / processing responsive"""
    if width and height and width * height > LARGE_SENSOR_PIXELS:
        return 0.5
    return 1.0


def um_per_pixel_guess(pitch_um,
                       relay_magnification=1.0,
                       native_width=None,
                       width=None):
    """
    Sensor pixel pitch projected through the relay lens
    Capturing below native resolution makes each pixel proportionally larger
    """
    if not pitch_um or not relay_magnification:
        return UM_PER_PIXEL_DEFAULT
    ret = pitch_um / relay_magnification
    if native_width and width:
        ret *= float(native_width) / float(width)
    return round(ret, 6)


def um_per_pixel_guess_params(params):
    return um_per_pixel_guess(params.get("native_pixel_pitch_um"),
                              params.get("relay_magnification"),
                              params.get("native_width"),
                              params.get("width"))


def validate_name(name):
    """Return an error string or None if ok"""
    if not name:
        return "Name is required"
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", name):
        return "Name may only contain letters, digits, '.', '_' and '-' (and must start with a letter or digit)"
    return None


def objective_db_key(entry):
    return "vendor: %s, model: %s" % (entry["vendor"], entry["model"])


def parse_db_key(s):
    """ "vendor: X, model: Y, ..." => (vendor, model)"""
    fields = {}
    for part in s.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            fields[k.strip()] = v.strip()
    return fields.get("vendor"), fields.get("model")


def db_lookup(db, s):
    vendor, model = parse_db_key(s)
    if vendor is None or model is None:
        return None
    return db.db.get((vendor.upper(), model.upper()))


def tube_length_guess(objectives_db, db=None):
    """Common infinity_tube_length of the DB objectives, else the default"""
    if db is None:
        return TUBE_LENGTH_DEFAULT
    lengths = set()
    for s in objectives_db:
        entry = db_lookup(db, s)
        if entry and entry.get("infinity_tube_length"):
            lengths.add(entry["infinity_tube_length"])
    if len(lengths) == 1:
        return int(list(lengths)[0])
    return TUBE_LENGTH_DEFAULT


def default_out_dir(name):
    from uscope.config import get_configs_dir
    return os.path.join(get_configs_dir(), name)


def is_in_configs_dir(name, out_dir):
    """True if ./app/argus.py --microscope <name> will find out_dir"""
    return os.path.realpath(out_dir) == os.path.realpath(
        default_out_dir(name))


def source_defaults(source):
    """imager params implied by selecting source"""
    sd = SOURCES.get(source, {})
    width = sd.get("width")
    height = sd.get("height")
    return {
        "width": width,
        "height": height,
        "native_width": sd.get("native_width"),
        "native_height": sd.get("native_height"),
        "native_pixel_pitch_um": sd.get("native_pixel_pitch_um"),
        "v4l2_name": sd.get("v4l2_name"),
        "scalar": scalar_guess(sd.get("native_width") or width,
                               sd.get("native_height") or height),
    }


def default_params(name="my-microscope", source="gst-toupcamsrc"):
    ret = {
        "name": name,
        "description": "",
        "out_dir": None,
        "source": source,
        "model": None,
        "relay_magnification": 1.0,
        "infinity_tube_length": TUBE_LENGTH_DEFAULT,
        "telecentric": False,
        "objectives_db": list(DEFAULT_OBJECTIVES_DB),
        # [{"name", "magnification", "na"}]
        "objectives": [],
        "hal": "grbl-ser",
        # None => autodetect. "mock" => emulated GRBL
        "port": None,
        "axes": "xyz",
        "scalars": {
            "x": 1.0,
            "y": 1.0,
            "z": 1.0
        },
        "backlash": 0.05,
        "soft_limits": OrderedDict(DEFAULT_SOFT_LIMITS),
        # None => not specified
        "limit_switches": None,
        "use_wcs_offsets": False,
        "xyz_format6": False,
        "rc_pre_home": [],
        "rc_post_home": [],
    }
    ret.update(source_defaults(source))
    ret["um_per_pixel_raw_1x"] = um_per_pixel_guess_params(ret)
    ret.update(KINEMATICS_DEFAULT)
    return ret


def _float(v):
    return None if v is None else float(v)


def build(params):
    """
    Return (usj, comments)
    comments: {path tuple: [comment lines]} placed before that key
    """
    p = params
    usj = OrderedDict()
    comments = {}

    def comment(path, *lines):
        comments.setdefault(tuple(path), []).extend(lines)

    # Kinematics
    kinematics = OrderedDict()
    for k in KINEMATICS_DEFAULT:
        kinematics[k] = float(p[k])
    usj["kinematics"] = kinematics
    comment(("kinematics", ), "Settle times in seconds after a move")
    comment(("kinematics", "tsettle_motion_base"), CAL_GUESS)
    comment(("kinematics", "tsettle_hdr"),
            "Guess: increase if HDR exposures are mixed up")

    # Motion
    axes = p.get("axes") or "xyz"
    motion = OrderedDict()
    motion["hal"] = p["hal"]
    if axes != "xyz":
        motion["axes"] = axes
        comment(("motion", "axes"), "Z not motorized / not connected")
    if p.get("backlash") is not None:
        motion["backlash"] = float(p["backlash"])
        comment(("motion", "backlash"), "mm. " + CAL_GUESS)
    scalars = OrderedDict()
    for axis in axes:
        v = float((p.get("scalars") or {}).get(axis, 1.0))
        if v != 1.0:
            scalars[axis] = v
    if scalars:
        motion["scalars"] = scalars
        comment(("motion", "scalars"),
                "GUI mm => controller units. Ex: gearing / steps per mm")
    soft_limits = p.get("soft_limits")
    if soft_limits:
        limits = OrderedDict()
        for axis in axes:
            for which in ("min", "max"):
                k = axis + which
                if soft_limits.get(k) is not None:
                    limits[k] = float(soft_limits[k])
        if limits:
            motion["soft_limits"] = limits
            comment(("motion", "soft_limits"),
                    "Guess: set to your actual stage travel (mm)")
    if p.get("limit_switches") is not None:
        motion["limit_switches"] = bool(p["limit_switches"])
        if not p["limit_switches"]:
            comment(("motion", "limit_switches"),
                    "No limit switches: homing is skipped")
    if p.get("use_wcs_offsets"):
        motion["use_wcs_offsets"] = True
    if p.get("xyz_format6"):
        motion["xyz_format6"] = True
    if p["hal"] == "grbl-ser":
        grbl = OrderedDict()
        if p.get("port"):
            grbl["port"] = p["port"]
            if p["port"] == "mock":
                comment(("motion", "grbl", "port"),
                        "Emulated GRBL controller (no motion hardware)")
        if p.get("rc_pre_home"):
            grbl["rc_pre_home"] = list(p["rc_pre_home"])
            comment(("motion", "grbl", "rc_pre_home"),
                    "Sent to GRBL before homing")
        if p.get("rc_post_home"):
            grbl["rc_post_home"] = list(p["rc_post_home"])
        if grbl:
            motion["grbl"] = grbl
        if not p.get("port"):
            comment(("motion", "hal"), "Serial port is autodetected")
    usj["motion"] = motion

    # Imager
    imager = OrderedDict()
    imager["source"] = p["source"]
    if p.get("model"):
        imager["model"] = p["model"]
        comment(("imager", "model"),
                "Expected camera model. Only used to warn on a mismatch")
    if p.get("v4l2_name"):
        imager["v4l2_name"] = p["v4l2_name"]
        comment(("imager", "v4l2_name"),
                "Find first device matching this name")
    if p.get("native_width") and p.get("native_height"):
        imager["native_width"] = int(p["native_width"])
        imager["native_height"] = int(p["native_height"])
    if p.get("native_pixel_pitch_um"):
        imager["native_pixel_pitch_um"] = float(p["native_pixel_pitch_um"])
    if p.get("width") and p.get("height"):
        imager["width"] = int(p["width"])
        imager["height"] = int(p["height"])
        comment(("imager", "width"), "Sensor resolution to capture at")
    else:
        comment(("imager", "source"),
                "width / height not set: detected from the camera")
    imager["scalar"] = float(p.get("scalar") or 1.0)
    comment(("imager", "scalar"),
            "Scale output images. 0.5 is typical for large (> 10 MP) sensors")
    usj["imager"] = imager

    # Optics
    optics = OrderedDict()
    optics["um_per_pixel_raw_1x"] = float(p["um_per_pixel_raw_1x"])
    if p.get("native_pixel_pitch_um") and p.get("relay_magnification"):
        if p.get("native_width") and p.get("width"):
            how = "native_pixel_pitch_um / %gx relay * native_width / width"
        else:
            how = "native_pixel_pitch_um / %gx relay"
        comment(("optics", "um_per_pixel_raw_1x"),
                how % (float(p["relay_magnification"]), ))
    comment(("optics", "um_per_pixel_raw_1x"), CAL_GUESS)
    if p.get("infinity_tube_length"):
        optics["infinity_tube_length"] = int(p["infinity_tube_length"])
        comment(("optics", "infinity_tube_length"),
                "mm. Olympus: 180, Mitutoyo / Nikon: 200")
    optics["telecentric"] = bool(p.get("telecentric"))
    usj["optics"] = optics

    # Objectives
    system = OrderedDict()
    system["camera_sn"] = None
    if p.get("objectives_db"):
        system["objectives_db"] = list(p["objectives_db"])
    if p.get("objectives"):
        objectives = []
        for objective in p["objectives"]:
            o = OrderedDict()
            o["name"] = str(objective["name"])
            o["magnification"] = float(objective["magnification"])
            if objective.get("na") is not None:
                o["na"] = float(objective["na"])
            objectives.append(o)
        system["objectives"] = objectives
    usj["systems"] = [system]
    comment(("systems", ), "Default configuration (any camera serial number)")
    return usj, comments


def build_config(params):
    return build(params)[0]


def validate_params(params):
    """Return a list of error strings"""
    p = params
    ret = []
    err = validate_name(p.get("name"))
    if err:
        ret.append(err)
    if p.get("source") not in SOURCES:
        ret.append("Unknown imager source: %s" % (p.get("source"), ))
    if bool(p.get("width")) != bool(p.get("height")):
        ret.append("Set both camera width and height (or neither)")
    if p.get("source") != "gst-toupcamsrc" and not (p.get("width")
                                                   and p.get("height")):
        ret.append("Camera width / height are required for %s" %
                   (p.get("source"), ))
    if not p.get("um_per_pixel_raw_1x") or p["um_per_pixel_raw_1x"] <= 0:
        ret.append("um_per_pixel_raw_1x must be > 0")
    if not p.get("objectives_db") and not p.get("objectives"):
        ret.append("At least one objective is required")
    for objective in p.get("objectives") or []:
        if not objective.get("name"):
            ret.append("Custom objective is missing a name")
        if not objective.get("magnification"):
            ret.append("Custom objective %s is missing magnification" %
                       (objective.get("name"), ))
    if p.get("hal") not in HALS:
        ret.append("Unknown motion hal: %s" % (p.get("hal"), ))
    limits = p.get("soft_limits") or {}
    for axis in p.get("axes") or "xyz":
        axmin = limits.get(axis + "min")
        axmax = limits.get(axis + "max")
        if axmin is not None and axmax is not None and axmin >= axmax:
            ret.append("Soft limit %s: min must be less than max" % (axis, ))
    return ret


"""
json5 emitter
Hand written so that comments can be included
"""


def _scalar_j5(v):
    if isinstance(v, float):
        return repr(v)
    return json.dumps(v)


def _comment_lines(lines, indent):
    return ["%s//%s" % (indent, l.replace("\n", " ")) for l in lines]


def _emit(value, comments, path, level, out):
    """Append lines for a value whose first line is already started"""
    indent = "    " * (level + 1)
    if isinstance(value, dict):
        out[-1] += "{"
        for k, v in value.items():
            this_path = path + (k, )
            out.extend(_comment_lines(comments.get(this_path, []), indent))
            out.append("%s%s: " % (indent, json.dumps(k)))
            _emit(v, comments, this_path, level + 1, out)
            out[-1] += ","
        out.append("    " * level + "}")
    elif isinstance(value, (list, tuple)):
        out[-1] += "["
        for i, v in enumerate(value):
            out.append(indent)
            _emit(v, comments, path + (i, ), level + 1, out)
            out[-1] += ","
        out.append("    " * level + "]")
    else:
        out[-1] += _scalar_j5(value)


def header_comment(params, date=None):
    if date is None:
        date = datetime.date.today().isoformat()
    lines = []
    for l in (params.get("description") or "").strip().split("\n"):
        if l.strip():
            lines.append(l.rstrip())
    if lines:
        lines.append("")
    lines += [
        "Generated by ./app/microforge.py (New Microscope Wizard) on %s" %
        (date, ),
        "Values marked \"Guess\" are starting points, not measurements",
        "To get real values: run ./app/argus.py --microscope %s" %
        (params.get("name"), ),
        "then Advanced options => Calibration",
        "Calibrated values are kept in the calibration store and override this file",
    ]
    # Don't let the description end the comment early
    return "/*\n%s\n*/\n" % "\n".join(lines).replace("*/", "* /")


def render_j5(params, date=None):
    usj, comments = build(params)
    out = [""]
    _emit(usj, comments, (), 0, out)
    return header_comment(params, date=date) + "\n".join(out) + "\n"


def write_config(params, out_dir=None):
    """Write microscope.j5, returning its path"""
    if out_dir is None:
        out_dir = params.get("out_dir") or default_out_dir(params["name"])
    os.makedirs(out_dir, exist_ok=True)
    fn = os.path.join(out_dir, "microscope.j5")
    with open(fn, "w") as f:
        f.write(render_j5(params))
    return fn


"""
Template from an existing config (--from)
"""


def load_usj(name_or_path):
    import json5
    from uscope.config import get_configs_dir
    fn = name_or_path
    if os.path.isdir(fn):
        fn = os.path.join(fn, "microscope.j5")
    elif not os.path.exists(fn):
        fn = os.path.join(get_configs_dir(), name_or_path, "microscope.j5")
    with open(fn) as f:
        return json5.load(f, object_pairs_hook=OrderedDict)


def params_from_usj(usj, params=None):
    """
    Best effort: fill in wizard params from an existing config
    Settings the wizard doesn't know about (ex: crop, source_properties) are dropped
    """
    p = copy.deepcopy(params) if params else default_params()

    imager = usj.get("imager") or {}
    source = imager.get("source")
    if source in SOURCES:
        p["source"] = source
        p.update(source_defaults(source))
    for k in ("width", "height", "native_width", "native_height",
              "native_pixel_pitch_um", "model", "v4l2_name", "scalar"):
        if k in imager:
            p[k] = imager[k]
    if "width" in imager and "scalar" not in imager:
        p["scalar"] = 1.0

    optics = usj.get("optics") or {}
    if optics.get("um_per_pixel_raw_1x"):
        p["um_per_pixel_raw_1x"] = float(optics["um_per_pixel_raw_1x"])
    elif optics.get("image_width_1x_mm") and p.get("width"):
        p["um_per_pixel_raw_1x"] = round(
            optics["image_width_1x_mm"] * 1000 / p["width"], 6)
    elif imager.get("um_per_pixel_raw_1x"):
        p["um_per_pixel_raw_1x"] = float(imager["um_per_pixel_raw_1x"])
    else:
        p["um_per_pixel_raw_1x"] = um_per_pixel_guess_params(p)
    if optics.get("infinity_tube_length"):
        p["infinity_tube_length"] = int(optics["infinity_tube_length"])
    if "telecentric" in optics:
        p["telecentric"] = bool(optics["telecentric"])

    systems = usj.get("systems") or []
    system = None
    for this in systems:
        if not this.get("camera_sn"):
            system = this
            break
    if system is None and systems:
        system = systems[0]
    objectives_db = []
    objectives = []
    if system:
        objectives_db = list(system.get("objectives_db", []))
        for o in system.get("objectives", []):
            if o.get("name") and o.get("magnification"):
                objectives.append({
                    "name": o["name"],
                    "magnification": float(o["magnification"]),
                    "na": o.get("na"),
                })
    if objectives_db or objectives:
        p["objectives_db"] = objectives_db
        p["objectives"] = objectives

    motion = usj.get("motion") or {}
    if motion.get("hal") in HALS:
        p["hal"] = motion["hal"]
    grbl = motion.get("grbl") or {}
    p["port"] = grbl.get("port")
    p["rc_pre_home"] = [str(x) for x in grbl.get("rc_pre_home", [])]
    p["rc_post_home"] = [str(x) for x in grbl.get("rc_post_home", [])]
    if motion.get("axes"):
        p["axes"] = motion["axes"]
    scalars = {"x": 1.0, "y": 1.0, "z": 1.0}
    scalars.update(motion.get("scalars") or {})
    p["scalars"] = scalars
    backlash = motion.get("backlash")
    if isinstance(backlash, dict):
        backlash = max([float(v) for v in backlash.values()] or [0.0])
    if backlash is not None:
        p["backlash"] = float(backlash)
    if motion.get("soft_limits"):
        p["soft_limits"] = OrderedDict(motion["soft_limits"])
    p["limit_switches"] = motion.get("limit_switches")
    p["use_wcs_offsets"] = bool(motion.get("use_wcs_offsets", False))
    p["xyz_format6"] = bool(motion.get("xyz_format6", False))

    kinematics = usj.get("kinematics") or {}
    for k in KINEMATICS_DEFAULT:
        if k in kinematics:
            p[k] = float(kinematics[k])
    return p
