import os
import threading
import traceback
from uscope.imager.image_sequence import CapturedImage

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
from gi.repository import GstBase, GObject, GstVideo


def caps_video_info(caps):
    """
    Summarize video/x-raw caps as a dict
    format: gstreamer format string (ex: "RGB")
    width, height
    stride: bytes per row of plane 0
    size: total frame bytes

    Returns None if caps aren't fixed raw video
    """
    if caps is None or not caps.is_fixed():
        return None
    s = caps.get_structure(0)
    # gst-python 1.26 returns a StructureWrapper usable only as a context manager
    if hasattr(s, "__enter__"):
        with s as structure:
            return _structure_video_info(caps, structure)
    return _structure_video_info(caps, s)


def _structure_video_info(caps, s):
    if s.get_name() != "video/x-raw":
        return None
    fmt = s.get_string("format")
    ok_w, width = s.get_int("width")
    ok_h, height = s.get_int("height")
    if not (fmt and ok_w and ok_h):
        return None
    ret = {
        "format": fmt,
        "width": width,
        "height": height,
        "stride": None,
        "size": None,
    }
    # Prefer letting gstreamer compute padding / strides
    # Newer pygobject: VideoInfo.new_from_caps() (from_caps() raises NotImplementedError)
    # Older (ex: Ubuntu 20.04): VideoInfo().from_caps()
    vinfo = None
    try:
        if hasattr(GstVideo.VideoInfo, "new_from_caps"):
            vinfo = GstVideo.VideoInfo.new_from_caps(caps)
    except Exception:
        vinfo = None
    if vinfo is None:
        try:
            vinfo = GstVideo.VideoInfo()
            if not vinfo.from_caps(caps):
                vinfo = None
        except Exception:
            vinfo = None
    if vinfo is not None:
        ret["stride"] = vinfo.stride[0]
        ret["size"] = vinfo.size
    return ret


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
        # Most recently negotiated input caps summary (see caps_video_info)
        self.video_info = None

    def set_cb(self, cb):
        self.cb = cb

    def do_set_caps(self, caps):
        self.video_info = caps_video_info(caps)
        return True

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
    def __init__(self, session, width, height, source_type):
        CbSink.__init__(self)

        self.session = session
        # Protects pending requests and the image buffer
        # Requests come from arbitrary threads (planner, scripting, GUI)
        # while frames arrive in the gstreamer streaming thread
        self._capture_lock = threading.Lock()
        # [(cb, meta), ...] waiting for the next frame
        self.pending = []
        self.next_image_id = 0
        self.images_actual = {}
        self.cb = self.render_cb
        self.width = width
        self.height = height
        self.source_type = source_type
        self.verbose = False

    def request_image(self, cb, meta=None):
        '''
        Request that the next image be saved
        Multiple outstanding requests are allowed: each receives
        (its own copy of) the next frame
        NOTE: callback executes in gstreamer thread context
        It does not (necessarily) execute in main thread
        '''
        with self._capture_lock:
            self.pending.append((cb, meta))

    def get_image(self, image_id):
        '''Fetch the image but keep it in the buffer'''
        with self._capture_lock:
            return self.images_actual[image_id]

    def del_image(self, image_id):
        '''Delete image in buffer'''
        with self._capture_lock:
            del self.images_actual[image_id]

    def pop_captured_image(self, image_id):
        '''Fetch the image and delete it form the buffer'''
        with self._capture_lock:
            image_dict = self.images_actual.pop(image_id)
        #self.verbose and print("bytes", len(buf), 'w', width, 'h', height)
        aplugin = self.session.vidpip.imager_aplugin
        # Let the source plugin annotate (ex: sensor bit depth, bayer pattern)
        aplugin.gst_frame_meta(image_dict)
        decoded = aplugin.gst_decode_image(image_dict)
        # Legacy plugins return a bare PIL image
        if isinstance(decoded, tuple):
            image, data = decoded
        else:
            image, data = decoded, None
        return CapturedImage(image=image,
                             meta=image_dict["meta"],
                             microscope=self.session.microscope,
                             data=data,
                             pixel_format=image_dict.get("format"),
                             bit_depth=image_dict.get("bit_depth"),
                             bayer_pattern=image_dict.get("bayer_pattern"))

    def capture_format(self):
        """
        gstreamer format string of the most recently negotiated frames
        None if not yet negotiated
        """
        if self.video_info is None:
            return None
        return self.video_info["format"]

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
            # Grab all outstanding requests
            # Done before storing so that a callback can re-request
            # and get the *next* frame rather than this one
            with self._capture_lock:
                if not self.pending:
                    return
                pending = self.pending
                self.pending = []
                # Negotiated caps are authoritative when available
                vi = self.video_info or {}
                # Copy buffer so that even as object is reused we don't lose it
                # type <class 'bytes'>
                data = bytearray(buffer)
                served = []
                for cb, meta in pending:
                    image_id = self.next_image_id
                    self.next_image_id += 1
                    self.images_actual[image_id] = {
                        "bytes": data,
                        "width": vi.get("width", self.width),
                        "height": vi.get("height", self.height),
                        # None => source plugin's legacy default
                        "format": vi.get("format"),
                        "stride": vi.get("stride"),
                        "meta": meta,
                    }
                    served.append((cb, image_id))
            self.verbose and print('Processing %u image request(s)' %
                                   len(served))
            # Callbacks outside the lock: they may call back into us
            for cb, image_id in served:
                cb(image_id)
        except Exception:
            # Exceptions here would otherwise be swallowed by gstreamer
            # and silently stall every capture
            traceback.print_exc()
            os._exit(1)


# XXX: these aren't properly registering anymore, but good enough
GObject.type_register(CbSink)
GObject.type_register(CaptureSink)
__gstelementfactory__ = (
    ("cbsink", Gst.Rank.NONE, CbSink),
    ("capturesink", Gst.Rank.NONE, CaptureSink),
)
