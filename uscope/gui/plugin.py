"""
Start aggregating plugin registration

Motion HAL
Imager HAL
Control Scroll (imager GUI controls)
"""

from uscope.imager.imager import Imager, MockImager
from uscope.img_util import get_scaled
from uscope.config import cal_load_all

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


class GstGUIImager(Imager):
    class Emitter(QObject):
        change_properties = pyqtSignal(dict)

    def __init__(self, gui, usj):
        Imager.__init__(self)
        self.gui = gui
        self.usj = usj
        self.image_ready = threading.Event()
        self.image_id = None
        self.emitter = GstGUIImager.Emitter()
        # FIXME
        self.width, self.height = (640, 480)

    def wh(self):
        return self.width, self.height

    def next_image(self):
        #self.gui.emit_log('gstreamer imager: taking image to %s' % file_name_out)
        def got_image(image_id):
            self.gui.emit_log('Image captured reported: %s' % image_id)
            self.image_id = image_id
            self.image_ready.set()

        self.image_id = None
        self.image_ready.clear()
        self.gui.capture_sink.request_image(got_image)
        self.gui.emit_log('Waiting for next image...')
        self.image_ready.wait()
        self.gui.emit_log('Got image %s' % self.image_id)
        return self.gui.capture_sink.pop_image(self.image_id)

    def get_normal(self):
        image = self.next_image()
        factor = float(self.usj['imager']['scalar'])
        scaled = get_scaled(image, factor, Image.ANTIALIAS)
        return {"0": scaled}

    def get_hdr(self, hdr):
        ret = {}
        factor = float(self.usj['imager']['scalar'])
        for hdri, hdrv in enumerate(hdr["properties"]):
            print("hdr: set %u %s" % (hdri, hdrv))
            self.emitter.change_properties.emit(hdrv)
            # Wait for setting to take effect
            time.sleep(hdr["tsleep"])
            image = self.next_image()
            scaled = get_scaled(image, factor, Image.ANTIALIAS)
            ret["%u" % hdri] = scaled
        return ret

    def get(self):
        # FIXME: cache at beginning of scan somehow
        hdr = None
        source = self.usj['imager']['source']
        cal = cal_load_all(source)
        if cal:
            hdr = cal.get("hdr", None)
        if hdr:
            return self.get_hdr(hdr)
        else:
            return self.get_normal()


def get_gui_imager(source, gui):
    # WARNING: only gst- sources are supported
    # This indirection may be eliminated
    if source == 'mock':
        return MockImager()
    elif source.find("gst-") == 0:
        ret = GstGUIImager(gui, gui.usj)
        ret.emitter.change_properties.connect(
            gui.control_scroll.set_disp_properties)
        return ret
    else:
        raise Exception('Invalid imager type %s' % source)
