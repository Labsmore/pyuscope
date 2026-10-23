"""
Sanity check that the camera found matches the microscope config

A wrong microscope config (bad --microscope, stale PYUSCOPE_MICROSCOPE, GRBL
auto config on a swapped controller, ...) otherwise shows up as an obscure
gstreamer error (ex: not-negotiated) or silently streams from some other camera

The comparison logic is Qt / gstreamer free so it can be unit tested
"""

import re


class CameraNotFound(Exception):
    pass


def config_fix_hint(microscope_name=None):
    ret = "If this is the wrong microscope config, select the correct one with --microscope <name>"
    ret += " or by setting the environment variable PYUSCOPE_MICROSCOPE=<name>"
    try:
        from uscope.config import get_configs_dir
        import os
        configs_dir = get_configs_dir()
        names = sorted(d for d in os.listdir(configs_dir)
                       if os.path.isdir(os.path.join(configs_dir, d)))
        ret += "\nAvailable configs: " + ", ".join(names)
    except Exception:
        pass
    if microscope_name:
        ret += f"\nIf {microscope_name} is correct, check that the camera is plugged in, powered and not in use by another program"
    return ret


def caps_sizes(caps_str):
    """
    Fixed width x height pairs in a gstreamer caps string, in order, deduplicated
    Sizes given as ranges / lists are not reported
    """
    ret = []
    for structure in caps_str.split(";"):
        mw = re.search(r"width=\(int\)(\d+)", structure)
        mh = re.search(r"height=\(int\)(\d+)", structure)
        if not mw or not mh:
            continue
        wh = (int(mw.group(1)), int(mh.group(1)))
        if wh not in ret:
            ret.append(wh)
    return ret


def size_caps_str(caps_str, width, height):
    """
    Caps string matching any media type in caps_str at the given size
    Lets the size be checked independent of the pixel format
    """
    names = []
    for structure in caps_str.split(";"):
        name = structure.strip().split(",")[0].strip()
        if name and name not in names:
            names.append(name)
    return "; ".join(f"{name},width={width},height={height}" for name in names)


def model_matches(expected, found):
    """
    Case insensitive substring match
    Lets configs give a short name (ex: "E3ISPM20000KPA")
    while the SDK reports something longer
    """
    return expected.strip().lower() in found.strip().lower()


def check_camera(expected, found):
    """
    expected: from microscope config
        wh: (w, h) raw sensor size the pipeline will request
        model: optional expected camera model (imager.model)
        v4l2_name: optional expected v4l2 card name (imager.v4l2_name)
    found: probed from the opened source
        model: camera model name, if the source reports one
        card: v4l2 card name, if applicable
        wh_supported: True / False / None (unknown)
        sizes: list of (w, h) the camera reported (may be empty)
    Returns a list of human readable problems (empty if ok)
    """
    issues = []
    exp_model = expected.get("model")
    found_model = found.get("model")
    if exp_model:
        if not found_model:
            issues.append(
                f"Config expects camera model '{exp_model}' but the camera did not report a model"
            )
        elif not model_matches(exp_model, found_model):
            issues.append(
                f"Config expects camera model '{exp_model}' but found '{found_model}'"
            )
    exp_card = expected.get("v4l2_name")
    found_card = found.get("card")
    if exp_card and found_card and exp_card != found_card:
        issues.append(
            f"Config expects v4l2 camera '{exp_card}' but found '{found_card}'"
        )
    wh = expected.get("wh")
    if wh and found.get("wh_supported") is False:
        msg = f"Config expects a {wh[0]}x{wh[1]} sensor (imager width / height)"
        sizes = found.get("sizes")
        if sizes:
            msg += " but the camera supports " + ", ".join("%ux%u" % size
                                                           for size in sizes)
        else:
            msg += " but the camera does not support it"
        issues.append(msg)
    return issues


def describe_camera(found):
    parts = []
    for k, label in (("model", "model"), ("card", "name"), ("serial", "S/N"),
                     ("device", "device")):
        v = found.get(k)
        if v:
            parts.append(f"{label} {v}")
    sizes = found.get("sizes")
    if sizes:
        parts.append("sizes " + ", ".join("%ux%u" % size for size in sizes))
    return ", ".join(parts) if parts else "no identifying information"


def mismatch_message(microscope_name, config_reason, source_name, found,
                     issues):
    lines = [
        f"Microscope config '{microscope_name}' (selected by: {config_reason}) does not appear to match the attached camera",
        "",
        f"Source: {source_name}",
        f"Camera found: {describe_camera(found)}",
        "",
    ]
    lines += ["Problems:"] + ["  " + issue for issue in issues]
    lines += ["", config_fix_hint(microscope_name)]
    return "\n".join(lines)


def not_found_message(microscope_name, config_reason, source_name, detail):
    lines = [
        f"No camera found for microscope config '{microscope_name}' (selected by: {config_reason})",
        "",
        f"Source: {source_name}",
        detail,
        "",
        config_fix_hint(microscope_name),
    ]
    return "\n".join(lines)
