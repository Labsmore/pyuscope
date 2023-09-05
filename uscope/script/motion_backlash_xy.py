"""
X1 cal: 0.15
X63.603 750 Y185.237 500 Z-25.795 250
"""

from uscope.app.argus.scripting import ArgusScriptingPlugin
from uscope.cal_util import move_str

import datetime
import time
import os
import json
"""
X1 test setup
20x objective
0.15 mm fiducial
0.25 mm travel
Roughly in upper right of image view
Center is fine have plenty of room
"""


class Plugin(ArgusScriptingPlugin):
    def run_test(self):
        out_dir = "motion_backlash_xy"
        axmin = 0.0
        step_mm = 1 / 800
        steps = 25
        # 1.25 um steps
        axmax = step_mm * steps
        # A value well above the estimated backlash
        # Should be the entire movement range
        safe_backlash = axmax
        # Divide movement range into this many movements
        axes = {"x", "y"}

        self.log(f"Range: {axmin} to {axmax}")
        self.log(f"Step: {steps}, each {step_mm} mm")

        tstart = time.time()
        if not os.path.exists(out_dir):
            os.mkdir(out_dir)

        origin = self.pos()

        def move_absolute(moves, comp=False):
            """
            Always approach from +axis
            TODO: check if need to compensate
            """
            if comp:
                rmoves = {}
                for axis in moves.keys():
                    # Move beyond the destination to force compensation
                    # FIXME: assumes negative backlash
                    rmoves[axis] = moves[axis] + safe_backlash
                self.move_absolute(rmoves)
            self.move_absolute(moves)

        def run_axis(loop_axis):
            move_log = {}
            j["axes"][loop_axis] = move_log
            self.log("")
            self.log("Testing axis %s" % axis)
            # First step at origin
            for step in range(steps + 1):
                self.log("")
                self.log("Pass %u" % step)
                self.log(datetime.datetime.utcnow().isoformat())
                self.log("Moving %s to origin" % axis)
                # Snap back to origin to verify no drift
                move_absolute({axis: origin[axis]}, comp=True)

                # Now attempt to move and see if we actually do and by how much
                # FIXME: assumes negative backlash
                this_move = origin[axis] + axmin + (axmax - axmin) * (step /
                                                                      steps)
                moves = {loop_axis: this_move}
                self.log("move %s" % (move_str(moves), ))
                move_absolute(moves, comp=False)

                # Record image to post process
                self.log("Getting image")
                self.sleep(1.5)
                im = self.image()
                basename = "%s_%003u.jpg" % (loop_axis, step)
                fn = "%s/%s" % (out_dir, basename)
                self.log("Saving %s" % fn)
                im.save(fn)

                move_log[step] = {
                    "magnitude": this_move,
                    "fn": basename,
                }
                self.log("Pass done")
                open("%s/log.json" % (out_dir, ), "w").write(
                    json.dumps(j,
                               sort_keys=True,
                               indent=4,
                               separators=(',', ': ')))
            return move_log

        j = {
            "test": "motion_backlash_xy",
            "steps": steps,
            "min": axmin,
            "max": axmax,
            "origin": origin,
            "axes": {},
        }
        try:
            for axis in sorted(axes):
                # Disable compensation so we can characterize it cleaner
                self.backlash_disable()
                j["axes"][axis] = run_axis(axis)
        finally:
            self.backlash_enable()

        j["seconds"] = int(time.time() - tstart)
        self.log("Main loop done")
        open("%s/log.json" % (out_dir, ), "w").write(
            json.dumps(j, sort_keys=True, indent=4, separators=(',', ': ')))
