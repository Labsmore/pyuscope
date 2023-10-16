from uscope.gui.gstwidget import GstVideoPipeline
from uscope.gui.control_scrolls import get_control_scroll
from uscope.config import get_usj, USC, get_bc, get_data_dir
from uscope.gui import plugin
from uscope.gst_util import Gst, CaptureSink
from uscope.app.argus.threads import MotionThread, ImageProcessingThread
from uscope.app.argus.threads import JoystickThread, JoystickNotFound
from uscope.microscope import Microscope

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import os.path
import sys
"""
Common GUI related services to a typical application
Owns core logging, motion, and imaging objects
However how they are displayed and used is up to end applications
"""

# debug = os.getenv("ARGUS_VERBOSE") == "Y"


def error(msg, code=1):
    prefix = 'ERROR'
    if sys.stdout.isatty():
        prefix = '\33[91m' + prefix + '\33[0m'
    print('{} {}'.format(prefix, msg))
    exit(code)


class ArgusShutdown(Exception):
    pass


class USCArgus:
    def __init__(self, j=None):
        """
        j: usj["app"]["argus"]
        """
        self.j = j

    # FIXME: default should be actual max jog rate
    def jog_max(self):
        return int(self.j.get("jog_max", 1000))

    def scan_dir(self):
        """
        Where scan jobs go
        """
        ret = self.j.get("scan_dir")
        if ret:
            return ret
        else:
            return os.path.join(get_data_dir(), "scan")

    def snapshot_dir(self):
        """
        Where snapshots are saved
        """
        ret = self.j.get("snapshot_dir")
        if ret:
            return ret
        else:
            return os.path.join(get_data_dir(), "snapshot")

    def cache_fn(self):
        """
        Main persistent GUI cache file
        """
        return os.path.join(get_data_dir(), "argus.j5")

    def batch_cache_fn(self):
        ret = self.j.get("batch_cache_file")
        if ret:
            return ret
        else:
            return os.path.join(get_data_dir(), "batch_cache.j5")

    def dry_default(self):
        """
        Should the dry checkbox be checked:
        -At startup
        -After job complete
        """
        return self.j.get("dry_default", True)

    def scan_name_widget(self):
        """
        simple: file name prefix only (no extension)
        sipr0n: vendor, chipid, layer enforced
        """
        ret = self.j.get("jog_name_widget", "simple")
        if ret not in ("simple", "sipr0n"):
            raise ValueError("Bad scan name widget: %s" % (ret, ))
        return ret

    def show_mdi(self):
        """
        Should argus GUI show MDI?
        For advanced users only
        Bypasses things like gearbox correction
        """
        val = os.getenv("ARGUS_MDI", None)
        if val is not None:
            return bool(val)
        return bool(self.j.get("mdi", 0))

    def jog_min(self):
        return int(self.j.get("jog_min", 1))


