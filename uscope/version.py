import subprocess
import re

meta_cache = None


def check_output(*args, **kwargs):
    bs = subprocess.check_output(*args, **kwargs)
    return bs.decode("ascii")


def get_meta():
    """
    {
        "tag": "v4.5.0",
        "ver": {
            "major": 4,
            "minor": 5,
            "patch": 0,
        },
        "githash": "ae2b9d253755a11771fd08514fb8369c5fbf2141",
        "dirty": true,
        "description": "v4.5.0-ae2b9d25-dirty" 
    }
    """
    global meta_cache
    if meta_cache:
        return meta_cache

    ret = {}
    ret["githash"] = check_output(["git", "rev-parse", "HEAD"],
                                  stderr=subprocess.DEVNULL).strip()
    tag = check_output(["git", "describe", "--tags", "--abbrev=0"],
                       stderr=subprocess.DEVNULL).strip()
    ret["tag"] = tag
    tag_githash = check_output(["git", "rev-parse", tag],
                               stderr=subprocess.DEVNULL).strip()
    major, minor, patch = tag[1:].split(".")
    ret["ver"] = {
        "major": int(major),
        "minor": int(minor),
        "patch": int(patch),
    }
    # ignore if extra config files
    ret["dirty"] = check_output(
        ["git", "status", "--untracked-files=no", "--porcelain"],
        stderr=subprocess.DEVNULL).strip().strip() != 0

    description = f"{tag}"
    if ret["dirty"] or ret["githash"] != tag_githash:
        description += "-" + ret["githash"][0:8]
        if ret["dirty"]:
            description += "-dirty"
    ret["description"] = description

    meta_cache = ret
    return meta_cache
