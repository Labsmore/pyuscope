import gi
gi.require_version('Gst', '1.0')

# Needed for window.get_xid(), xvimagesink.set_window_handle(), respectively:
# WARNING: importing GdkX11 will cause hard crash (related to Qt)
# fortunately its not needed
# from gi.repository import GdkX11, GstVideo
from gi.repository import Gst
Gst.init(None)
from gi.repository import GLib

from uscope.imager.imager import Imager
from uscope.gst_util import CaptureSink
import threading


class GstImager(Imager):
    def __init__(self, source_name=None, source_opts={}, verbose=False):
        Imager.__init__(self)
        self.image_ready = threading.Event()
        self.image_id = None
        if source_name is None:
            source_name = "videotestsrc"
        self.source_name = source_name

        self.width = source_opts.get("width", 640)
        self.height = source_opts.get("height", 480)
        self.gst_jpg = source_opts.get("gst_jpg", True)

        self.player = Gst.Pipeline.new("player")

        self.prepareSource(source_opts)
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

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)
        bus.connect("sync-message::element", self.on_sync_message)

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
        elif self.source_name == "videotestsrc":
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

    def on_message(self, bus, message):
        t = message.type

        # print("on_message", message, t)
        if t == Gst.MessageType.EOS:
            self.player.set_state(Gst.State.NULL)
            print("End of stream")
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print("Error: %s" % err, debug)
            self.player.set_state(Gst.State.NULL)
        elif t == Gst.MessageType.STATE_CHANGED:
            pass

    def on_sync_message(self, bus, message):
        if message.get_structure() is None:
            return
        message_name = message.get_structure().get_name()
        if message_name == "prepare-window-handle":
            print("prepare-window-handle", message.src.get_name(),
                  self.full_widget_winid, self.roi_widget_winid)


def easy_run(imager, target):
    imager.player.set_state(Gst.State.PLAYING)
    loop = GLib.MainLoop()
    thread = threading.Thread(target=target, args=(loop, ))
    thread.start()
    loop.run()
