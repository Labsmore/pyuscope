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

        # Zoom state (ScalerCrop-based digital zoom)
        self.zoom = 1.0
        # The value to restore zoom to when toggling high zoom off
        self.zoom_out = None
        # Populated in run() after the camera has started
        self.full_res = None
        self.default_crop = None

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
        self.picam2.start()

        # Derive the full sensor size and default ScalerCrop rectangle from
        # camera_properties.  ScalerCrop coordinates are always in full-sensor-
        # pixel units regardless of binning/scaling.  We avoid capture_metadata()
        # here because QGlPicamera2 has already registered frame callbacks and
        # a concurrent metadata capture can deadlock on some versions.
        full_w, full_h = self.picam2.camera_properties['PixelArraySize']
        self.full_res = (full_w, full_h)
        self.default_crop = (0, 0, full_w, full_h)

    # ---- Digital zoom via picamera2 ScalerCrop ----

    def _apply_zoom(self):
        """Recompute and apply the ScalerCrop rectangle for the current zoom."""
        if self.default_crop is None:
            return
        _, _, def_w, def_h = self.default_crop
        crop_w = int(def_w / self.zoom)
        crop_h = int(def_h / self.zoom)
        # Centre the crop within the full sensor area
        offset_x = (self.full_res[0] - crop_w) // 2
        offset_y = (self.full_res[1] - crop_h) // 2
        self.picam2.set_controls({"ScalerCrop": (offset_x, offset_y, crop_w, crop_h)})

    def zoomable_plus(self):
        zoom = self.zoom * 2
        if zoom >= 32.0:
            zoom = 32.0
        self.change_roi_zoom(zoom)

    def zoomable_minus(self):
        zoom = self.zoom // 2
        if zoom <= 1.0:
            zoom = 1.0
        self.change_roi_zoom(zoom)

    def zoomable_high_toggle(self):
        if self.zoom_out:
            self.change_roi_zoom(self.zoom_out)
            self.zoom_out = None
        else:
            self.zoom_out = self.zoom
            self.change_roi_zoom(self._calc_zoom_magnified())

    def change_roi_zoom(self, zoom):
        assert zoom >= 1.0
        self.zoom = zoom
        self._apply_zoom()

    def _calc_zoom_magnified(self):
        """Return the zoom level that shows camera pixels at ~2x screen pixels."""
        widget_width = self.preview_widget.width()
        if widget_width <= 0:
            return 1.0
        factor = 4.0
        incoming_used_w = int(widget_width / factor)
        if incoming_used_w <= 0:
            return 1.0
        if self.default_crop is None:
            return 1.0
        _, _, def_w, _ = self.default_crop
        return max(1.0, def_w / incoming_used_w)

    # ---- Stubs for GstVideoPipeline methods not applicable to picamera2 ----

    def add_full_widget(self):
        return None

    def full_restart_pipeline(self):
        pass

    def remove_full_widget(self):
        pass

    def recover_video_crash(self):
        pass

    def enable_rtsp_server(self, enabled):
        pass
