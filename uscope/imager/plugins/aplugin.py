from uscope.gui.imager import GstGUIImager
from uscope.gui.gstwidget import SinkxZoomableWidget


class ArgusImagerPlugin:
    def __init__(self, ac):
        self.ac = ac

    def name(self):
        """
        Name registered to
        ex: gst-v4l2src-hy800b
        """
        assert 0, "Required"

    '''
    def detect_sources(self):
        """
        If present return a list of properties that are required to configure that source,
        one for each detected device
        """
        return []
    '''

    def get_imager(self):
        """
        Return a pyuscope Imager object
        """
        assert 0, "Required"

    def get_widget(self):
        """
        rpi has performance constraints
        allow it to do special direct rendering that bypasses gstreamer pipeline entirely
        """
        return SinkxZoomableWidget


# Intended to be used with GstGUIImager
class ArgusGstImagerPlugin(ArgusImagerPlugin):
    def name(self):
        return self.source_name()

    def source_name(self):
        """
        Return the name of the gstreamer source plugin
        """
        assert 0, "Required"

    def get_gst_source(self, name=None):
        '''
        return gstreamer Element instance for this with name assined to this object instance
        '''
        assert 0, "Required"

    def get_imager(self):
        return GstGUIImager(self.ac)

    def supported_formats(self):
        """
        gstreamer video/x-raw format strings this source can be negotiated to
        (see uscope.imager.pixel_format)
        Return None if the source doesn't support selecting the format,
        in which case the pipeline won't constrain it
        """
        return None

    def gst_frame_meta(self, image_dict):
        """
        Optionally annotate a captured frame dict before decoding
        ex: add "bit_depth" / "bayer_pattern" for raw captures
        Called in the requesting thread, not the gstreamer streaming thread
        """
        pass

    def gst_decode_image(self, image_dict):
        """
        Decode a captured frame dict (see CaptureSink) into either
        -a PIL RGB image (legacy)
        -(PIL RGB image, native depth numpy array or None)
        """
        assert 0, "Required"