class ArgusCommon(QObject):
    """
    was:
    pictures_to_take, pictures_taken, image, first
    """
    cncProgress = pyqtSignal(dict)
    log_msg = pyqtSignal(str)
    takeSnapshot = pyqtSignal()
    setJogSlider = pyqtSignal(float)
    """
    Dictionary w/ objective config
    The low level format supported by config file
    """
    objectiveChanged = pyqtSignal(dict)

    # Receive the processed snapshot image
    snapshotProcessed = pyqtSignal(dict)

    # pos = pyqtSignal(int)

    def __init__(self, microscope_name=None, mw=None):
        QObject.__init__(self)

        self.mw = mw
        self.logs = []
        self.update_pconfigs = []

        self.motion_thread = None
        self.planner_thread = None
        self.joystick_thread = None
        self.image_processing_thread = None

        self.microscope = None
        self.microscope_name = microscope_name
        self.usj = get_usj(name=microscope_name)
        self.usc = USC(usj=self.usj)
        self.usc.app_register("argus", USCArgus)
        self.aconfig = self.usc.app("argus")
        self.bc = get_bc()

        self.scan_configs = None
        self.imager = None
        self.kinematics = None
        self.motion = None
        self.vidpip = GstVideoPipeline(usc=self.usc,
                                       zoomable=True,
                                       overview2=True,
                                       log=self.log)

        # FIXME: review sizing
        # self.vidpip.size_widgets()
        # self.capture_sink = Gst.ElementFactory.make("capturesink")

        # TODO: some pipelines output jpeg directly
        # May need to tweak this
        cropped_width, cropped_height = self.usc.imager.cropped_wh()
        self.capture_sink = CaptureSink(width=cropped_width,
                                        height=cropped_height,
                                        source_type=self.vidpip.source_name)
        assert self.capture_sink
        self.vidpip.player.add(self.capture_sink)
        # jpegenc is probably obsolete here
        # Now data is normalized in CaptureSink to do jpeg conversion etc
        if 1:
            self.vidpip.setupGst(raw_tees=[self.capture_sink])
        else:
            self.jpegenc = Gst.ElementFactory.make("jpegenc")
            self.vidpip.player.add(self.jpegenc)
            self.vidpip.setupGst(raw_tees=[self.jpegenc])
            self.jpegenc.link(self.capture_sink)

        # Special UI initialization
        # Requires video pipeline already setup
        self.control_scroll = get_control_scroll(self.vidpip, usc=self.usc)

        self.planner_thread = None
        # motion.progress = self.hal_progress
        self.motion_thread = MotionThread(usc=self.usc)
        self.motion_thread.log_msg.connect(self.log)
        self.motion = self.motion_thread.motion

        # TODO: init things in Microscope and then push references here
        self.microscope = Microscope(
            bc=self.bc,
            usc=self.usc,
            motion=self.motion,
            # should all be initialized
            auto=False,
            configure=False)

        try:
            self.joystick_thread = JoystickThread(ac=self)
        except JoystickNotFound:
            self.log("Joystick not found")

    def initUI(self):
        self.vidpip.setupWidgets()

    def post_ui_init(self):
        self.microscope.jog_lazy = self.motion_thread.jog_lazy
        self.microscope.cancel_jog = self.motion_thread.stop

        # FIXME: these are not thread safe
        # convert to signals

        # self.microscope.take_snapshot = self.mainTab.snapshot_widget.take_snapshot
        def take_snapshot_emit():
            self.takeSnapshot.emit()

        self.takeSnapshot.connect(self.mainTab.snapshot_widget.take_snapshot)
        self.microscope.take_snapshot = take_snapshot_emit

        # self.microscope.set_jog_scale = self.mainTab.motion_widget.slider.set_jog_slider
        def set_jog_scale_emit(val):
            self.setJogSlider.emit(val)

        self.setJogSlider.connect(
            self.mainTab.motion_widget.slider.set_jog_slider)
        self.microscope.set_jog_scale = set_jog_scale_emit

        self.vid_fd = None
        self.vidpip.run()
        self.init_imager()
        # Needs imager / vidpip to be able to query which properties are present
        self.control_scroll.run()

        # TODO: init things in Microscope and then push references here
        self.microscope.imager = self.imager
        self.microscope.configure()

        self.motion_thread.start()
        if self.joystick_thread:
            self.joystick_thread.log_msg.connect(self.log)
            self.joystick_thread.start()
        # Needs imager which isn't initialized until gst GUI objects are made
        self.image_processing_thread = ImageProcessingThread(
            motion_thread=self.motion_thread, ac=self)
        self.kinematics = self.image_processing_thread.kinematics
        self.image_processing_thread.log_msg.connect(self.log)
        self.image_processing_thread.start()

    def shutdown(self):
        if self.motion_thread:
            self.motion_thread.shutdown()
            self.motion_thread = None
        if self.planner_thread:
            self.planner_thread.shutdown()
            self.planner_thread = None
        if self.image_processing_thread:
            self.image_processing_thread.shutdown()
            self.image_processing_thread = None
        if self.joystick_thread:
            self.joystick_thread.shutdown()
            self.joystick_thread = None

    def init_imager(self):
        source = self.vidpip.source_name
        self.log('Loading imager %s...' % source)
        # Gst is pretty ingrained for the GUI
        #
        self.imager = plugin.get_gui_imager(source, self)

    def emit_log(self, s='', newline=True):
        # event must be omitted from the correct thread
        # however, if it hasn't been created yet assume we should log from this thread
        self.log_msg.emit(s)

    def log(self, s='', newline=True):
        for log in self.logs:
            log(s, newline=newline)

    def update_pconfig(self, pconfig):
        for update_pconfig in self.update_pconfigs:
            update_pconfig(pconfig)

    def auto_exposure_enabled(self):
        return self.control_scroll.auto_exposure_enabled()

    def set_exposure(self, n):
        return self.control_scroll.set_exposure(n)

    def get_exposure(self):
        return self.control_scroll.get_exposure()

    def get_exposure_property(self):
        return self.control_scroll.get_exposure_property()

    # FIXME: better abstraction
    def is_idle(self):
        if not self.mw.mainTab.snapshot_widget.snapshot_pb.isEnabled():
            self.log("Wait for snapshot to complete before CNC'ing")
            return False
        return True

    def poll_misc(self):
        """
        Mostly looking for crashes in other contexts to propagate up
        """
        if self.motion_thread.motion is None:
            raise ArgusShutdown("Motion thread crashed")
        if not self.vidpip.ok:
            raise ArgusShutdown("Video pipeline crashed")

    def joystick_disable(self, asneeded=False):
        assert asneeded
        jl = self.mainTab.motion_widget.joystick_listener
        if jl:
            jl.disable()

    def joystick_enable(self, asneeded=False):
        assert asneeded
        jl = self.mainTab.motion_widget.joystick_listener
        if jl:
            jl.enable()

    def objective_config(self):
        """
        Return currently selected objective configuration
        """
        return self.mainTab.objective_widget.obj_config
