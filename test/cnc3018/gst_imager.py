#!/usr/bin/env python3

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
    parser.add_argument("--width", default=640, type=int, help="")
    parser.add_argument("--height", default=480, type=int, help="")
    parser.add_argument("--touptek-esize",
                        default=0,
                        type=int,
                        help="touptek esize. Must have correct width/height")
    parser.add_argument("--source",
                        default="gst-videotestsrc",
                        help="gst-videotestsrc, gst-v4l2src, gst-toupcamsrc")
    parser.add_argument("out", nargs="?", help="File to save to")
    args = parser.parse_args()
    ''''
    """
    v4l2-ctl -d /dev/video0 --list-formats-ext
    """
    touptek_esize = source_opts.get("esize", 0)
    width = 640
    height = 480

    width = 1024
    height = 768

    if self.source_name == "gst-toupcamsrc":
        touptek_esize = 2
        width = 800
        height = 600

    if self.source_name == "gst-v4l2src":
        width = 1280
        height = 720
    '''

    source_opts = {
        "width": args.width,
        "height": args.height,
        "gst_jpg": args.gst_jpg,
        "touptek": {
            "esize": args.touptek_esize,
        }
    }

    imager = uscope.imager.gst.GstImager(source_name=args.source,
                                         source_opts=source_opts)

    def thread(loop):
        if imager.source_name == "gst-v4l2src":
            print("stabalizing camera")
            time.sleep(2)
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
