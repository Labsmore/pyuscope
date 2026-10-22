from uscope.imager.plugins.aplugin import ImagerPlugin
from .properties import Picam2Properties


class Plugin(ImagerPlugin):
    def name(self):
        return "picam2src"

    def get_properties(self):
        return Picam2Properties(self.session, plugin=self)

    def get_widget(self):
        # Picam2 uses its own preview widget (QGlPicamera2 / QPicamera2)
        # created directly by PiCam2VideoPipeline, not the GStreamer sink widgets
        return None
