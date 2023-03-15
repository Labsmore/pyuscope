"""
Start aggregating plugin registration

Motion HAL
Imager HAL
Control Scroll (imager GUI controls)
"""

from uscope.imager.imager import Imager, MockImager
from uscope.config import cal_load_all
from uscope.imager.imager_util import get_scaled

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import threading
import time
from PIL import Image
"""
WARNING: early on there was some variety in maybe different imagers
for now GUI pretty solidly assumes Gst
This is the intended direction in general
ie if you want something to show up in the GUI write a gstreamer plugin for it

However we might consider some flexibility allowing not to render...TBD
ex: if you are using DSLR with own screen no strict requirement to render here
although could still have a method to snap pictures
"""
"""
FIXME: a lot of this should be some sort of layer on top of planner
is not strictly speaking related to the GUI. Hmm
"""


class GstGUIImager(Imager):
    class Emitter(QObject):
        change_properties = pyqtSignal(dict)

    def __init__(self, ac, usc):
        Imager.__init__(self)
        self.ac = ac
        self.usc = usc
        self.image_ready = threading.Event()
        self.image_id = None
        self.emitter = GstGUIImager.Emitter()
        self.width, self.height = self.usc.imager.cropped_wh()

    def wh(self):
        return self.width, self.height

    def next_image(self):
        #self.ac.emit_log('gstreamer imager: taking image to %s' % file_name_out)
        def got_image(image_id):
            self.ac.emit_log('Image captured reported: %s' % image_id)
            self.image_id = image_id
            self.image_ready.set()

        self.image_id = None
        self.image_ready.clear()
        self.ac.capture_sink.request_image(got_image)
        self.ac.emit_log('Waiting for next image...')
        self.image_ready.wait()
        self.ac.emit_log('Got image %s' % self.image_id)
        return self.ac.capture_sink.pop_image(self.image_id)

    def get(self):
        image = self.next_image()
        factor = self.usc.imager.scalar()
        scaled = get_scaled(image, factor, Image.ANTIALIAS)
        return {"0": scaled}

    def log_planner_header(self, log):
        log("Imager config")
        log("  Image size")
        log("    Raw sensor size: %uw x %uh" % (self.usc.imager.raw_wh()))
        cropw, croph = self.usc.imager.cropped_wh()
        log("    Cropped sensor size: %uw x %uh" %
            (self.usc.imager.cropped_wh()))
        scalar = self.usc.imager.scalar()
        log("    Output scale factor: %0.1f" % scalar)
        log("    Final scaled image: %uw x %uh" %
            (cropw * scalar, croph * scalar))


def get_gui_imager(source, gui):
    # WARNING: only gst- sources are supported
    # This indirection may be eliminated
    if source == 'mock':
        return MockImager()
    elif source.find("gst-") == 0:
        ret = GstGUIImager(gui, usc=gui.usc)
        # For HDR which needs in situ control
        ret.emitter.change_properties.connect(
            gui.control_scroll.set_disp_properties)
        return ret
    else:
        raise Exception('Invalid imager type %s' % source)
