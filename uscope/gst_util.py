from .img_util import get_scaled

import os
from PIL import Image
import io
import threading
import traceback

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
        (result, mapinfo) = buffer.map(Gst.MapFlags.READ)
        assert result

        try:
            # type: bytes
            if self.cb:
                self.cb(mapinfo.data)
        finally:
            buffer.unmap(mapinfo)

        return Gst.FlowReturn.OK


class CaptureSink(CbSink):
    """
    Multi-threaded capture sink
    Queues images on request
    """
    def __init__(self):
        CbSink.__init__(self)

        self.image_requested = threading.Event()
        self.next_image_id = 0
        self.images = {}
        self.cb = self.render_cb
        self.user_cb = None

    def request_image(self, cb):
        '''Request that the next image be saved'''
        # Later we might make this multi-image
        if self.image_requested.is_set():
            raise Exception('Image already requested')
        self.user_cb = cb
        self.image_requested.set()

    def get_image(self, image_id):
        '''Fetch the image but keep it in the buffer'''
        return self.images[image_id]

    def del_image(self, image_id):
        '''Delete image in buffer'''
        del self.images[image_id]

    def pop_image(self, image_id):
        '''Fetch the image and delete it form the buffer'''
        ret = self.images[image_id]
        del self.images[image_id]
        # Arbitrarily convert to PIL here
        # TODO: should pass rawer/lossless image to PIL instead of jpg?
        open("tmp.bin", "wb").write(ret)
        return Image.open(io.BytesIO(ret))

    '''
    gstreamer plugin core methods
    '''

    def render_cb(self, buffer):
        #print 'Capture sink buffer in'
        try:
            '''
            Two major circumstances:
            -Imaging: want next image
            -Snapshot: want next image
            In either case the GUI should listen to all events and clear out the ones it doesn't want
            '''
            #print 'Got image'
            if self.image_requested.is_set():
                print('Processing image request')
                # Does this need to be locked?
                # Copy buffer so that even as object is reused we don't lose it
                # is there a difference between str(buffer) and buffer.data?
                # type <class 'bytes'>
                # print("type", type(buffer))
                self.images[self.next_image_id] = bytearray(buffer)
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
