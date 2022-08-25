#!/usr/bin/env python3

from uscope.motion.plugins import get_motion_hal
from uscope.config import get_usj
from uscope.util import add_bool_arg
from uscope.imager import gst
import time
import argparse


def run(microscope=None,
        x=True,
        x_mag=10.0,
        y=True,
        y_mag=10.0,
        z=True,
        z_mag=10.0,
        image=True,
        image_freq=1,
        gst_args=None):
    usj = get_usj(name=microscope)
    print(usj)
    print("Initializing imager...")
    opts = gst.gst_usj_to_gstcliimager_args(usj=usj)
    print(opts)
    imager = gst.GstCLIImager(opts=opts)
    print("Initializing motion...")
    motion = get_motion_hal(usj)
    print(motion)
    print("System ready")

    motion.move_absolute({"x": 0.0, "y": 0.0, "z": 0.0})
    motion.move_absolute({"x": 10.0, "y": 10.0, "z": 10.0})
    motion.move_absolute({"x": 0.0, "y": 0.0, "z": 0.0})
    print("movement done")

    def thread(loop):
        if imager.source_name == "toupcamsrc":
            # gain takes a while to ramp up
            print("stabalizing camera")
            time.sleep(1)

        print("Getting image")
        im = imager.get()
        print("Got image")
        im = im["0"]
        print("Saving")
        im.save("out.jpg")
        print("done")
        loop.quit()

    gst.easy_run(imager, thread)
    print("Exiting")


def main():
    parser = argparse.ArgumentParser(
        description="Move testing axes using microscope.json configuration")
    parser.add_argument("--microscope", help="Select configuration directory")
    add_bool_arg(parser, "--x", default=True, help="Move X axis")
    parser.add_argument("--x-mag",
                        type=float,
                        help="Movement max range for origin")
    add_bool_arg(parser, "--y", default=True, help="Move Y axis")
    parser.add_argument("--y-mag",
                        type=float,
                        help="Movement max range for origin")
    add_bool_arg(parser, "--z", default=True, help="Move Z axis")
    parser.add_argument("--z-mag",
                        type=float,
                        help="Movement max range for origin")
    add_bool_arg(parser, "--image", default=True, help="Take images")
    parser.add_argument("--image-freq", help="Take images every N passes")
    gst.gst_add_args(parser)
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    run(microscope=args.microscope, gst_args=gst.gst_get_args(args))


if __name__ == "__main__":
    main()
