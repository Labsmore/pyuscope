import json
import os
from collections import OrderedDict

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
    "out_dir":"out",
    "imager": {
        "engine":'mock',
        "snapshot_dir":"snapshot",
        "width": 3264,
        "height": 2448,
        "scalar": 0.5,
   },
    "cnc": {
        # Good for testing and makes usable to systems without CNC
        "engine": "mock",
        "startup_run": False,
        "startup_run_exit": False,
        "overwrite":False,
        # Default to no action, make movement explicit
        # Note that GUI can override this
        "dry":True,
    }
}
    
def get_config(fn='microscope.json'):
    j = json.load(open('microscope.json'), object_pairs_hook=OrderedDict)
    def default(rootj, rootd):
        for k, v in rootd.iteritems():
            if not k in rootj:
                rootj[k] = v
            elif type(v) is dict:
                default(rootj[k], v)
    default(j, defaults)
    return j
