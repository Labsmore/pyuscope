import os
from PIL import Image
import io
import threading
import traceback
import cv2
import numpy as np

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstBase', '1.0')
gi.require_version('GstVideo', '1.0')

# Needed for window.get_xid(), xvimagesink.set_window_handle(), respectively:
# WARNING: importing GdkX11 will cause hard crash (related to Qt)
# fortunately its not needed
# from gi.repository import GdkX11, GstVideo
from gi.repository import Gst

Gst.init(None)
from gi.repository import GstBase, GObject


class CbSink(GstBase.BaseSink):
    """
    Simple capture sink providing callback
    """

    __gstmetadata__ = ('CustomSink','Sink', \
                      'Custom test sink element', 'John McMaster')

    __gsttemplates__ = Gst.PadTemplate.new("sink", Gst.PadDirection.SINK,
                                           Gst.PadPresence.ALWAYS,
                                           Gst.Caps.new_any())

    def __init__(self, *args, **kwargs):
        GstBase.BaseSink.__init__(self, *args, **kwargs)
        self.cb = None

    def set_cb(self, cb):
        self.cb = cb

    """
        # self.sinkpad.set_chain_function(self.chainfunc)
        # self.sinkpad.set_event_function(self.eventfunc)

    def chainfunc(self, pad, buffer):
        # print("got buffer, size %u" % len(buffer))
        print("chaiunfun %s" % (buffer, ))
        return Gst.FlowReturn.OK

    def eventfunc(self, pad, event):
        return True
    """

    def do_render(self, buffer):
        # print("do_render()")
        (result, mapinfo) = buffer.map(Gst.MapFlags.READ)
        assert result

        try:
            # type: bytes
            if self.cb:
                self.cb(mapinfo.data)
        finally:
            buffer.unmap(mapinfo)

        return Gst.FlowReturn.OK


# NOTE: this was a jpg capture sink, now its a raw capture sink
class CaptureSink(CbSink):
    """
    Multi-threaded capture sink
    Queues images_actual on request
    """

    # FIXME: get width/height from stream
    def __init__(self, width, height, source_type):
        CbSink.__init__(self)

        self.image_requested = threading.Event()
        self.next_image_id = 0
        self.images_actual = {}
        self.cb = self.render_cb
        self.user_cb = None
        self.width = width
        self.height = height
        self.source_type = source_type
        self.verbose = False

    def request_image(self, cb):
        '''Request that the next image be saved'''
        # Later we might make this multi-image
        if self.image_requested.is_set():
            raise Exception('Image already requested')
        self.user_cb = cb
        self.image_requested.set()

    def get_image(self, image_id):
        '''Fetch the image but keep it in the buffer'''
        return self.images_actual[image_id]

    def del_image(self, image_id):
        '''Delete image in buffer'''
        del self.images_actual[image_id]

    def pop_image(self, image_id):
        '''Fetch the image and delete it form the buffer'''
        buf, width, height, source_type = self.images_actual[image_id]
        del self.images_actual[image_id]
        self.verbose and print("bytes", len(buf), 'w', width, 'h', height)
        # Arbitrarily convert to PIL here
        # TODO: should pass rawer/lossless image to PIL instead of jpg?
        # open("tmp.bin", "wb").write(ret)
        if source_type == "gst-toupcamsrc":
            # xxx: sometimes get too much data...is this the right fix?
            # buf = buf[0:width * height * 3]
            assert len(buf) == width * height * 3, (
                "Wanted %u, got %u, w=%u, h=%u" %
                (width * height * 3, len(buf), width, height))
            # Need 59535360 bytes, got 59535360
            # print("Need %u bytes, got %u" % (3 * width * height, len(buf)))
            return Image.frombytes('RGB', (width, height), bytes(buf), 'raw',
                                   'RGB')

        elif source_type == "gst-v4l2src":
            with open("raw.bin", "wb") as f:
                f.write(buf)
            print("buf", self.width, self.height, len(buf))
            # assert 0, "fixme"

            w = width
            h = height
            shape = (h, w, 2)
            yuv = np.frombuffer(buf, dtype=np.uint8)
            yuv = yuv.reshape(shape)
            bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2RGBA_YUYV)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb)
        elif source_type == "jpg":
            return Image.open(io.BytesIO(buf))
        else:
            assert 0, source_type

    '''
    gstreamer plugin core methods
    '''

    def render_cb(self, buffer):
        # print("render_cb()")
        try:
            '''
            Two major circumstances:
            -Imaging: want next image
            -Snapshot: want next image
            In either case the GUI should listen to all events and clear out the ones it doesn't want
            '''
            # print('Got image')
            if self.image_requested.is_set():
                self.verbose and print('Processing image request')
                # Does this need to be locked?
                # Copy buffer so that even as object is reused we don't lose it
                # is there a difference between str(buffer) and buffer.data?
                # type <class 'bytes'>
                # print("type", type(buffer))

                # FIXME: hack
                # get width more properly
                """
                if len(buffer) == 5440 * 3648 * 3:
                    width, height = 5440, 3648
                else:
                    assert 0, "FIXME"
                """

                self.images_actual[self.next_image_id] = (bytearray(buffer),
                                                          self.width,
                                                          self.height,
                                                          self.source_type)
                # Clear before emitting signal so that it can be re-requested in response
                self.image_requested.clear()
                #print 'Emitting capture event'
                self.user_cb(self.next_image_id)
                #print 'Capture event emitted'
                self.next_image_id += 1
        except:
            traceback.print_exc()
            os._exit(1)


# XXX: these aren't properly registering anymore, but good enough
GObject.type_register(CbSink)
GObject.type_register(CaptureSink)
__gstelementfactory__ = (
    ("cbsink", Gst.Rank.NONE, CbSink),
    ("capturesink", Gst.Rank.NONE, CaptureSink),
)
