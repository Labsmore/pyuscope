import gi
import copy

gi.require_version('Gst', '1.0')

# Needed for window.get_xid(), xvimagesink.set_window_handle(), respectively:
# WARNING: importing GdkX11 will cause hard crash (related to Qt)
# fortunately its not needed
# from gi.repository import GdkX11, GstVideo
from gi.repository import Gst

Gst.init(None)
from gi.repository import GLib

from uscope.imager.imager import Imager
from uscope.imager.config import default_gstimager_config
from uscope.gst_util import CaptureSink
from uscope.util import add_bool_arg
from uscope import config
import threading
import time


class ImageTimeout(Exception):
    pass


class GstCLIImager(Imager):
    """
    Stand alone imager that doesn't rely on GUI feed
    """
    def __init__(self, opts={}, verbose=False, microscope=None):
        Imager.__init__(self, verbose=verbose)
        self.microscope = microscope
        self.image_ready = threading.Event()
        self.image_id = None
        opts = copy.deepcopy(opts)

        # Fill in extended configuration parameters
        # Ex: source to use
        default_gstimager_config(opts)

        # gst source name
        # does not have gst- prefix
        source_name = opts["source"]
        self.source_name = source_name

        self.width, self.height = opts.get("wh", (640, 480))
        self.gst_jpg = opts.get("gst_jpg", True)

        self.player = Gst.Pipeline.new("player")

        self.prepareSource(opts)
        self.player.add(self.source)

        self.raw_capsfilter = Gst.ElementFactory.make("capsfilter")
        assert self.raw_capsfilter is not None
        self.raw_capsfilter.props.caps = Gst.Caps(
            "video/x-raw,width=%u,height=%u" % (self.width, self.height))
        self.player.add(self.raw_capsfilter)
        if not self.source.link(self.raw_capsfilter):
            raise RuntimeError("Failed to link")

        self.videoconvert = Gst.ElementFactory.make('videoconvert')
        assert self.videoconvert is not None
        self.player.add(self.videoconvert)
        if not self.raw_capsfilter.link(self.videoconvert):
            raise RuntimeError("Failed to link")

        if self.gst_jpg:
            self.jpegenc = Gst.ElementFactory.make("jpegenc")
            self.player.add(self.jpegenc)
            if not self.videoconvert.link(self.jpegenc):
                raise RuntimeError("Failed to link")
        else:
            self.jpegenc = None

        self.capture_sink = CaptureSink(width=self.width,
                                        height=self.height,
                                        raw_input=not self.gst_jpg)
        assert self.capture_sink is not None
        self.player.add(self.capture_sink)
        if self.jpegenc:
            if not self.jpegenc.link(self.capture_sink):
                raise RuntimeError("Failed to link")
        else:
            if not self.videoconvert.link(self.capture_sink):
                raise RuntimeError("Failed to link")

        self.warm_up_time = opts.get("warm_up_time")
        if self.warm_up_time is None:
            # XXX hack: can we push this down the stack?
            if self.source_name == "toupcamsrc":
                # gain takes a while to ramp up
                # print("stabalizing camera")
                self.warm_up_time = 1.0
            else:
                self.warm_up_time = 0.0

        # Ideally related to max exposure
        # 1.0 second is not enough for dark image
        self.frame_timeout = 2.0

    def __del__(self):
        self.stop()

    def get_sn(self):
        return self.source.get_property("serial-number")

    def wh(self):
        return self.width, self.height

    def prepareSource(self, source_opts={}):
        # Must not be initialized until after layout is set
        # print(source)
        # assert 0
        if self.source_name in ("v4l2src", "v4l2src-mu800"):
            self.source = Gst.ElementFactory.make('v4l2src', None)
            assert self.source is not None
            device = source_opts.get("v4l2src",
                                     {}).get("device", "/dev/video0")
            self.source.set_property("device", device)
        elif self.source_name == "toupcamsrc":
            self.source = Gst.ElementFactory.make('toupcamsrc', None)
            assert self.source is not None, "Failed to load toupcamsrc. Is it in the path?"
            touptek_esize = source_opts.get("toupcamsrc",
                                            {}).get("esize", None)
            if touptek_esize is not None:
                self.source.set_property("esize", touptek_esize)
        elif self.source_name in ("libcamerasrc"):
            self.source = Gst.ElementFactory.make('libcamerasrc', None)
            assert self.source is not None
        elif self.source_name == "videotestsrc":
            # print('WARNING: using test source')
            self.source = Gst.ElementFactory.make('videotestsrc', None)
        else:
            raise Exception('Unknown source %s' % (self.source_name, ))
        assert self.source is not None

    def get(self):
        def got_image(image_id):
            self.verbose and print('Image captured reported: %s' % image_id)
            self.image_id = image_id
            self.image_ready.set()

        self.image_id = None
        self.image_ready.clear()
        self.capture_sink.request_image(got_image)
        self.verbose and print('Waiting for next image...')
        self.image_ready.wait(timeout=self.frame_timeout)
        self.verbose and print('Got image %s' % self.image_id)
        if self.image_id is None:
            raise ImageTimeout()
        img = self.capture_sink.pop_image(self.image_id)
        return {"0": img}

    def warm_up(self):
        """
        Called by Planner() to eat a few images
        """
        time.sleep(self.warm_up_time)

    def stop(self):
        if self.player:
            self.player.set_state(Gst.State.NULL)
            self.player = None

    def gst_run(self, target, verbose=False):
        """
        Given a GstImager, start gstreamer pipeline and start a thread for function "target"
        """

        errors = []

        # Gst.Pipeline
        # https://lazka.github.io/pgi-docs/Gst-1.0/classes/Pipeline.html
        # https://lazka.github.io/pgi-docs/Gst-1.0/classes/Element.html#Gst.Element.set_state
        self.player.set_state(Gst.State.PLAYING)
        loop = GLib.MainLoop()
        thread_go = threading.Event()
        loop_done = threading.Event()

        def shutdown():
            loop_done.set()
            thread_go.set()
            loop.quit()

        def on_message(bus, message):
            t = message.type

            verbose and print("on_message", message, t)
            if t == Gst.MessageType.EOS:
                self.player.set_state(Gst.State.NULL)
                shutdown()
            elif t == Gst.MessageType.ERROR:
                err, debug = message.parse_error()
                verbose and print("Error: %s" % err, debug)
                self.player.set_state(Gst.State.NULL)
                errors.append(err)
                shutdown()
            elif t == Gst.MessageType.STATE_CHANGED:
                pass
            elif t == Gst.MessageType.STREAM_START:
                thread_go.set()

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", on_message)

        # bus.connect("sync-message::element", on_sync_message)

        def wrapper(args):
            verbose and print("wrap start, waiting for ready")
            thread_go.wait(timeout=1.0)
            if loop_done.is_set():
                verbose and print("skipping thread on bad start")
                return
            verbose and print("wrap ready, go go go")
            try:
                target(loop)
            finally:
                # XXX: must be thread safe?
                loop.quit()
                pass

        thread = threading.Thread(target=wrapper, args=(loop, ))
        thread.start()
        loop.run()
        verbose and print("loop done, joining")
        # In case of error shut down quickly
        shutdown()
        thread.join(timeout=1.0)
        verbose and print("thread joined")

        if len(errors):
            raise GstError(errors[0])


