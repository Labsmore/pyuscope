from uscope.gui.gstwidget import GstVideoPipeline
from uscope.gui.control_scrolls import get_control_scroll
from uscope.config import get_usj, USC, get_bc, get_data_dir
from uscope.gui import plugin
from uscope.gst_util import Gst, CaptureSink
from uscope.app.argus.threads import QMotionThread, QImageProcessingThread, QJoystickThread, QTaskThread
from uscope.joystick import JoystickNotFound
from uscope.microscope import Microscope
from uscope.kinematics import Kinematics
from uscope.motion.hal import HomingAborted
from uscope.motion.grbl import LimitSwitchActive

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


def gui_ask_home():
    ret = QMessageBox.warning(
        None, "Home?",
        "System is not homed. Ensure system is clear of fingers, cables, etc before proceeding",
        QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
    if ret != QMessageBox.Ok:
        raise HomingAborted("Homing aborted")


class USCArgus:
    def __init__(self, j=None):
        """
        j: usj["app"]["argus"]
        """
        self.j = j

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

    """
    def jog_min(self):
        return int(self.j.get("jog_min", 1))

    # FIXME: default should be actual max jog rate
    def jog_max(self):
        return int(self.j.get("jog_max", 1000))
    """


class ArgusCommon(QObject):
    """
    was:
    pictures_to_take, pictures_taken, image, first
    """
    cncProgress = pyqtSignal(dict)
    log_msg = pyqtSignal(str)
    takeSnapshot = pyqtSignal()
    setJogSlider = pyqtSignal(float)
    snapshotCaptured = pyqtSignal(dict)
    """
    Dictionary w/ objective config
    The low level format supported by config file
    """
    objectiveChanged = pyqtSignal(dict)

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
        self.task_thread = None

        self.microscope = None
        self.microscope_name = microscope_name
        self.usj = get_usj(name=microscope_name)
        self.usc = USC(usj=self.usj)
        self.usc.app_register("argus", USCArgus)
        self.aconfig = self.usc.app("argus")
        self.bc = get_bc()
        # force creating directories to make structure more consistent
        self.bc.batch_data_dir()
        self.bc.script_data_dir()

        # TODO: init things in Microscope and then push references here
        self.microscope = Microscope(bc=self.bc,
                                     usc=self.usc,
                                     auto=False,
                                     configure=False)

        self.scan_configs = None
        self.imager = None
        self.kinematics = None
        self.motion = None
        self.vidpip = GstVideoPipeline(usc=self.usc,
                                       zoomable=True,
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
        self.motion_thread = QMotionThread(ac=self)
        self.motion_thread.log_msg.connect(self.log)
        self.motion = self.motion_thread.motion
        self.microscope.set_motion(self.motion)
        self.motion.set_ask_home(gui_ask_home)

        self.microscope.motion_thread = self.motion_thread

        # TODO: we should try to make this allow connecting and disconnecting joystick
        # its not required for any critical initialization / would be easy to do
        try:
            self.joystick_thread = QJoystickThread(ac=self)
        except JoystickNotFound:
            self.log("Joystick not found")
        self.microscope.joystick_thread = self.joystick_thread

        self.task_thread = QTaskThread(ac=self)

    def initUI(self):
        self.vidpip.setupWidgets()

    def post_ui_init(self):
        # Try to do critical initialization first in case something fails

        # hack...used by joystick...
        # self.microscope.jog_abs_lazy = self.motion_thread.jog_abs_lazy
        self.microscope.jog_fractioned_lazy = self.motion_thread.jog_fractioned_lazy
        self.microscope.jog_cancel = self.motion_thread.jog_cancel
        self.microscope.motion_stop = self.motion_thread.stop
        self.microscope.get_jog_controller = self.motion_thread.get_jog_controller

        # FIXME: these are not thread safe
        # convert to signals

        # self.microscope.take_snapshot = self.mainTab.imaging_widget.take_snapshot
        def take_snapshot_emit():
            self.takeSnapshot.emit()

        self.takeSnapshot.connect(self.mainTab.imaging_widget.take_snapshot)
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
        try:
            self.microscope.configure()
        except LimitSwitchActive:
            QMessageBox.critical(
                None, "Error",
                "Limit switch tripped. Manually move away from limit switches and then re-home",
                QMessageBox.Ok, QMessageBox.Ok)
            raise

        self.motion_thread.start()
        if self.joystick_thread:
            self.joystick_thread.log_msg.connect(self.log)
            self.joystick_thread.start()
        # Needs imager which isn't initialized until gst GUI objects are made
        self.image_processing_thread = QImageProcessingThread(ac=self)

        self.task_thread.start()

        self.kinematics = Kinematics(
            microscope=self.microscope,
            log=self.log,
        )
        self.kinematics.configure()
        self.microscope.kinematics = self.kinematics

        self.image_processing_thread.log_msg.connect(self.log)
        self.image_processing_thread.start()

        # Must be made thread safe
        self.microscope.set_motion_ts(self.motion_thread.get_planner_motion())
        # emits events + uses queue => already thread safe
        self.microscope.set_imager_ts(self.microscope.imager)

        if not self.bc.check_panotools():
            self.log("WARNING panotools: incomplete installation")
            self.log("  enblend: " + str(self.bc.enblend_cli()))
            self.log("  enfuse: " + str(self.bc.enfuse_cli()))
            self.log("  align_image_stack: " +
                     str(self.bc.align_image_stack_cli()))

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
        if self.task_thread:
            self.task_thread.shutdown()
            self.task_thread = None

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

    def get_exposure_disp_property(self):
        return self.control_scroll.get_exposure_disp_property()

    def auto_color_enabled(self):
        return self.control_scroll.auto_color_enabled()

    # FIXME: better abstraction
    def is_idle(self):
        if not self.mw.mainTab.imaging_widget.snapshot_pb.isEnabled():
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

    def joystick_disable(self):
        jl = self.mainTab.motion_widget.joystick_listener
        if jl:
            jl.disable()

    def joystick_enable(self):
        jl = self.mainTab.motion_widget.joystick_listener
        if jl:
            jl.enable()

    def objective_config(self):
        """
        Return currently selected objective configuration
        """
        return self.mainTab.objective_widget.obj_config

    def imaging_config(self):
        """
        Return imaging widget configuration
        """
        return self.mainTab.imaging_widget.imaging_config
