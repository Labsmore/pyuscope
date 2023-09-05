"""
X1 cal: 0.15
X63.603 750 Y185.237 500 Z-25.795 250
"""

from uscope.config import get_usc
from uscope.app.argus.scripting import ArgusScriptingPlugin

import random
import datetime
import os


class Plugin(ArgusScriptingPlugin):
    def run_test(self):
        passes = 0
        axes = {"x", "y"}
        # axes={"x", "y", "z"}
        # Max single moves (ie 1 mm each direction)
        # However multiple moves may stack up
        scalars = {
            "x": 1.0,
            "y": 1.0,
            "z": 1.0,
        }
        burst_moves = 5
        out_dir = "motion_torture"
        if not os.path.exists(out_dir):
            os.mkdir(out_dir)

        def filter_moves(moves):
            ret = {}
            if "x" in axes:
                ret["x"] = moves["x"]
            if "y" in axes:
                ret["y"] = moves["y"]
            if "z" in axes:
                ret["z"] = moves["z"]
            return ret

        def current_pos():
            return filter_moves(self.pos())

        origin = current_pos()
        usc = get_usc()
        self.log("System ready")

        try:
            # self.backlash_disable()

            # Consider planner for movement? aware of things like backlash
            backlash = usc.usj["motion"].get("backlash", 0.0)
            self.log("Backlash: %0.3f" % backlash)

            def move_str(moves):
                ret = ""
                for axis in "xyz":
                    if not axis in axes:
                        continue
                    if ret:
                        ret += " "
                    ret += "%s%+0.3f" % (axis.upper(), moves[axis])
                return ret

            def move_absolute(moves):
                """
                Always approach from +axis
                TODO: check if need to compensate
                """
                # Make sure we don't go too far from origin
                for axis, pos in moves.items():
                    assert abs(pos - origin[axis]
                               ) < burst_moves * scalars[axis] + 0.01
                self.move_absolute(filter_moves(moves))

            self.log("Moving to origin")
            move_absolute(origin)
            self.log("movement done")

            passi = 0
            while True:
                self.check_running()
                passi += 1
                if passes and passi > passes:
                    break
                self.log("")
                self.log("Pass %u" % passi)
                self.log(datetime.datetime.utcnow().isoformat())

                # Do a series of moves
                for pokei in range(burst_moves):
                    self.log("Poke %u" % pokei)
                    # Encourage mainly small moves
                    # maybe log scale?
                    if random.randint(0, 4):
                        this_scalar = 0.1
                    else:
                        this_scalar = 1.0

                    def rand_move(axis):
                        delta = this_scalar * scalars[axis] * random.randrange(
                            -100, 100) / 100
                        pos = current_pos()[axis] + delta
                        # if abs(pos) > scalars[axis]:
                        #    pos = scalars[axis] * pos / abs(pos)
                        return pos

                    moves = {}
                    for axis in axes:
                        moves[axis] = rand_move(axis)
                    self.log("move %s" % (move_str(moves), ))
                    move_absolute(moves)

                self.log("Moving to origin")
                # Snap back to origin to verify no drift
                move_absolute(origin)

                self.log("Getting image")
                self.sleep(1.5)
                im = self.image()
                im.save("%s/%003u.jpg" % (out_dir, passi))
                self.log("Pass done")

        finally:
            # self.backlash_enable()
            pass

        self.log("Main loop done")