def gst_add_args(parser):
    """
    Consumed in two ways:
    -CLI app directly
    -Converted to usj for simple apps
    """

    # FIXME: some issue with raw, keep default
    add_bool_arg(
        parser,
        "--gst-jpg",
        default=True,
        help="Capture jpg (as opposed to raw) using gstreamer encoder")
    # ??? what was this
    # add_bool_arg(parser, "--show", default=False, help="FIXME remove?")
    parser.add_argument("--gst-wh",
                        default="640,480",
                        help="Image width,height")
    parser.add_argument("--toupcamsrc-esize",
                        default=0,
                        type=int,
                        help="touptek esize. Must have correct width/height")
    parser.add_argument("--v4l2src-device", default=None, help="video device")
    parser.add_argument("--gst-source",
                        default="videotestsrc",
                        help="videotestsrc, v4l2src, toupcamsrc")
    parser.add_argument("--gst-crop", default="", help="top,bottom,left,right")


def gstcliimager_args_to_usj(args):
    """
    Convert GstCLIImager's gst_add_args() args result to usj["imager"] section


    "imager": {
        "engine":"gst-testsrc",
        "width": 800,
        "height": 600,
        "scalar": 0.5
    },
    "imager": {
        "source":"gst-v4l2src",
        "width": 1280,
        "height": 720,
        "source_properties": {
            "device": "/dev/video1"
        },
        "scalar": 0.5
    },
    "imager": {
        "source":"gst-toupcamsrc",
        "width": 5440,
        "height": 3648,
        "!source_properties": {
            "esize": 0
        },
        "scalar": 0.5
    },
    """
    imager = {
        "source_properties": {},
    }
    imager["source"] = "gst-" + args["gst_source"]
    w, h = args["gst_wh"].split(",")
    imager["width"], imager["height"] = int(w), int(h)
    if args["gst_crop"]:
        top, bottom, left, right = args["gst_crop"].split(",")
        imager["crop"] = {
            "top": int(top),
            "bottom": int(bottom),
            "left": int(left),
            "right": int(right),
        }
    if imager["source"] == "gst-v4l2src":
        imager["source_properties"]["device"] = args["v4l2src_device"]
    if imager["source"] == "gst-toupcamsrc":
        imager["source_properties"]["esize"] = args["toupcamsrc_esize"]
    return imager


def gst_usj_to_gstcliimager_args(imager=None, usj=None):
    """
    Convert usj's imager section to GstCLIImager's args
    """
    if imager is None:
        imager = usj["imager"]
    source = imager["source"]
    if not "gst-" in source:
        raise Exception("require gst- source")
    source = source.replace("gst-", "")
    args = {
        "source": source,
        "wh": (imager["width"], imager["height"]),
    }
    source_properties = imager.get("source_properties", {})
    if imager["source"] == "v4l2src":
        device = source_properties.get("device")
        if device:
            args["v4l2src_device"] = device
    if imager["source"] == "toupcamsrc":
        esize = source_properties.get("esize")
        if esize:
            args["toupcamsrc_esize"] = esize

    return args


def gst_get_args(args):
    width, height = args.gst_wh.split(",")
    width = int(width)
    height = int(height)
    source_opts = {
        "source": args.gst_source,
        "wh": (width, height),
        "gst_jpg": args.gst_jpg,
        "v4l2src": {
            "device": args.v4l2src_device,
        },
        "toupcamsrc": {
            "esize": args.toupcamsrc_esize,
        },
    }
    return source_opts


class GstError(Exception):
    pass


def apply_imager_cal(imager, verbose=False):
    usj_source = "gst-" + imager.source_name
    properties = config.cal_load(source=usj_source)
    for propk, propv in properties.items():
        verbose and print("Set source %s => %s" % (propk, propv))
        imager.source.set_property(propk, propv)


def get_cli_imager_by_config(usj=None, verbose=False, microscope=None):
    if usj is None:
        usj = config.get_usj()
    opts = gst_usj_to_gstcliimager_args(usj=usj)
    imager = GstCLIImager(opts=opts, microscope=microscope)
    apply_imager_cal(imager, verbose=verbose)
    return imager
