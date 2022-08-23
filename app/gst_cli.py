#!/usr/bin/env python3
"""
GstImager (gstreamer wrapper) demo

================================================================================
Test source
================================================================================

Example:
./app/gst_cli.py --gst-source videotestsrc --gst-wh 456,123 --gst-jpg out.jpg

Notes:
-width/height can be anything


================================================================================
v4l
================================================================================

Example:
./app/gst_cli.py --gst-source v4l2src --v4l2src-device /dev/video0 --gst-wh 640,480 --gst-jpg out.jpg

Notes:
-width/height must match a valid resolution

Get formats:
v4l2-ctl -d /dev/video2 --list-formats-ext

If you get:
Error: gst-stream-error-quark: Internal data stream error. (1) gstbasesrc.c(3072): gst_base_src_loop (): /GstPipeline:player/GstV4l2Src:v4l2src0:
streaming stopped, reason not-negotiated (-4)
You may have selected an invalid resolution


================================================================================
touptek
================================================================================

Example:

./app/gst_cli.py --gst-source toupcamsrc --toupcamsrc-esize 2 --gst-wh 800,600 --gst-jpg out.jpg

Notes:
-width/height must match a valid resolution and the provided esize

Get formats:
use touplite GUI
"esize" starts from 0 and is each of the resolutions in order
TODO: find a way to list on CLI

esize quick reference

ToupTek UCMOS08000KPB / AmScope MU800
0: 3264, 2448
1: 1600, 1200
2: 800, 600
"""

from uscope.util import add_bool_arg
from uscope.imager import gst
import time


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="GstImager (gstreamer wrapper) demo")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    gst.gst_add_args(parser)
    parser.add_argument("out", nargs="?", help="File to save to")
    args = parser.parse_args()

    imager = gst.GstCLIImager(gst.gst_get_args(args))

    def thread(loop):
        if imager.source_name == "toupcamsrc":
            # gain takes a while to ramp up
            print("stabalizing camera")
            time.sleep(1)
        print("Getting image")
        im = imager.get()
        print("Got image")
        im = im["0"]
        if args.out:
            print("Saving to %s" % args.out)
            # Should be im object either way
            # but IIRC there was a decode issue
            # so hack for now passing around raw buf
            if args.gst_jpg:
                im.save(args.out)
            else:
                open(args.out, "wb").write(im)
        loop.quit()

    gst.easy_run(imager, thread)


if __name__ == "__main__":
    main()
