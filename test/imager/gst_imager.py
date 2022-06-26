#!/usr/bin/env python3
"""
GstImager (gstreamer wrapper) demo

================================================================================
Test source
================================================================================

Example:
./test/imager/gst_imager.py --source videotestsrc --width 456 --height 123 --gst-jpg out.jpg

Notes:
-width/height can be anything


================================================================================
v4l
================================================================================

Example:
./test/imager/gst_imager.py --source v4l2src --v4l2src-device /dev/video2 --width 640 --height 480 --gst-jpg out.jpg

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

./test/imager/gst_imager.py --source toupcamsrc --toupcamsrc-esize 2 --wh 800,600 --gst-jpg out.jpg

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
import uscope.imager.gst
import time


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="GstImager (gstreamer wrapper) demo")
    add_bool_arg(parser,
                 "--verbose",
                 default=False,
                 help="Due to health hazard, default is True")
    # FIXME: some issue with raw, keep default
    add_bool_arg(
        parser,
        "--gst-jpg",
        default=True,
        help="Capture jpg (as opposed to raw) using gstreamer encoder")
    add_bool_arg(parser, "--show", default=False, help="")
    parser.add_argument("--wh", default="640,480", help="Image width,height")
    parser.add_argument("--toupcamsrc-esize",
                        default=0,
                        type=int,
                        help="touptek esize. Must have correct width/height")
    parser.add_argument("--v4l2src-device", default=None, help="video device")
    parser.add_argument("--source",
                        default="videotestsrc",
                        help="videotestsrc, v4l2src, toupcamsrc")
    parser.add_argument("out", nargs="?", help="File to save to")
    args = parser.parse_args()

    width, height = args.wh.split(",")
    width = int(width)
    height = int(height)
    source_opts = {
        "width": width,
        "height": height,
        "gst_jpg": args.gst_jpg,
        "v4l2src": {
            "device": args.v4l2src_device,
        },
        "toupcamsrc": {
            "esize": args.toupcamsrc_esize,
        },
    }

    imager = uscope.imager.gst.GstImager(source_name=args.source,
                                         source_opts=source_opts)

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

    uscope.imager.gst.easy_run(imager, thread)


if __name__ == "__main__":
    main()
