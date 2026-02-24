from uscope.imager.plugins.aplugin import ArgusImagerPlugin


class Plugin(ArgusImagerPlugin):
    def name(self):
        return "picam2src"

    def get_imager(self):
        from uscope.gui.imager import Picam2GUIImager
        return Picam2GUIImager(self.ac)

    def get_control_scroll(self):
        from uscope.gui.control_scroll import Picam2ControlScroll
        return Picam2ControlScroll
