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

    def gst_decode_image(self, image_dict):
        assert 0, "Required"
