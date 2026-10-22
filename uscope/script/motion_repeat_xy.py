#!/usr/bin/env python3

import matplotlib.pyplot as plt

from uscope.gui.scripting import ArgusScriptingPlugin
from uscope.calibration import find_centroid, find_centroid_fn, get_cal
from uscope.util import utcnow, add_bool_arg, datetime_file_str, LogToFile

import numpy as np
import glob
import json
import os
import random
import time


def savej(j, out_dir):
    open("%s/log.json" % (out_dir, ), "w").write(
        json.dumps(j, sort_keys=True, indent=4, separators=(',', ': ')))


def distance(p1, p2):
    return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)**0.5


def get_positions(in_dir, adict):
    ret = {}
    for stepk, stepv in adict.items():
        fn = in_dir + "/" + stepv["fn"]
        # self.log("Processing %s..." % fn)
        x, y = find_centroid_fn(fn)
        ret[stepv["fn"]] = (x, y)
    return ret


def median_pos(poss):
    xs = []
    ys = []
    for (x, y) in poss:
        xs.append(x)
        ys.append(y)
    return sorted(xs)[len(xs) // 2], sorted(ys)[len(ys) // 2]


def analyze(in_dir=None,
            show=False,
            title=None,
            log=None,
            microscope=None,
            verbose=False):
    if log is None:
        log = print
    if not in_dir:
        in_dir = sorted(glob.glob("data/script/*repeat-xy_*"))[-1]

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
            scalar_pos = median_pos(scalar_poss.values())
            steps_poss = get_positions(in_dir, axisv["steps"])
            steps_pos = median_pos(steps_poss.values())
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
            pix_per_um = 1 / um_per_pix
            for stepk, stepv in enumerate(axisv["moves"]):
                fn = in_dir + "/" + stepv["fn"]
                # self.log("Processing %s..." % fn)
                x, y = find_centroid_fn(fn)
                x *= um_per_pix
                y *= um_per_pix
                xs.append(x)
                ys.append(y)

            origin_x = sum(xs) / len(xs)
            origin_y = sum(ys) / len(ys)
            origin = (origin_x, origin_y)
            log("Origin: x=%0.3f um (%u pix) y=%0.3f um (%u pix)" %
                (origin_x, origin_x * pix_per_um, origin_y,
                 origin_y * pix_per_um))

            dxs = []
            dys = []
            ds = []
            for stepk, (x, y) in enumerate(zip(xs, ys)):
                dx = x - origin_x
                dy = y - origin_y
                d = distance(origin, (x, y))
                dxs.append(dx)
                dys.append(dy)
                ds.append(d)
                verbose and log("  Point % 3u: %0.3f w/ dx=%0.3f, dy=%0.3f" %
                                (stepk, d, dx, dy))

            log("X")
            dxs_median = np.median(dxs)
            dxs_std = np.std(dxs)
            dxs_max = np.max(dxs)
            log("  Median: %0.3f um" % dxs_median)
            log("  Standard deviation: %0.3f um" % dxs_std)
            log("  Max: %0.3f um" % dxs_max)
            results["dxs"] = {
                "values": dxs,
                "median": dxs_median,
                "std": dxs_std,
                "max": dxs_max,
            }

            log("Y")
            dys_median = np.median(dxs)
            dys_std = np.std(dxs)
            dys_max = np.max(dxs)
            log("  Median: %0.3f um" % dys_median)
            log("  Standard deviation: %0.3f um" % dys_std)
            log("  Max: %0.3f um" % dys_max)
            results["dys"] = {
                "values": dys,
                "median": dys_median,
                "std": dys_std,
                "max": dys_max,
            }

            log("XY")
            ds_median = np.median(ds)
            ds_std = np.std(ds)
            ds_max = np.max(ds)
            log("  Median: %0.3f um" % ds_median)
            log("  Standard deviation: %0.3f um" % ds_std)
            log("  Max: %0.3f um" % ds_max)
            results["ds"] = {
                "values": ds,
                "median": ds_median,
                "std": ds_std,
                "max": ds_max,
            }

            pj[axis] = results

            plt.clf()

            plt.plot(results["ds"]["values"])
            plt.ylabel("Median offset (um)")
            plt.xlabel("Iteration")
            if title:
                plt.title(title + f" ({axis})")
            plt.savefig("%s/processed_%s.png" % (in_dir, axis))
            if show:
                plt.show()

        open("%s/processed.json" % (in_dir, ), "w").write(
            json.dumps(pj, sort_keys=True, indent=4, separators=(',', ': ')))

        log("")
        log("")
        log("")
        log("Summary")
        log("X (with respect to XY)")
        log("  Median: %0.3f um" % pj["x"]["ds"]["median"])
        log("  Standard deviation: %0.3f um" % pj["x"]["ds"]["std"])
        log("  Max: %0.3f um" % pj["x"]["ds"]["max"])
        log("Y (with respect to XY)")
        log("  Median: %0.3f um" % pj["y"]["ds"]["median"])
        log("  Standard deviation: %0.3f um" % pj["y"]["ds"]["std"])
        log("  Max: %0.3f um" % pj["y"]["ds"]["max"])


class Plugin(ArgusScriptingPlugin):
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

    def run_axis(self, axis, backlash, moves, quick=False):
        axj = {}
        axj["moves"] = []
        axj["n_moves"] = moves
        self.log("")
        self.log("Testing axis %s" % axis)

        axis_out_dir = self.output_directory + "/" + axis
        if not os.path.exists(axis_out_dir):
            os.mkdir(axis_out_dir)

        def save_image(basename):
            im = self.image()
            fn = "%s/%s" % (self.output_directory, basename)
            # print("Saving %s" % fn)
            # should be .tif?
            im.save(fn, quality=95)

        '''
        def run_scalar():
            # Do a series of moves with known magnitude
            j_ref_scalar = {"mag": ref_scalar, "steps": {}}
            j["axes"][loop_axis]["ref_move"] = j_ref_scalar
            # Take median of 1 3 or 5
            # maybe more to scale with other sample size
            scalar_moves = 3
            for step in range(scalar_moves):
                # Move far away
                self.move_relative({axis: ref_scalar + backlash})
                # Complete backlash compensation
                self.move_relative({axis: -backlash})
                basename = "%s/scalar_%02u.tif" % (loop_axis, step)
                j_ref_scalar["steps"][step] = {"fn": basename}
                save_image(basename)
                # Complete move back
                self.move_relative({axis: -ref_scalar})
                savej(j, out_dir)
        '''

        def run_main():
            for movei in range(moves):
                this_move = backlash + self.max_random_offset * random.uniform(
                    0.0, 1.0)
                self.log("%s move %u / %u: +%0.3f" %
                         (axis, movei + 1, moves, this_move))
                self.log(utcnow().isoformat())
                # FIXME: assumes negative backlash correction
                self.move_relative({axis: +this_move})
                self.move_absolute({axis: self.origin[axis]})

                if not quick:
                    self.sleep(0.2)

                # Record image to post process
                # self.log("Getting image")
                basename = "%s/%003u.jpg" % (axis, movei)
                save_image(basename)

                axj["moves"].append({
                    "fn": basename,
                    "move": this_move,
                })
                # Keep saving in case crash during long collect
                # savej(axj, axis_out_dir)
            self.log("Pass done")

        #run_scalar()
        run_main()
        return axj

    def collect(self,
                output_directory,
                overwrite=False,
                axes={"x", "y"},
                origin=None,
                autofocus=False,
                quick=False):

        self.log(f"motion_repeat_xy, quick={quick}")
        if quick:
            # these could probably be 0 since most systems are already backlash compensated
            x_backlash = 0.1
            y_backlash = 0.1
            # ref_scalar=None,
            x_moves = 10
            y_moves = 10
        else:
            # these could probably be 0 since most systems are already backlash compensated
            x_backlash = 0.1
            y_backlash = 0.1
            # ref_scalar=None,
            x_moves = 100
            y_moves = 100

        self.output_directory = str(output_directory)
        self.max_random_offset = 1.0

        if not self.output_directory:
            dt = datetime_file_str()
            model = self.microscope_model()
            sn = self.microscope_serial()
            sn_str = ""
            if sn:
                sn_str = f"_sn-{sn}"
            self.output_directory = f"data/script/{dt}_repeat-xy_model-{model}{sn_str}"

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

        #if ref_scalar is None:
        #    ref_scalar = backlash * 2

        j = {
            "test": "repeat_xy",
            "microscope_model": self.microscope_model(),
            "microscope_serial": self.microscope_serial(),
            "autofocus": autofocus,
            "quick": quick,
            "x_moves": x_moves,
            "y_moves": y_moves,
            "x_backlash": x_backlash,
            "y_backlash": y_backlash,
            "max_random_offset": self.max_random_offset,
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
            backlash = {
                "x": x_backlash,
                "y": y_backlash,
            }[axis]
            j["axes"][axis] = self.run_axis(axis=axis,
                                            backlash=backlash,
                                            moves=moves,
                                            quick=quick)
            save_current()

        j["seconds"] = int(time.time() - tstart)
        self.log("Main loop done")
        save_current()

    def cleanup(self):
        if self.origin is not None:
            self.move_absolute(self.origin)

    def run_test(self):
        self.origin = None
        vals = self.get_input()
        button = vals.get("button")

        task = button["value"]
        if task == "fiducial":
            cal = get_cal(self._ac.microscope)
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
