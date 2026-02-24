from uscope.imager.plugins.aplugin import ArgusImagerPlugin
from .widgets import Picam2ControlScroll


class Plugin(ArgusImagerPlugin):
    def name(self):
        return "picam2src"

    def get_imager(self):
        from uscope.gui.imager import Picam2GUIImager
        return Picam2GUIImager(self.ac)

    def get_control_scroll(self):
        return Picam2ControlScroll

    def get_widget(self):
        # Picam2 uses its own preview widget (QGlPicamera2 / QPicamera2)
        # created directly by PiCam2VideoPipeline, not the GStreamer sink widgets
        return None
