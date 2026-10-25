"""
toupcamsrc resolution selection

toupcamsrc only exposes the selected resolution via the "esize" property
(SDK still resolution index) which is applied when the camera is opened.
Available resolutions are listed by GST_TOUPCAMSRC_INFO=Y (stdout dump)

Priority:
-User choice (Main tab camera resolution), persisted per microscope S/N + camera S/N
-microscope.j5 imager width/height and/or source_properties.esize
-Camera max resolution

Changing it at runtime restarts the camera (see GstImagerPipeline.reconfigure_geometry())
"""

import json5
import os
import re
import subprocess


class CameraNotFound(Exception):
    pass


def parse_info(text):
    """
    Parse GST_TOUPCAMSRC_INFO output
    Only resolutions + serial number: the rest of the dump varies by SDK / plugin version

      StillResolutionNumber(): 3
        eSize=0
              StillResolution(): 4928w x 4928h
              PixelSize(): 1.1w x 1.1h um
        ...
      SerialNumber(): TP2210101618037715CAC72D6E44CF4

    Returns {"serial": str or None, "resolutions": [{"esize", "width", "height"}, ...]}
    """
    ret = {"serial": None, "resolutions": []}
    esize = None
    for l in text.split("\n"):
        m = re.match(r"\s*eSize=(\d+)\s*$", l)
        if m:
            esize = int(m.group(1))
            continue
        m = re.match(r"\s*StillResolution\(\): (\d+)w x (\d+)h", l)
        if m and esize is not None:
            ret["resolutions"].append({
                "esize": esize,
                "width": int(m.group(1)),
                "height": int(m.group(2)),
            })
            esize = None
            continue
        m = re.match(r"\s*SerialNumber\(\): (\S+)", l)
        if m:
            ret["serial"] = m.group(1)
    return ret


