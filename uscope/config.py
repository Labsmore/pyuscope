import json
import os
from collections import OrderedDict
from uscope.util import writej, readj
'''
A few general assumptions:
-Camera is changed rarely.  Therefore only one camera per config file
-Objectives are changed reasonably often
    They cannot changed during a scan
    They can be changed in the GUI
'''

defaults = {
    "live_video": True,
    "objective_json": "objective.json",
    "scan_json": "scan.json",
    "out_dir": "out",
    "imager": {
        "engine": 'mock',
        "snapshot_dir": "snapshot",
        "width": 3264,
        "height": 2448,
        "scalar": 0.5,
    },
    "cnc": {
        # Good for testing and makes usable to systems without CNC
        "engine": "mock",
        "startup_run": False,
        "startup_run_exit": False,
        "overwrite": False,
        # Default to no action, make movement explicit
        # Note that GUI can override this
        "dry": True,
    }
}

# microscope.json
usj = None


def get_usj(config_dir=None):
    global usj

    if usj is not None:
        return usj

    if config_dir is None:
        config_dir = "config"
    j = json.load(open(os.path.join(config_dir, "microscope.json")),
                  object_pairs_hook=OrderedDict)

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


def config_dir():
    return "config"


def cal_fn(mkdir=False):
    if mkdir and not os.path.exists(config_dir()):
        os.mkdir(config_dir())
    return os.path.join(config_dir(), "imager_calibration.json")


def cal_load(source):
    fn = cal_fn()
    if not os.path.exists(fn):
        return
    configj = readj(fn)
    configs = configj["configs"]
    for config in configs:
        if config["source"] == source:
            return config["properties"]
    return None


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
