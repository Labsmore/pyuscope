"""
toupcamsrc resolution selection

toupcamsrc only exposes the selected resolution via the "esize" property
(SDK still resolution index) which is applied when the camera is opened.
Available resolutions are listed by GST_TOUPCAMSRC_INFO=Y (stdout dump)

Priority:
-User choice (Video => Resolution), persisted per microscope S/N + camera S/N
-microscope.j5 imager width/height and/or source_properties.esize
-Camera max resolution
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
    Ties: lowest esize (SDK lists largest first)
    """
    if not resolutions:
        return None
    return max(resolutions,
               key=lambda r: (r["width"] * r["height"], -r["esize"]))


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

    native_width/height (basis of hardware_resolution_scalar()) keeps its
    old meaning: configured width/height if any, otherwise the camera max
    """
    if imagerj.get("native_width") is None or imagerj.get(
            "native_height") is None:
        config_wh, _config_esize = config_resolution(imagerj)
        if config_wh:
            native = config_wh
        elif max_r:
            native = max_r["width"], max_r["height"]
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


def pixel_crop_configured(usc_imager):
    """
    Crop specified in pixels is only valid for the resolution it was set up for
    (crop_fractions scale)
    """
    if "crop_pixels" in usc_imager.j:
        return True
    store = getattr(usc_imager.microscope, "cal_store", None)
    if store is None:
        return False
    try:
        return any(e.key == "imager.crop_pixels"
                   for e in store.entries(include_disabled=False))
    except Exception:
        return False


class ResolutionSelector:
    """
    Selects the camera resolution before the pipeline is built and tracks
    the user override (applied on next start)
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
        # Resolution the camera is opened at
        self.active = None
        self.how = None
        # User selection (wh), takes effect on next start
        self.override = None
        self.pixel_crop = False
        # microscope.j5 values (imager j is updated with the selection)
        self.config_wh = None
        self.config_esize = None

    def configure(self, probe_func=probe, max_esize_func=esize_max):
        imagerj = self.usc_imager.j
        config_wh, config_esize = config_resolution(imagerj)
        self.config_wh, self.config_esize = config_wh, config_esize
        self.pixel_crop = pixel_crop_configured(self.usc_imager)
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
        if user_wh and self.pixel_crop:
            self.log(
                "WARNING: ignoring saved resolution %ux%u: crop_pixels configured"
                % user_wh)
            user_wh = None
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

    def can_select(self):
        return len(self.resolutions) > 1 and not self.pixel_crop

    def set_override(self, wh):
        """
        wh: (w, h) or None to restore the default (microscope.j5 or camera max)
        Takes effect on next start
        """
        if wh is not None:
            if self.pixel_crop:
                raise ValueError(
                    "Can't change resolution: microscope crop is specified in pixels"
                )
            if not find_wh(self.resolutions, wh):
                raise ValueError("Resolution %ux%u not supported by camera" %
                                 tuple(wh))
            wh = (int(wh[0]), int(wh[1]))
        self.override = wh

    def restart_required(self):
        """
        Does the selection differ from what the camera is running at?
        """
        if self.active is None:
            return False
        if self.override is not None:
            want = self.override
        elif self.how == "user":
            # Default restored: config / max
            try:
                r, _how = select(self.resolutions,
                                 config_wh=self.config_wh,
                                 config_esize=self.config_esize)
                want = r["width"], r["height"]
            except Exception:
                return True
        else:
            return False
        return tuple(want) != (self.active["width"], self.active["height"])

    def cache_sn_save(self, cachej_sn):
        # Camera wasn't queried: keep whatever was saved before
        if not self.resolutions:
            return
        save_resolution(cachej_sn, self.override, self.camera_sn)
