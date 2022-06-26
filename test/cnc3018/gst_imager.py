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

from uscope.imager.imager import Imager
from uscope.gst_util import Gst, CaptureSink
import threading
import time


class GstImager(Imager):
    def __init__(self, source_name=None, verbose=False):
        Imager.__init__(self)
        self.image_ready = threading.Event()
        self.image_id = None
        if source_name is None:
            # source_name = "gst-videotestsrc"
            source_name = "gst-v4l2src"
            source_name = "gst-toupcamsrc"
        self.source_name = source_name
        """
        v4l2-ctl -d /dev/video0 --list-formats-ext
        """
        touptek_esize = 0
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

        self.jpg = True

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

        if self.jpg:
            self.jpegenc = Gst.ElementFactory.make("jpegenc")
            self.player.add(self.jpegenc)
            if not self.videoconvert.link(self.jpegenc):
                raise RuntimeError("Failed to link")
        else:
            self.jpegenc = None

        self.capture_sink = CaptureSink(width=width,
                                        height=height,
                                        raw_input=not self.jpg)
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

    def prepareSource(self, touptek_esize=None):
        # Must not be initialized until after layout is set
        # print(source)
        # assert 0
        if self.source_name in ('gst-v4l2src', 'gst-v4l2src-mu800'):
            self.source = Gst.ElementFactory.make('v4l2src', None)
            assert self.source is not None
            self.source.set_property("device", "/dev/video2")
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


def main():

    # GObject.threads_init()
    Gst.init(None)
    imager = GstImager()

    print("starting pipeline")
    imager.player.set_state(Gst.State.PLAYING)

    def get_image():
        if imager.source_name == "gst-v4l2src":
            print("stabalizing camera")
            time.sleep(2)
        print("Getting image")
        im = imager.get()
        print("Got image")
        im["0"].save("gst_imager.jpg")
        loop.quit()

    thread = threading.Thread(target=get_image)
    thread.start()
    loop = GLib.MainLoop()
    print("Running event loop")
    loop.run()


if __name__ == "__main__":
    main()
