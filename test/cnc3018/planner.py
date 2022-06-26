#!/usr/bin/env python3

import gi
gi.require_version('Gst', '1.0')

# Needed for window.get_xid(), xvimagesink.set_window_handle(), respectively:
# WARNING: importing GdkX11 will cause hard crash (related to Qt)
# fortunately its not needed
# from gi.repository import GdkX11, GstVideo
from gi.repository import Gst
Gst.init(None)
from gi.repository import GObject, GLib

from uscope.motion.grbl import GRBLSer, GRBL, GrblHal
from uscope.imager.imager import MockImager
from uscope.util import add_bool_arg
import uscope.planner
import shutil
import os
import json
from uscope.imager.imager import Imager
from uscope.gst_util import Gst, CaptureSink
import threading
import time

from .gst_imager import GstImager


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Planner module command line')
    parser.add_argument('--host',
                        default='mk',
                        help='Host.  Activates remote mode')
    parser.add_argument('--port', default=22617, type=int, help='Host port')
    parser.add_argument('--overwrite', action='store_true')
    add_bool_arg(parser,
                 '--verbose',
                 default=False,
                 help='Due to health hazard, default is True')
    add_bool_arg(parser,
                 '--dry',
                 default=True,
                 help='Due to health hazard, default is True')
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

    Gst.init(None)

    print("Connecting to CNC...")
    hal = GrblHal()

    print("Connecting to camera...")
    # imager = MockImager()
    imager = GstImager()

    print("starting pipeline")
    imager.player.set_state(Gst.State.PLAYING)

    print("Launching threads...")

    def planner_thread():
        print("Launching planner...")

        if 1:
            if imager.source_name == "gst-v4l2src":
                print("stabalizing camera")
                time.sleep(2)
            print("Getting image")
            im = imager.get()
            print("Got image")
            im["0"].save("gst_imager.jpg")
            loop.quit()

        if 0:
            # w, h in pix
            img_sz = (1500, 1000)
            mm_per_pix = 1 / 1000
            planner = uscope.planner.Planner(json.load(open(args.scan_json)),
                                             hal,
                                             imager=imager,
                                             img_sz=img_sz,
                                             unit_per_pix=mm_per_pix,
                                             out_dir=args.out,
                                             progress_cb=None,
                                             dry=args.dry,
                                             log=None,
                                             verbosity=2)
            planner.run()
            loop.quit()

    thread = threading.Thread(target=planner_thread)
    thread.start()
    loop = GLib.MainLoop()
    print("Running event loop")
    loop.run()


if __name__ == "__main__":
    main()
