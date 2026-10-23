"""
Imager plugins describe a camera source
Qt free: front ends (Argus, headless) build their own pipeline / views on top
"""

from uscope.imager.properties import ImagerProperties


class ImagerPlugin:
    def __init__(self, session):
        """
        session: ArgusCommon or HeadlessSession
        """
        self.session = session

    def name(self):
        """
        Name registered to
        ex: gst-v4l2src-hy800b
        """
        assert 0, "Required"

    def get_properties(self):
        """
        Return the ImagerProperties model for this source
        """
        return ImagerProperties(self.session, plugin=self)

    def configure_resolution(self, log=None):
        """
        Called before the pipeline is built
        May update microscope.j5 imager (in memory) with the selected resolution
        """
        pass

    def resolution_selector(self):
        """
        Return a ResolutionSelector (see gst_toupcamsrc) if the source supports
        selecting resolution, otherwise None
        """
        return None

    def get_widget(self):
        """
        Argus video widget class
        rpi has performance constraints
        allow it to do special direct rendering that bypasses gstreamer pipeline entirely
        """
        from uscope.gui.gstwidget import zoomable_widget_class
        return zoomable_widget_class()


class GstImagerPlugin(ImagerPlugin):
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

    def camera_present(self, source):
        """
        Whether the started source opened a camera
        None if the source can't tell (decided by the state change result)
        """
        return None

    def camera_not_found_detail(self, source):
        return "The video source failed to start"

    def probe_camera(self, source):
        """
        Identify the camera behind a started source
        Returns a dict, see uscope.imager.camera_check.check_camera() found
        """
        return {}

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
