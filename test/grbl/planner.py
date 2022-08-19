#!/usr/bin/env python3
"""
3018 CNC panoramic imaging demo
Uses grbl controller w/ touptek camera

sudo apt-get install -y python3-gst-1.0

Setup scan:

cat << EOF >scan.json
{
    "start": {
        "x": 0,
        "y": 0
    },
    "end": {
        "x": 2.0,
        "y": 1.5
    },
    "overlap": 0.7
}
EOF

./test/grbl/planner.py --gst-source videotestsrc --fov-w 456 --no-dry --overwrite scan.json out/

"""

from uscope.motion.grbl import GrblHal
from uscope.imager.imager import MockImager
from uscope.util import add_bool_arg
from uscope.imager import gst
import uscope.planner
import shutil
import os
import json
import threading
import time


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Planner module command line')
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    gst.gst_add_args(parser)
    parser.add_argument('--host',
                        default='mk',
                        help='Host.  Activates remote mode')
    parser.add_argument('--port', default=22617, type=int, help='Host port')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--fov-w',
                        type=float,
                        required=True,
                        help="field of view width in units (typically mm)")
    add_bool_arg(parser,
                 '--dry',
                 default=True,
                 help='Must be changed for real operation')
    parser.add_argument('scan_json',
                        nargs='?',
                        default='scan.json',
                        help='Scan parameters JSON')
    parser.add_argument('out',
                        nargs='?',
                        default='out/default',
                        help='Output directory')
    args = parser.parse_args()

    if os.path.exists(args.out):
        if not args.overwrite:
            raise Exception("Refusing to overwrite")
        shutil.rmtree(args.out)
    if not args.dry:
        os.mkdir(args.out)

    print("Connecting to CNC...")
    movement = GrblHal()

    print("Connecting to camera...")
    # imager = MockImager()
    imager = gst.GstImager(gst.gst_get_args(args))

    print("Launching threads...")

    def planner_thread(loop):
        print("Launching planner...")

        if 0:
            if imager.source_name == "gst-v4l2src":
                print("stabalizing camera")
                time.sleep(1)
            print("Getting image")
            im = imager.get()
            print("Got image")
            im["0"].save("gst_imager.jpg")

        if 1:
            mm_per_pix = args.fov_w / imager.wh()[0]
            # print("Imager %uw x %uh, w/ width = %0.3f mm => %0.06f mm per pix" % (imager.width, imager.height, args.fov_w, mm_per_pix))
            planner = uscope.planner.Planner(json.load(open(args.scan_json)),
                                             movement=movement,
                                             imager=imager,
                                             mm_per_pix=mm_per_pix,
                                             out_dir=args.out,
                                             progress_cb=None,
                                             dry=args.dry,
                                             log=None,
                                             origin="ll",
                                             verbosity=2)
            planner.run()
        loop.quit()

    gst.easy_run(imager, planner_thread)


if __name__ == "__main__":
    main()
