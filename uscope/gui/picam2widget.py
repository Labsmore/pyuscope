from PyQt5.Qt import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from uscope.imager.plugins.aplugins import get_imager_aplugin

import os


class PiCam2VideoPipeline:
    """
    Integrates Qt widgets + libpicamera2 for easy setup

    vidpip = GstVideoPipeline()
    vidpip.setupWidgets()
    vidpip.setupGst()
    vidpip.run()
    """

    def __init__(
        self,
        # Enable overview view?
        overview=False,
        # Enable ROI view?
        overview_roi=False,
        zoomable=False,
        # Enable overview view?
        # hack for second tab displaying overview
        overview2=False,
        overview_full_window=False,
        widget_configs=None,
        # microscope configuration
        usj=None,
        ac=None,
        log=None):

        self.ac = ac
        self.picam2 = ac.capture_pc2
        self.source = None
        self.source_name = "picam2src"
        self.verbose = os.getenv("USCOPE_GSTWIDGET_VERBOSE") == "Y"

        # Load the picam2 plugin for the imager architecture
        self.imager_aplugin = get_imager_aplugin(ac=ac, source_name="picam2src")

        # Set up preview configuration and switch to it
        self.preview_config = self.picam2.create_preview_configuration(main={"size": (1280, 960)})
        self.picam2.configure(self.preview_config)

        if log is None:
            def log(s):
                print(s)
        self.log = log

        # Create the preview widget -- use GL if available
        if os.environ['DISPLAY'].startswith(':'):
            # Running locally, try the GL previewer
            self.log("picam2widget: Using GL-accelerated preview widget")
            from picamera2.previews.qt import QGlPicamera2
            self.preview_widget = QGlPicamera2(self.picam2, width=1280, height=960, keep_ar=True)
        else:
            # Running remotely, force the non-GL renderer
            self.log("picam2widget: Using X11 preview widget (slow!) because SSH X11 Forwarding seems to be in use")
            from picamera2.previews.qt import QPicamera2
            self.preview_widget = QPicamera2(self.picam2, width=1280, height=960, keep_ar=True)

        # Clear if anything bad happens and shouldn't be trusted
        self.ok = True


    def get_widget(self, name):
        """
        Called by external user to get the widget to render to
        """
        #return self.widgets[name]
        return self.preview_widget

    def setupWidgets(self):
        #for widget in self.widgets.values():
        #    widget.setupWidget()
        pass

    def run(self):
        #for widget in self.widgets.values():
        #    widget.setupWidget()
        self.picam2.start()
