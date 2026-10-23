from uscope.gui.gstwidget import GstVideoPipeline
from uscope.gui.picam2widget import PiCam2VideoPipeline
from uscope.gui.control_scroll import ImagerControlScroll
from uscope.config import get_bc
from uscope.gui import imager
from uscope.app.argus.threads import QMotionThread, QImageProcessingThread, QJoystickThread, QTaskThread, QImagerControlThread
from uscope.joystick import JoystickNotFound
from uscope.microscope import Microscope
from uscope.scan_runner import ScanRunner
from uscope.kinematics import Kinematics
from uscope.motion.hal import HomingAborted
from uscope.motion.grbl import LimitSwitchActive, Estop
from uscope.imager.camera_check import CameraNotFound
from uscope.subsystem import Subsystem
from uscope import util

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import os.path
import sys
import threading
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


class GuiHomeProgress:
    """
    Shows a progress bar while homing blocks the GUI thread (ex: at startup)
    Progress is relative to max_home_time, so it is a worst case estimate
    """
    def __init__(self, fallback):
        self.fallback = fallback
        self.dialog = None

    def __call__(self, state, percent, remaining):
        app = QApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            self.fallback(state, percent, remaining)
            return

        if state == "start":
            self.close()
            self.dialog = QProgressDialog()
            self.dialog.setWindowTitle("Homing")
            self.dialog.setCancelButton(None)
            self.dialog.setRange(0, 1000)
            self.dialog.setMinimumDuration(0)
            self.dialog.setAutoClose(False)
            self.dialog.setAutoReset(False)
            self.dialog.setWindowModality(Qt.ApplicationModal)
            self.dialog.setMinimumWidth(400)
            self.dialog.show()
        elif state in ("done", "failed"):
            self.close()
            return

        if self.dialog is None:
            return
        self.dialog.setValue(int(percent * 10))
        remaining = int(remaining)
        self.dialog.setLabelText("Homing...\nUp to %u:%02u remaining" %
                                 (remaining // 60, remaining % 60))
        app.processEvents()

    def close(self):
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
            QApplication.instance().processEvents()


class USCArgus:
    def __init__(self, j=None, microscope=None):
        """
        j: usj["app"]["argus"]
        """
        self.j = j
        self.microscope = microscope

    def scan_dir(self):
        """
        Where scan jobs go
        """
        ret = self.j.get("scan_dir")
        if ret:
            return ret
        else:
            return os.path.join(self.microscope.usc.bc.get_data_dir(), "scan")

    def snapshot_dir(self):
        """
        Where snapshots are saved
        """
        ret = self.j.get("snapshot_dir")
        if ret:
            return ret
        else:
            return os.path.join(self.microscope.usc.bc.get_data_dir(),
                                "snapshot")

    def cache_fn(self):
        """
        Main persistent GUI cache file
        """
        return self.microscope.usc.bc.argus_cache_fn()

    def batch_cache_fn(self):
        ret = self.j.get("batch_cache_file")
        if ret:
            return ret
        else:
            return os.path.join(self.microscope.usc.bc.get_data_dir(),
                                "batch_cache.j5")

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
            # bool("0") is True: parse as a number
            return bool(int(val))
        return bool(self.j.get("mdi", 0))

    def video_record_container(self):
        """
        mp4 or mkv
        Falls back to the other if encoders for this one aren't installed
        """
        ret = self.j.get("video_record_container", "mp4")
        if ret not in ("mp4", "mkv"):
            raise ValueError("Bad video_record_container: %s" % (ret, ))
        return ret

    def video_record_audio(self):
        """
        Default for the record audio checkbox
        """
        return bool(self.j.get("video_record_audio", False))

    def video_record_audio_source(self):
        """
        gst-launch style description, ex: "pulsesrc device=foo"
        """
        return self.j.get("video_record_audio_source", "autoaudiosrc")

    def video_record_max_wh(self):
        """
        Downscale recordings to fit in this size to keep encoding real time
        None: record full resolution
        """
        ret = self.j.get("video_record_max_wh", [3840, 2160])
        if ret is None:
            return None
        return tuple(ret)

    """
    def jog_min(self):
        return int(self.j.get("jog_min", 1))

    # FIXME: default should be actual max jog rate
    def jog_max(self):
        return int(self.j.get("jog_max", 1000))
    """


# GUI specific functionality
class ACSubsystem(Subsystem):
    def __init__(self, ac):
        self.ac = ac
        super().__init__(self.ac.microscope)

    def name(self):
        return "argus"

    def system_status_ts(self, root_status, status):
        """
        WARNING: this must be thread safe
        Be very careful taking cached instead of widget state values
        """
        root_status[
            "taking_snapshot"] = self.ac.mw.mainTab.imaging_widget.taking_snapshot
        root_status["planner"] = self.ac.scan_runner.progress_ts() or None
        root_status["scripting"] = {
            "running": self.ac.scriptingTab.is_running(),
        }
        root_status[
            "autofocusing"] = self.ac.image_processing_thread.autofocus_running


def camera_not_found_dialog(e):
    QMessageBox.critical(None, "Camera not found", str(e), QMessageBox.Ok,
                         QMessageBox.Ok)


class ArgusCommon(QObject):
    log_msg = pyqtSignal(str)
    takeSnapshot = pyqtSignal()
    setJogSlider = pyqtSignal(float)
    snapshotCaptured = pyqtSignal(dict)
    """
    Dictionary w/ objective config
    The low level format supported by config file
    """
    objectiveChanged = pyqtSignal(dict)
    """
    Capture pixel format was changed (gstreamer format string, ex: "RGB")
    May be emitted from a non-GUI thread (ex: scripting)
    """
    captureFormatChanged = pyqtSignal(str)
    # list of scan hconfigs, see ScriptingPlugin.run_planner_hconfig()
    runPlannerHConfigs = pyqtSignal(object)
    # dict, see ScriptingPlugin.planner_set_xy3p_corners()
    setPlannerXY3PCorners = pyqtSignal(object)

    # pos = pyqtSignal(int)

    def __init__(self, microscope_name=None, mw=None):
        QObject.__init__(self)

        self.mw = mw
        self.tabs = {}
        self.logs = []
        """
        Capture format override
        None: use microscope.j5 imager.format
        str: user selected format (Video => Capture format), persisted per camera S/N
        """
        self.capture_format_override = None

        self.motion_thread = None
        self.joystick_thread = None
        self.image_processing_thread = None
        self.task_thread = None
        self.imager_control_thread = None

        self.bc = get_bc()
        self.check_threads = self.bc.check_threads()
        if self.check_threads:
            print("ArgusCommon: checking threads")
        self.main_thread = threading.get_ident()

        try:
            self.microscope = Microscope(auto=False,
                                         configure=False,
                                         name=microscope_name)
        # vm1 GRBL is queried during auto config
        except Estop:
            QMessageBox.critical(
                None, "Error",
                "Emergency stop is activated. Check estop button and/or power supply and then re-home",
                QMessageBox.Ok, QMessageBox.Ok)
            raise
        except LimitSwitchActive:
            QMessageBox.critical(
                None, "Error",
                "Limit switch tripped. Manually move away from limit switches and then re-home",
                QMessageBox.Ok, QMessageBox.Ok)
            raise
        self.usc = self.microscope.usc
        self.usc.app_register("argus", USCArgus)
        self.aconfig = self.usc.app("argus")
        # force creating directories to make structure more consistent
        self.bc.batch_data_dir()
        self.bc.script_data_dir()

        self.scan_configs = None
        self.imager = None
        self.kinematics = None
        self.motion = None
        self.subsystem = None

        if self.usc.imager.source() == "picam2src":
            self.log("VIDSRC: Using Picamera2 source")
            from picamera2 import Picamera2
            self.capture_pc2 = Picamera2()
            self.vidpip = PiCam2VideoPipeline(ac=self, zoomable=True, log=self.log)

        else:
            self.log("VIDSRC: Using Gstreamer source")
            self.vidpip = GstVideoPipeline(ac=self, zoomable=True, log=self.log)
            self.capture_sink = self.vidpip.capture_sink
            # v4l2 sources look up the configured camera by name here
            try:
                self.vidpip.setupGst()
            except CameraNotFound as e:
                camera_not_found_dialog(e)
                raise

        # Requires video pipeline already setup
        self.imager_properties = self.vidpip.imager_aplugin.get_properties()
        self.control_scroll = ImagerControlScroll(self.imager_properties,
                                                  ac=self)

        self.scan_runner = ScanRunner(self, scan_dir=self.aconfig.scan_dir())
        # motion.progress = self.hal_progress
        self.motion_thread = QMotionThread(ac=self)
        self.motion_thread.log_msg.connect(self.log)
        self.motion = self.motion_thread.motion
        self.microscope.set_motion(self.motion)
        self.motion.set_ask_home(gui_ask_home)
        self.motion.set_home_progress(
            GuiHomeProgress(self.motion.default_home_progress))
        # If the GUI dies with an uncaught exception, at least cancel jogs
        util.register_emergency_stop(self.motion.emergency_stop)

        self.microscope.motion_thread = self.motion_thread

        # TODO: we should try to make this allow connecting and disconnecting joystick
        # its not required for any critical initialization / would be easy to do
        try:
            self.joystick_thread = QJoystickThread(ac=self)
        except JoystickNotFound:
            self.log("Joystick not found")
        self.microscope.joystick_thread = self.joystick_thread

        self.task_thread = QTaskThread(ac=self)
        self.imager_control_thread = QImagerControlThread(ac=self)

    def initUI(self):
        self.vidpip.setupWidgets()

    def post_ui_init(self):
        # Try to do critical initialization first in case something fails

        # Now that UI is setup start directing log messages here
        self.microscope.log = self.emit_log

        # hack...used by joystick...
        # self.microscope.jog_abs_lazy = self.motion_thread.jog_abs_lazy
        self.microscope.jog_fractioned_lazy = self.motion_thread.jog_fractioned_lazy
        self.microscope.jog_cancel = self.motion_thread.jog_cancel
        self.microscope.motion_stop = self.motion_thread.stop
        self.microscope.get_jog_controller = self.motion_thread.get_jog_controller
        self.microscope.image_save_extension = self.image_save_extension
        self.runPlannerHConfigs.connect(
            self.mainTab.imaging_widget.go_planner_hconfigs)
        self.setPlannerXY3PCorners.connect(self.panoramaTab.set_xy3p_corners)

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
            self.top_widget.motion_widget.slider.set_jog_slider)
        self.microscope.set_jog_scale = set_jog_scale_emit

        self.vid_fd = None
        try:
            self.vidpip.run()
        except CameraNotFound as e:
            camera_not_found_dialog(e)
            raise
        camera_mismatch = getattr(self.vidpip, "camera_mismatch", None)
        if camera_mismatch:
            QMessageBox.warning(
                None, "Microscope config / camera mismatch",
                camera_mismatch +
                "\n\nContinuing anyway: video and calibration may be wrong",
                QMessageBox.Ok, QMessageBox.Ok)
        self.init_imager()
        # Needs imager / vidpip to be able to query which properties are present
        self.imager_properties.setup()
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
        except Estop:
            QMessageBox.critical(
                None, "Error",
                "Emergency stop is activated. Check estop button and/or power supply and then re-home",
                QMessageBox.Ok, QMessageBox.Ok)
            raise

        self.motion_thread.start()
        if self.joystick_thread:
            self.joystick_thread.log_msg.connect(self.log)
            self.joystick_thread.start()
        # Needs imager which isn't initialized until gst GUI objects are made
        self.image_processing_thread = QImageProcessingThread(ac=self)

        self.task_thread.start()
        self.imager_control_thread.start()

        self.kinematics = Kinematics(
            microscope=self.microscope,
            log=self.log,
        )
        self.kinematics.configure()
        self.microscope.kinematics = self.kinematics
        # Planner already uses it from its own thread
        self.microscope.set_kinematics_ts(self.kinematics)

        self.image_processing_thread.log_msg.connect(self.log)
        self.image_processing_thread.start()

        # Must be made thread safe
        self.microscope.set_motion_ts(self.motion_thread.get_planner_motion())
        # Imager and its property model are thread safe
        self.microscope.set_imager_ts(self.microscope.imager)

        self.subsystem = ACSubsystem(self)
        self.microscope.add_subsystem(self.subsystem)

        if not self.bc.check_panotools():
            self.log("WARNING panotools: incomplete installation")
            # self.log("  enblend: " + str(self.bc.enblend_cli()))
            self.log("  enfuse: " + str(self.bc.enfuse_cli()))
            self.log("  align_image_stack: " +
                     str(self.bc.align_image_stack_cli()))

        if self.microscope.bc.stress_test():
            self.log("WARNING: stress test enabled")

    def shutdown_request(self, phase):
        if self.motion_thread:
            self.motion_thread.shutdown_request(phase)
        self.scan_runner.shutdown_request(phase)
        if self.image_processing_thread:
            self.image_processing_thread.shutdown_request(phase)
        if self.joystick_thread:
            self.joystick_thread.shutdown_request(phase)
        if self.task_thread:
            self.task_thread.shutdown_request(phase)
        if self.imager_control_thread:
            self.imager_control_thread.shutdown_request(phase)

    def shutdown_join(self):
        if self.motion_thread:
            self.motion_thread.shutdown_join()
            # causes too many corner cases
            # self.motion_thread = None
        self.scan_runner.shutdown_join()
        if self.image_processing_thread:
            self.image_processing_thread.shutdown_join()
            # self.image_processing_thread = None
        if self.joystick_thread:
            self.joystick_thread.shutdown_join()
            # self.joystick_thread = None
        if self.task_thread:
            self.task_thread.shutdown_join()
            # self.task_thread = None
        if self.imager_control_thread:
            self.imager_control_thread.shutdown_join()

    def check_thread_safety(self):
        if self.check_threads:
            assert self.main_thread == threading.get_ident(), (
                "GUI thread unsafe access detected", self.main_thread,
                threading.get_ident())

    def cache_save(self, cachej):
        self.microscope.cache_save(cachej)

    def cache_load(self, cachej):
        self.microscope.cache_load(cachej)

    # TODO: refactor these into subsystem interface
    def cache_sn_save(self, cachej):
        self.microscope.cache_sn_save(cachej)
        imagerj = cachej.setdefault("imager", {})
        # Only persist an explicit override so that microscope.j5 changes
        # take effect when the user hasn't picked something
        if self.capture_format_override:
            imagerj["format"] = self.capture_format_override
        else:
            imagerj.pop("format", None)
        selector = self.vidpip.imager_aplugin.resolution_selector()
        if selector:
            selector.cache_sn_save(cachej)

    def cache_sn_load(self, cachej):
        self.microscope.cache_sn_load(cachej)
        fmt = cachej.get("imager", {}).get("format")
        if fmt:
            self.apply_capture_format_override(fmt, log_invalid=True)

    def apply_capture_format_override(self, fmt, log_invalid=False):
        """
        Set (fmt is a format string) or clear (fmt is None) the capture format override
        Switches the live pipeline accordingly
        Returns True if applied
        """
        if self.imager is None:
            return False
        override = fmt
        if fmt is None:
            fmt = self.usc.imager.capture_format()
        try:
            self.imager.set_capture_format(fmt)
        except (ValueError, RuntimeError) as e:
            if log_invalid:
                self.log(f"WARNING: ignoring capture format {fmt}: {e}")
                return False
            raise
        self.capture_format_override = override
        return True

    def init_imager(self):
        source = self.vidpip.source_name
        self.log('Loading imager %s...' % source)
        if source == "picam2src":
            self.imager = imager.Picam2GUIImager(self)
        else:
            self.imager = imager.GstGUIImager(self)
        # gst pipeline already created / should be ready to go
        self.imager.device_restarted()

    def emit_log(self, s='', newline=True):
        # event must be omitted from the correct thread
        # however, if it hasn't been created yet assume we should log from this thread
        self.log_msg.emit(s)

    def log(self, s='', newline=True):
        """
        WARNING: this is not thread safe
        If you need something thread safe use microscope.log
        """
        for log in self.logs:
            log(s, newline=newline)

    def update_pconfig(self, pconfig):
        pass

    def auto_exposure_enabled(self):
        return self.imager_properties.auto_exposure_enabled()

    def set_exposure(self, n):
        return self.imager_properties.set_exposure(n)

    def get_exposure(self):
        return self.imager_properties.get_exposure()

    def get_exposure_disp_property(self):
        return self.imager_properties.get_exposure_disp_property()

    def auto_color_enabled(self):
        return self.imager_properties.auto_color_enabled()

    # FIXME: better abstraction
    def is_idle(self):
        # if not self.mw.mainTab.imaging_widget.snapshot_pb.isEnabled():
        if self.mw.mainTab.imaging_widget.taking_snapshot:
            self.log("Wait for snapshot to complete before CNC'ing")
            return False
        return True

    def poll_misc(self):
        """
        Mostly looking for crashes in other contexts to propagate up
        """
        # It is not always possible to recover a motion controller crash without re-homing (ex: LIP-X1)
        # We also have seen some transient serial errors but full crashes seem to be pretty rare
        if self.motion_thread and self.motion_thread.motion is None:
            raise ArgusShutdown("Motion thread crashed")
        # In theory we can fairly quickly restart camera on disconnect
        if self.vidpip and not self.vidpip.ok:
            # raise ArgusShutdown("Video pipeline crashed")
            self.log("WARNING: video pipeline crashed. Attempting restart")
            self.recover_video_crash()

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
        return self.microscope.objective_state.config()

    def imaging_config(self):
        """
        Return imaging widget configuration
        """
        return self.mainTab.imaging_widget.imaging_config

    """
    Scripting session interface (see uscope/scripting.py)
    Thread safe
    """

    def get_active_objective(self):
        """
        Returns the name of the active objective
        """
        return self.microscope.objective_state.name()

    def set_active_objective(self, objective):
        self.microscope.objective_state.select(objective)

    def image_save_extension(self):
        return self.mainTab.imaging_widget.save_extension()

    def set_um_per_pixel_raw_1x(self, val):
        self.microscope.objective_state.set_um_per_pixel_raw_1x(val)

    def run_planner_hconfigs(self, hconfigs):
        self.runPlannerHConfigs.emit(list(hconfigs))

    def set_planner_xy3p_corners(self, corners):
        self.setPlannerXY3PCorners.emit(dict(corners))

    def is_planner_running(self):
        return self.scan_runner.is_running()

    def capture_format_changed(self, fmt):
        # Let the GUI (menu, ...) follow along
        self.captureFormatChanged.emit(fmt)

    def recover_video_crash(self):
        prop_cache = self.imager_properties.get_prop_cache()
        self.vidpip.recover_video_crash()
        # FIXME: maybe this needs to be delayed until we are actually up?
        # In any case GUI will still have old state and it might "just work"
        # Assume we are good
        # If the camera is still gone we'll get a pipeline crash in a second or so
        self.imager_properties.recover_video_crash(prop_cache)
