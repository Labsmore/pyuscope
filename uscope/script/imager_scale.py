#!/usr/bin/env python3

import matplotlib.pyplot as plt

from uscope.scripting import ScriptingPlugin
from uscope.microscope import get_virtual_microscope, get_mconfig
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


def mean_without_outliers(vals):
    vals = sorted(vals)
    # throw out lower and upper 25%
    vals = vals[len(vals) // 4:3 * len(vals) // 4]
    return np.mean(vals)


def analyze(in_dir=None,
            show=False,
            title=None,
            log=None,
            microscope=None,
            verbose=False):
    if log is None:
        log = print
    if not in_dir:
        in_dir = sorted(glob.glob("data/script/*imager_scale_*"))[-1]

    log("Loading from %s" % in_dir)
    with LogToFile(os.path.join(in_dir, "analysis.txt"), log=log) as log:
        j = json.load(open(in_dir + "/log.json"))

        model = j["microscope_model"]
        serial = j["microscope_serial"]
        if not title and model and serial:
            title = f"{model} S/N {serial}"

        # output
        pj = {
            "axes": {},
        }
        um_per_pix_old = j["objective"]["um_per_pixel"]

        for axis, axisj in j["axes"].items():
            log("Processing axis " + str(axis))
            delta_um = j["delta_mm"] * 1000
            xs = []
            ys_pix = []
            ys_um_per_pix_old = []
            ys_um_per_pix_new = []
            delta_pixs = []
            # pix_per_um = 1 / um_per_pix
            for movei, movej in enumerate(axisj["moves"]):
                xs.append(movei)
                fn_plus = os.path.join(in_dir, movej["filename_plus"])
                x_plus, y_plus = find_centroid_fn(fn_plus)
                fn_minus = os.path.join(in_dir, movej["filename_minus"])
                x_minus, y_minus = find_centroid_fn(fn_minus)
                """
                if the sensor is misaligned, its more accurate to use the full delta
                """
                dx = x_plus - x_minus
                dy = y_plus - y_minus
                delta_pix = (dx**2 + dy**2)**0.5
                delta_pixs.append(delta_pix)
                um_per_pix_new = delta_um / delta_pix

                ys_pix.append(delta_pix)
                ys_um_per_pix_old.append(um_per_pix_old)
                ys_um_per_pix_new.append(um_per_pix_new)

            plt.clf()

            um_per_pix_new = mean_without_outliers(ys_um_per_pix_new)

            pj["axes"][axis] = {
                "delta_pixs": delta_pixs,
                "um_per_pix_new": um_per_pix_new,
                "ys_um_per_pix_new": ys_um_per_pix_new,
            }
            plt.plot(xs, ys_um_per_pix_old)
            plt.plot(xs, ys_um_per_pix_new)
            plt.xlabel("Step number")
            plt.ylabel("um_per_pixel")
            if not title:
                title = ""
            plt.title(
                title +
                f" ({axis})\num_per_pix_old: %0.3f um\num_per_pix_new: %0.3f um"
                % (um_per_pix_old, um_per_pix_new))
            plt.savefig("%s/processed_%s.png" % (in_dir, axis))
            if show:
                plt.show()

        microscope = get_virtual_microscope(mconfig=get_mconfig(
            name=j["microscope_model"], serial=j["microscope_serial"]))
        # < 1 if using a smaller resolution than full
        imagerj = j.get("imager")
        if imagerj:
            # Resolution the images were taken at
            hardware_resolution_scalar = imagerj["raw_wh"][0] / imagerj[
                "native_wh"][0]
        else:
            hardware_resolution_scalar = microscope.usc.imager.hardware_resolution_scalar(
            )
        # < 1 if downscaling to final image
        soft_image_scalar = microscope.usc.imager.scalar()
        native_pixel_size = microscope.usc.imager.native_pixel_pitch_um()

        ideal_sel_pixel_size_1x = native_pixel_size / (
            hardware_resolution_scalar * soft_image_scalar)
        calc_mag_x = ideal_sel_pixel_size_1x / pj["axes"]["x"]["um_per_pix_new"]
        calc_mag_y = ideal_sel_pixel_size_1x / pj["axes"]["y"]["um_per_pix_new"]

        old_um_per_pixel_raw_1x = microscope.usc.optics.um_per_pixel_native_1x(
        ) / hardware_resolution_scalar
        obj_um_per_pixel_new = (pj["axes"]["x"]["um_per_pix_new"] +
                                pj["axes"]["y"]["um_per_pix_new"]) / 2
        new_um_per_pixel_1x = obj_um_per_pixel_new * j["objective"][
            "magnification"]
        # Raw pixels may be smaller than we received
        new_um_per_pixel_raw_1x = new_um_per_pixel_1x * soft_image_scalar
        # Native pixels may also be smaller
        new_um_per_pixel_native_1x = new_um_per_pixel_raw_1x * hardware_resolution_scalar

        pj["old_um_per_pixel_raw_1x"] = old_um_per_pixel_raw_1x
        pj["obj_um_per_pixel_new"] = obj_um_per_pixel_new
        pj["new_um_per_pixel_1x"] = new_um_per_pixel_1x
        pj["new_um_per_pixel_raw_1x"] = new_um_per_pixel_raw_1x
        pj["new_um_per_pixel_native_1x"] = new_um_per_pixel_native_1x

        open("%s/processed.json" % (in_dir, ), "w").write(
            json.dumps(pj, sort_keys=True, indent=4, separators=(',', ': ')))


        log("Scale summary")
        log("1X per pixel")
        log("  new_um_per_pixel_sel: %0.3f" % new_um_per_pixel_1x)
        log("  new_um_per_pixel_raw: %0.3f" % new_um_per_pixel_raw_1x)
        log("    Old: %0.3f" % old_um_per_pixel_raw_1x)
        log("  new_um_per_pixel_native: %0.3f" % new_um_per_pixel_native_1x)
        log("    Pixel pitch: %0.3f um" % native_pixel_size)
        log("This objective")
        log("  Objective: %uX (%s)" %
            (j["objective"]["magnification"], j["objective"]["name"]))
        log("    Calculated mag X: %0.2fX" % calc_mag_x)
        log("    Calculated mag Y: %0.2fX" % calc_mag_y)
        log("  um_per_pixel")
        log("    New: %0.3f" % obj_um_per_pixel_new)
        log("      Old: %0.3f um" % (um_per_pix_old, ))
        log("    New X: %0.3f um" % (pj["axes"]["x"]["um_per_pix_new"], ))
        log("    New Y: %0.3f um" % (pj["axes"]["y"]["um_per_pix_new"], ))
        log("Imager info")
        log("  Native pixel size: %0.3f um" % native_pixel_size)
        log("  Processed pixel size 1X: %0.3f um" % ideal_sel_pixel_size_1x)
        # Image comes off of camera and then we resize
        log("  Soft scalar: %0.3fX" % soft_image_scalar)
        # Difference between native resolution and selected resolution
        log("  Hard scalar: %0.3fX" % hardware_resolution_scalar)
    return pj


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

    def collect(
        self,
        output_directory,
        overwrite=False,
        origin=None,
        autofocus=False,
        quick=False,
    ):
        self.output_directory = str(output_directory)
        objective_config = self.get_objective_config()

        self.log(f"motion_scale, quick={quick}")
        self.log("Using objective %s" % objective_config["name"])
        magnification = objective_config["magnification"]

        if quick:
            moves = 10
        else:
            moves = 50
        # Cover about 1/3 of the FoV
        # Generally center optics are most accurate
        # some sensors are wider than tall, bias to x
        # will break down though if completely out of field...
        plus_minus = objective_config["x_view"] * 0.15
        self.log("+/- %0.3f mm" % plus_minus)

        if not self.output_directory:
            dt = datetime_file_str()
            model = self.microscope_model()
            sn = self.microscope_serial()
            sn_str = ""
            if sn:
                sn_str = f"_sn-{sn}"
            self.output_directory = f"data/script/{dt}_imager_scale_model-{model}{sn_str}_mag-{magnification}"

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
        self.log("origin " + str(self.origin))

        if autofocus:
            self.autofocus()

        axes = "xy"
        j = {
            "test": "imager_scale",
            "microscope_model": self.microscope_model(),
            "microscope_serial": self.microscope_serial(),
            "autofocus": autofocus,
            "quick": quick,
            "n_moves": moves,
            "origin": self.origin,
            "objective": objective_config,
            "imager": {
                "raw_wh": list(self.session.microscope.usc.imager.raw_wh()),
                "native_wh":
                list(self.session.microscope.usc.imager.native_wh()),
            },
            "plus_minus_mm": plus_minus,
            "delta_mm": plus_minus * 2,
            "axes": {},
        }

        def save_current():
            savej(j, self.output_directory)

        save_current()

        def save_image(basename):
            im = self.image()
            fn = os.path.join(self.output_directory, basename)
            # should be .tif?
            im.save(fn, quality=95)

        for axis in axes:
            axisj = {
                "axis": axis,
                "plus_minus_mm": plus_minus,
                "delta_mm": plus_minus * 2,
                "moves": [],
            }

            self.log("Testing axis %s" % axis)

            axis_out_dir = self.output_directory + "/" + axis
            if not os.path.exists(axis_out_dir):
                os.mkdir(axis_out_dir)

            self.move_absolute(self.origin)
            for movei in range(moves):
                self.log("%s move %u / %u" % (axis, movei + 1, moves))
                self.move_absolute({axis: self.origin[axis] + plus_minus})
                if not quick:
                    self.sleep(0.5)
                basename_plus = "%s/%003u_plus.jpg" % (axis, movei)
                save_image(basename_plus)

                self.move_absolute({axis: self.origin[axis] - plus_minus})
                if not quick:
                    self.sleep(0.5)
                basename_minus = "%s/%003u_minus.jpg" % (axis, movei)
                save_image(basename_minus)

                axisj["moves"].append({
                    "filename_plus": basename_plus,
                    "filename_minus": basename_minus,
                    "move": movei,
                })
            j["axes"][axis] = axisj

        self.log("Pass done")

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
            pj = analyze(in_dir=vals.get("Output directory", ""), log=self.log)
            # Resolution may have changed since the images were taken
            self.set_um_per_pixel_raw_1x(
                pj["new_um_per_pixel_native_1x"] /
                self.session.microscope.usc.imager.hardware_resolution_scalar())
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
