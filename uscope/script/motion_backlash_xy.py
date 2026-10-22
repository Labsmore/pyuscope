#!/usr/bin/env python3

import matplotlib.pyplot as plt

from uscope.scripting import ScriptingPlugin
from uscope.calibration import find_centroid, find_centroid_fn, get_cal
from uscope.util import add_bool_arg, datetime_file_str, LogToFile

import numpy as np
import time
import os
import json
import glob


def distance(p1, p2):
    return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)**0.5


def savej(j, out_dir):
    open("%s/log.json" % (out_dir, ), "w").write(
        json.dumps(j, sort_keys=True, indent=4, separators=(',', ': ')))


def analyze(in_dir=None,
            show=False,
            title=None,
            log=None,
            microscope=None,
            verbose=False):
    if log is None:
        log = print
    if not in_dir:
        in_dir = sorted(glob.glob("data/script/*backlash-xy_*"))[-1]

    log("Loading from %s" % in_dir)
    with LogToFile(os.path.join(in_dir, "analysis.txt"), log=log) as log:
        j = json.load(open(in_dir + "/log.json"))

        model = j["microscope_model"]
        serial = j["microscope_serial"]
        if not title and model and serial:
            title = f"{model} S/N {serial}"

        # output
        pj = {}
        for axis, axisv in sorted(j["axes"].items()):
            log("")
            log("Axis " + str(axis))
            results = {"steps": {}}
            """
            imw, imh = 960, 960
            scalar_poss = get_positions(in_dir, axisv["ref_move"]["steps"])
            scalar_pos = min_pos(scalar_poss.values())
            steps_poss = get_positions(in_dir, axisv["steps"])
            steps_pos = min_pos(steps_poss.values())
            ref_pix = distance(scalar_pos, steps_pos)
            self.log("Reference move")
            ref_mag = axisv["ref_move"]["mag"]
            self.log("  mm: %0.1f" % ref_mag)
            self.log("  pixels: %0.1f" % ref_pix)
            imw_mm = ref_mag / ref_pix * imw
            self.log("  imw: %0.1f mm" % imw_mm)
            continue
                """

            xs = []
            ys = []
            um_per_pix = j["objective"]["um_per_pixel"]
            # pix_per_um = 1 / um_per_pix
            for stepi in range(axisv["n_moves"]):
                step_xs = []
                step_ys = []
                for repeati in range(axisv["n_repeat"]):
                    stepv = axisv["moves"][stepi * axisv["n_repeat"] + repeati]
                    fn = in_dir + "/" + stepv["fn"]
                    # self.log("Processing %s..." % fn)
                    x, y = find_centroid_fn(fn)
                    x *= um_per_pix
                    # ll vs ul origin
                    y *= -um_per_pix
                    step_xs.append(x)
                    step_ys.append(y)
                x = np.median(step_xs)
                y = np.median(step_ys)
                xs.append(x)
                ys.append(y)
            # Normalize to lowest coordinate
            xs_min = min(xs)
            ys_min = min(ys)
            xs = [x - xs_min for x in xs]
            ys = [y - ys_min for y in ys]
            origin = (max(xs), max(ys))
            print("origin", origin)
            print("xs", len(xs), xs)
            print("ys", len(ys), ys)
            ds = [distance((x, y), origin) for x, y in zip(xs, ys)]
            # convert mm to um
            step_keys = [
                1000 * axisv["step_size"] * x for x in range(axisv["n_moves"])
            ]

            # First 1/3 which has backlash region doesn't follow the trend
            regression_ds = ds[len(ds) // 3:]
            regression_xs = step_keys[len(step_keys) // 3:]
            model = np.polyfit(regression_xs, regression_ds, 1)
            print("model", model)
            ideal_ds = [d * model[0] + model[1] for d in regression_xs]

            # solve for y = 0
            # y = m x + b
            # x = (y - b) / m
            backlash = (0 - model[1]) / model[0]
            results["backlash"] = backlash
            pj[axis] = results

            plt.clf()

            plt.plot(step_keys, ds)
            plt.plot(regression_xs, ideal_ds)
            plt.ylabel("Offset (um)")
            plt.xlabel("Offset (um)")
            if not title:
                title = ""
            plt.title(title + f" ({axis})\nBacklash: %0.3f um" % backlash)
            plt.savefig("%s/processed_%s.png" % (in_dir, axis))
            if show:
                plt.show()

        open("%s/processed.json" % (in_dir, ), "w").write(
            json.dumps(pj, sort_keys=True, indent=4, separators=(',', ': ')))

        log("")
        log("")
        log("")
        log("Backlash summary")
        log("X backlash: %0.3f um" % (pj["x"]["backlash"], ))
        log("Y backlash: %0.3f um" % (pj["y"]["backlash"], ))


class Plugin(ScriptingPlugin):
    def input_config(self):
        return {
            "Output directory": {
                "widget": "QLineEdit",
                "type": str,
                "default": ""
            },
            # Collect a single image and throw thresholding
            "Sample": {
                "widget": "QPushButton",
                "value": "sample",
            },
            # Collect data
            "Collect": {
                "widget": "QPushButton",
                "value": "collect",
            },
            "Collect (quick)": {
                "widget": "QPushButton",
                "value": "collect_quick",
            },
            # Plot collected data
            "Analyze": {
                "widget": "QPushButton",
                "value": "analyze",
            },
            "Go to fiducial": {
                "widget": "QPushButton",
                "value": "fiducial",
            },
            "Autofocus": {
                "widget": "QComboBox",
                "values": ["Yes", "No"],
                "default": "No"
            },
        }

    def show_run_button(self):
        return False

    def sample(self):
        im = self.image()
        x, y = find_centroid(im, show=self.show_image)
        self.log(f"Centroid at x={x}, y={y}")

    def run_axis(self, axis, moves, step_size, repeat, quick=False):
        max_backlash = moves * step_size
        axj = {}
        axj["moves"] = []
        axj["n_moves"] = moves
        axj["n_repeat"] = repeat
        axj["step_size"] = step_size
        self.log("")
        self.log("Testing axis %s" % axis)

        axis_out_dir = self.output_directory + "/" + axis
        if not os.path.exists(axis_out_dir):
            os.mkdir(axis_out_dir)

        def save_image(basename):
            im = self.image()
            fn = os.path.join(self.output_directory, basename)
            # print("Saving %s" % fn)
            # should be .tif?
            im.save(fn, quality=95)

        for movei in range(moves):
            this_move = step_size * movei
            self.log("")
            self.log("%s move %u / %u: +%0.6f" %
                     (axis, movei + 1, moves, this_move))
            for repeati in range(repeat):
                # FIXME: assumes negative backlash correction
                # Do an initial move to set clean backlash compensated start position
                self.move_absolute({axis: self.origin[axis] + max_backlash})
                # Negative
                self.move_absolute({axis: self.origin[axis]})
                # Now start moving the opposite direction of the previous move
                # It won't initially move due toe backlash
                self.move_relative({axis: +this_move})
                self.log("sleep after move rel " + str(self.pos()))
                self.log("moving to origin " + str(self.origin[axis]))

                if not quick:
                    self.sleep(0.2)

                self.log("Saving image")
                # Record image to post process
                # self.log("Getting image")
                basename = "%s/%003u_r%02u.jpg" % (axis, movei, repeati)
                save_image(basename)

                axj["moves"].append({
                    "fn": basename,
                    "move": this_move,
                    "repeat": repeati,
                })
                # Keep saving in case crash during long collect
                # savej(axj, axis_out_dir)
        self.log("Pass done")
        return axj

    def collect(
        self,
        output_directory,
        overwrite=False,
        # ref_scalar=None,
        axes={"x", "y"},
        origin=None,
        autofocus=False,
        quick=False,
    ):
        self.backlash_disable()
        self.output_directory = str(output_directory)
        """
        VM1
        Repeatability is around 4 um
        Step size is 1.25 um
        Realistically should go in 5 um steps to be super useful
        (or take more data points)

        1.25 um steps
        """

        self.log(f"motion_backlash_xy, quick={quick}")
        if quick:
            x_moves = 4
            y_moves = 7
            repeat = 3
            x_step_size = 0.00125 * 2
            y_step_size = 0.00125 * 2
        else:
            x_moves = 15
            y_moves = 15
            repeat = 8
            x_step_size = 0.00125 * 1
            y_step_size = 0.00125 * 1
        #if ref_scalar is None:
        #    ref_scalar = backlash * 2

        # these could probably be 0 since most systems are already backlash compensated
        #x_max_backlash = 0.05
        #y_max_backlash = 0.05
        # motion = self.motion()
        # epsilons = motion.epsilon()

        if not self.output_directory:
            dt = datetime_file_str()
            model = self.microscope_model()
            sn = self.microscope_serial()
            sn_str = ""
            if sn:
                sn_str = f"_sn-{sn}"
            self.output_directory = f"data/script/{dt}_backlash-xy_model-{model}{sn_str}"

        self.log(f"Saving to {self.output_directory}")
        if os.path.exists(self.output_directory):
            # if not self.message_box_yes_cancel("Start?", "Output directory already exists. Are you sure you want to continue"):
            #     return
            if not overwrite:
                self.log("Aborted: refusing to overwrite existing directory")
                return
            self.log("WARNING: output directory already exists")
        else:
            os.mkdir(self.output_directory)

        tstart = time.time()
        if origin is None:
            origin = self.pos()
        self.origin = origin

        if autofocus:
            self.autofocus()

        j = {
            "test": "backlash_xy",
            "microscope_model": self.microscope_model(),
            "microscope_serial": self.microscope_serial(),
            "autofocus": autofocus,
            "quick": quick,
            "x_moves": x_moves,
            "y_moves": y_moves,
            "x_step_size": x_step_size,
            "y_step_size": y_step_size,
            "x_repeat": repeat,
            "y_repeat": repeat,
            "origin": self.origin,
            "objective": self.get_objective_config(),
            "axes": {},
        }

        def save_current():
            savej(j, self.output_directory)

        save_current()
        for axis in sorted(axes):
            moves = {
                "x": x_moves,
                "y": y_moves,
            }[axis]
            step_size = {
                "x": x_step_size,
                "y": y_step_size,
            }[axis]
            j["axes"][axis] = self.run_axis(axis=axis,
                                            step_size=step_size,
                                            repeat=repeat,
                                            moves=moves,
                                            quick=quick)
            save_current()

        j["seconds"] = int(time.time() - tstart)
        self.log("Main loop done")
        save_current()

    def cleanup(self):
        self.backlash_enable()
        if self.origin is not None:
            self.move_absolute(self.origin)

    def run_test(self):
        self.origin = None
        vals = self.get_input()
        button = vals.get("button")

        task = button["value"]
        if task == "fiducial":
            cal = get_cal(self.session.microscope)
            cal.fid70_go()
        elif task == "sample":
            self.sample()
        elif task == "collect":
            self.log("Sleeping. Clear the area!")
            self.sleep(5)
            self.collect(
                output_directory=vals.get("Output directory", ""),
                autofocus=vals.get("Autofocus") == "Yes",
            )
        elif task == "collect_quick":
            self.collect(output_directory=vals.get("Output directory", ""),
                         autofocus=vals.get("Autofocus") == "Yes",
                         quick=True)
        elif task == "analyze":
            analyze(in_dir=vals.get("Output directory", ""), log=self.log)
        else:
            assert 0, task


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run test")
    add_bool_arg(parser, "--show", default=False)
    parser.add_argument("--title")
    parser.add_argument("in_dir")
    args = parser.parse_args()

    analyze(in_dir=args.in_dir, show=args.show, title=args.title)


if __name__ == "__main__":
    main()
