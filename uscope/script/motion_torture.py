"""
X1 cal: 0.15
X63.603 750 Y185.237 500 Z-25.795 250
"""

from uscope.config import get_usc
from uscope.scripting import ScriptingPlugin

import random
from uscope.util import utcnow
import os


class Plugin(ScriptingPlugin):
    def input_config(self):
        return {
            "Buttons": {
                "widget": "QPushButtons",
                "buttons": {
                    "Run": "run",
                    "Analyze": "analyze",
                },
            },
            "Output directory": {
                "widget": "QLineEdit",
                "type": str,
                "default": "motion_torture"
            },
            "Overwrite": {
                "widget": "QComboBox",
                "values": ["Yes", "No"],
                "default": "No"
            },
        }

    def collect(self, output_directory, overwrite=False):
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
        out_dir = output_directory
        if os.path.exists(out_dir):
            if not overwrite:
                raise ValueError("Refusing to overwrite")
        else:
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
                self.log(utcnow().isoformat())

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

    def analyze(self):
        assert 0, "fixme"

    def run_test(self):
        vals = self.get_input()
        """
        button = vals.get("button")

        task = button["value"]
        if task == "run":
            self.do_run(
                output_directory=vals["Output directory"],
                overwrite=vals["Overwrite"] == "Yes")
        elif task == "analyze":
            self.analyze(
                output_directory=vals["Output directory"],
                )
        else:
            assert 0, task
        """
        self.collect(output_directory=vals["Output directory"],
                     overwrite=vals["Overwrite"] == "Yes")