def probe(timeout=15):
    """
    Query the attached camera (opens + closes it in a subprocess, ~750 ms)
    """
    env = dict(os.environ)
    env["GST_TOUPCAMSRC_INFO"] = "Y"
    try:
        p = subprocess.run(
            ["gst-launch-1.0", "toupcamsrc", "num-buffers=0", "!", "fakesink"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CameraNotFound(f"toupcamsrc probe failed: {e}")
    ret = parse_info(p.stdout.decode("ascii", errors="replace"))
    if not ret["resolutions"]:
        raise CameraNotFound("toupcamsrc probe: no camera resolutions found")
    return ret


def esize_max():
    """
    Highest esize the installed toupcamsrc accepts (GParamSpec range)
    None if unknown
    """
    try:
        from gi.repository import Gst
        source = Gst.ElementFactory.make("toupcamsrc")
        if source is None:
            return None
        spec = source.find_property("esize")
        if spec is None:
            return None
        return spec.maximum
    except Exception:
        return None


def filter_settable(resolutions, max_esize):
    """
    Drop resolutions the plugin can't select
    (toupcamsrc <= 0.7.0 limits esize to 0 - 2)
    """
    if max_esize is None:
        return list(resolutions)
    return [r for r in resolutions if r["esize"] <= max_esize]


def max_resolution(resolutions):
    """
    Largest by pixel count
    Ties: first listed
    """
    if not resolutions:
        return None
    return max(resolutions, key=lambda r: r["width"] * r["height"])


def find_wh(resolutions, wh):
    if wh is None:
        return None
    w, h = int(wh[0]), int(wh[1])
    for r in resolutions:
        if (r["width"], r["height"]) == (w, h):
            return r
    return None


def find_esize(resolutions, esize):
    for r in resolutions:
        if r["esize"] == esize:
            return r
    return None


def resolution_label(r, resolutions=None):
    ret = "%u x %u" % (r["width"], r["height"])
    if resolutions and r is max_resolution(resolutions):
        ret += " (max)"
    return ret


def select(resolutions, config_wh=None, config_esize=None, user_wh=None):
    """
    Pick the resolution to open the camera at
    resolutions: from probe() (filtered to settable). May be empty if the camera couldn't be probed

    Returns (resolution dict, how)
    how: "user", "config" or "max"
    """
    r = find_wh(resolutions, user_wh)
    if r:
        return r, "user"

    if config_wh:
        r = find_wh(resolutions, config_wh)
        if r:
            if config_esize is not None and config_esize != r["esize"]:
                raise ValueError(
                    "microscope.j5 imager %ux%u doesn't match esize %u (camera esize %u)"
                    % (config_wh[0], config_wh[1], config_esize, r["esize"]))
            return r, "config"
        if resolutions:
            raise ValueError(
                "microscope.j5 imager %ux%u not supported by camera. Supported: %s"
                % (config_wh[0], config_wh[1], ", ".join(
                    resolution_label(r) for r in resolutions)))
        # Couldn't probe: trust the config as before
        return {
            "esize": config_esize,
            "width": int(config_wh[0]),
            "height": int(config_wh[1]),
        }, "config"

    if config_esize is not None:
        r = find_esize(resolutions, config_esize)
        if r is None:
            raise ValueError(
                "microscope.j5 esize %u: camera resolution unknown" %
                (config_esize, ))
        return r, "config"

    r = max_resolution(resolutions)
    if r is None:
        raise CameraNotFound(
            "imager width/height not configured and couldn't query camera resolutions"
        )
    return r, "max"


def config_resolution(imagerj):
    """
    Resolution hard coded in microscope.j5 imager
    Returns (wh or None, esize or None)
    """
    w = imagerj.get("width")
    h = imagerj.get("height")
    wh = None
    if w is not None and h is not None:
        wh = (int(w), int(h))
    esize = imagerj.get("source_properties", {}).get("esize")
    if esize is not None:
        esize = int(esize)
    return wh, esize


def apply_to_imagerj(imagerj, r, max_r=None):
    """
    Make microscope.j5 imager (in memory) reflect the selected resolution
    so that raw_wh() etc and prepareSource() pick it up
    May be called again at runtime to switch resolution

    native_width/height (basis of hardware_resolution_scalar()):
    camera max if probed, else configured native_width/height, else
    configured width/height
    config_wh: width/height as written in microscope.j5 (see USCImager.authored_wh())
    """
    if "config_wh" not in imagerj:
        config_wh, _config_esize = config_resolution(imagerj)
        imagerj["config_wh"] = list(config_wh) if config_wh else None
    config_wh = imagerj["config_wh"]
    if max_r:
        imagerj["native_width"] = max_r["width"]
        imagerj["native_height"] = max_r["height"]
    elif imagerj.get("native_width") is None or imagerj.get(
            "native_height") is None:
        if config_wh:
            native = config_wh
        else:
            native = r["width"], r["height"]
        imagerj["native_width"], imagerj["native_height"] = native
    imagerj["width"] = r["width"]
    imagerj["height"] = r["height"]
    if r["esize"] is not None:
        imagerj.setdefault("source_properties", {})["esize"] = r["esize"]


def saved_resolution(cachej_sn, camera_sn):
    """
    User selected resolution persisted in the Argus cache (see save_resolution())
    Ignored if saved for a different camera
    Returns (w, h) or None
    """
    j = cachej_sn.get("imager", {}).get("resolution")
    if not j:
        return None
    saved_sn = j.get("camera_sn")
    if saved_sn and camera_sn and saved_sn != camera_sn:
        return None
    try:
        return int(j["width"]), int(j["height"])
    except (KeyError, TypeError, ValueError):
        return None


def save_resolution(cachej_sn, wh, camera_sn):
    """
    wh None: clear the user selection
    """
    imagerj = cachej_sn.setdefault("imager", {})
    if wh is None:
        imagerj.pop("resolution", None)
    else:
        imagerj["resolution"] = {
            "width": int(wh[0]),
            "height": int(wh[1]),
            "camera_sn": camera_sn,
        }


def load_cachej_sn(fn, model_serial_string):
    """
    Per microscope S/N entry of the Argus cache file
    Read directly since the camera is configured before Argus loads its cache
    """
    if not os.path.exists(fn):
        return {}
    try:
        with open(fn, "r") as f:
            cachej = json5.load(f)
    except Exception:
        return {}
    return cachej.get("microscopes", {}).get(model_serial_string, {})


class ResolutionSelector:
    """
    Selects the camera resolution before the pipeline is built and tracks
    the user override
    """

    def __init__(self, usc_imager, cache_fn, model_serial_string, log=None):
        self.usc_imager = usc_imager
        self.cache_fn = cache_fn
        self.model_serial_string = model_serial_string
        self.log = log or print
        self.camera_sn = None
        # Settable resolutions. Empty if the camera couldn't be probed
        self.resolutions = []
        self.camera_max = None
        # Resolution the camera is running at
        self.active = None
        self.how = None
        # User selection (wh)
        self.override = None
        # microscope.j5 values (imager j is updated with the selection)
        self.config_wh = None
        self.config_esize = None

    def configure(self, probe_func=probe, max_esize_func=esize_max):
        imagerj = self.usc_imager.j
        config_wh, config_esize = config_resolution(imagerj)
        self.config_wh, self.config_esize = config_wh, config_esize
        try:
            info = probe_func()
        except CameraNotFound as e:
            if not config_wh:
                raise
            self.log(f"WARNING: {e}. Using microscope.j5 resolution")
            info = {"serial": None, "resolutions": []}
        self.camera_sn = info["serial"]
        self.camera_max = max_resolution(info["resolutions"])
        self.resolutions = filter_settable(info["resolutions"],
                                           max_esize_func())
        if len(self.resolutions) != len(info["resolutions"]):
            self.log(
                "WARNING: toupcamsrc can't select some camera resolutions (esize range)"
            )

        user_wh = saved_resolution(
            load_cachej_sn(self.cache_fn, self.model_serial_string),
            self.camera_sn)
        if user_wh and not find_wh(self.resolutions, user_wh):
            self.log(
                "WARNING: ignoring saved resolution %ux%u: not supported by camera"
                % user_wh)
            user_wh = None

        self.active, self.how = select(self.resolutions,
                                       config_wh=config_wh,
                                       config_esize=config_esize,
                                       user_wh=user_wh)
        if self.how == "user":
            self.override = user_wh
        apply_to_imagerj(imagerj, self.active, max_r=self.camera_max)
        self.log("Camera resolution: %ux%u (esize %s, %s)" %
                 (self.active["width"], self.active["height"],
                  self.active["esize"], self.how))

    def label(self, r):
        return resolution_label(r, self.resolutions)

    def selected_wh(self):
        """
        What the camera is running at (w, h)
        """
        return self.active["width"], self.active["height"]

    def can_select(self):
        return len(self.resolutions) > 1

    def unavailable_reason(self):
        """
        Why can_select() is False (None if it isn't)
        """
        if not self.resolutions:
            return "Couldn't query the camera's resolutions (using microscope.j5 width / height)"
        if len(self.resolutions) == 1:
            return "The camera has a single selectable resolution"
        return None

    def apply(self, imagerj, r):
        apply_to_imagerj(imagerj, r, max_r=self.camera_max)

    def set_override(self, wh, apply=None):
        """
        wh: (w, h) or None to restore the default (microscope.j5 or camera max)
        apply(r): switch the running camera to resolution r
            (ex: GstImagerPipeline.reconfigure_geometry). Raises on failure
        Persisted by cache_sn_save()
        """
        if wh is None:
            r, how = select(self.resolutions,
                            config_wh=self.config_wh,
                            config_esize=self.config_esize)
        else:
            r = find_wh(self.resolutions, wh)
            if not r:
                raise ValueError("Resolution %ux%u not supported by camera" %
                                 tuple(wh))
            wh = (int(wh[0]), int(wh[1]))
            how = "user"
        if apply is not None and r != self.active:
            apply(r)
        self.active = r
        self.how = how
        self.override = wh

    def cache_sn_save(self, cachej_sn):
        # Camera wasn't queried: keep whatever was saved before
        if not self.resolutions:
            return
        save_resolution(cachej_sn, self.override, self.camera_sn)
