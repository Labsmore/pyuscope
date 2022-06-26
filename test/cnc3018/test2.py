#!/usr/bin/env python3

from uscope.motion.grbl import GRBLSer, GRBL, GrblHal
from uscope.imager.imager import MockImager
from uscope.util import add_bool_arg
import uscope.planner
import shutil
import os
import json
from uscope.imager.imager import Imager
from uscope.gst_util import Gst, CaptureSink


class GstImager(Imager):
    def __init__(self, source_name=None, verbose=False):
        Imager.__init__(self)
        self.image_id = None
        if source_name is None:
            source_name = "gst-videotestsrc"
        self.source_name = source_name
        """
        v4l2-ctl -d /dev/video0 --list-formats-ext
        """
        touptek_esize = 0
        width = 640
        height = 480

        self.player = Gst.Pipeline.new("player")

        self.prepareSource(touptek_esize=touptek_esize)
        self.player.add(self.source)

        self.raw_capsfilter = Gst.ElementFactory.make("capsfilter")
        assert self.raw_capsfilter is not None
        self.raw_capsfilter.props.caps = Gst.Caps(
            "video/x-raw,width=%u,height=%u" % (width, height))
        self.player.add(self.raw_capsfilter)
        if not self.source.link(self.raw_capsfilter):
            raise RuntimeError("Failed to link")

        self.videoconvert = Gst.ElementFactory.make('videoconvert')
        assert self.videoconvert is not None
        self.player.add(self.videoconvert)
        if not self.raw_capsfilter.link(self.videoconvert):
            raise RuntimeError("Failed to link")

        self.capture_sink = CaptureSink(width=width,
                                        height=height,
                                        raw_input=True)
        assert self.capture_sink is not None
        self.player.add(self.capture_sink)
        if not self.videoconvert.link(self.capture_sink):
            raise RuntimeError("Failed to link")

    def prepareSource(self, touptek_esize=None):
        # Must not be initialized until after layout is set
        # print(source)
        # assert 0
        if self.source_name in ('gst-v4l2src', 'gst-v4l2src-mu800'):
            self.source = Gst.ElementFactory.make('v4l2src', None)
            assert self.source is not None
            self.source.set_property("device", "/dev/video0")
        elif self.source_name == 'gst-toupcamsrc':
            self.source = Gst.ElementFactory.make('toupcamsrc', None)
            assert self.source is not None, "Failed to load toupcamsrc. Is it in the path?"
            if touptek_esize is not None:
                self.source.set_property("esize", touptek_esize)
        elif self.source_name == 'gst-videotestsrc':
            print('WARNING: using test source')
            self.source = Gst.ElementFactory.make('videotestsrc', None)
        else:
            raise Exception('Unknown source %s' % (self.source_name, ))
        assert self.source is not None
        """
        if self.usj:
            usj = config.get_usj()
            properties = usj["imager"].get("source_properties", {})
            for propk, propv in properties.items():
                print("Set source %s => %s" % (propk, propv))
                self.source.set_property(propk, propv)
        """

    def get(self):
        def got_image(image_id):
            print('Image captured reported: %s' % image_id)
            self.image_id = image_id
            self.image_ready.set()

        self.image_id = None
        self.image_ready.clear()
        self.capture_sink.request_image(got_image)
        print('Waiting for next image...')
        self.image_ready.wait()
        print('Got image %s' % self.image_id)
        img = self.capture_sink.pop_image(self.image_id)
        return {"0": img}


def main1():
    imager = GstImager()
    print("Getting image")
    _im = imager.get()
    print("Got image")


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

    imager = MockImager()
    hal = GrblHal()

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


if __name__ == "__main__":
    main()
