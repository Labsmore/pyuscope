"""
Headless microscope session
Qt free counterpart of ArgusCommon (uscope/gui/common.py)
Provides the session interface used by the scripting engine (see uscope/scripting.py)

imager=
    None: camera from the microscope config (gst-* sources run a GstImagerPipeline)
    "mock": MockImager (no camera)
    Imager instance: used as is
"""

from uscope.config import get_bc
from uscope.microscope import Microscope
from uscope.motion.thread import SimpleMotionThread
from uscope.imagep.thread import SimpleImageProcessingThreadBase
from uscope.imager.imager import MockImager
from uscope.imager.properties import MockImagerProperties
from uscope.imager.thread import SimpleImagerControlThread
from uscope.scan_runner import ScanRunner
from uscope.subsystem import Subsystem
from uscope.threads import ShutdownPhase

import threading
import time
import traceback


class SessionSubsystem(Subsystem):
    """
    High level status as reported by Argus' ACSubsystem
    """
    def __init__(self, session):
        self.session = session
        super().__init__(session.microscope)

    def name(self):
        return "session"

    def system_status_ts(self, root_status, status):
        root_status["taking_snapshot"] = False
        root_status["planner"] = self.session.planner_progress_ts()
        root_status["autofocusing"] = bool(
            self.session.image_processing_thread
            and self.session.image_processing_thread.autofocus_running)


class HeadlessSession:
    # How often to poll imager properties (ex: exposure under auto exposure)
    IMAGER_POLL_PERIOD = 0.5

    def __init__(self,
                 microscope_name=None,
                 imager=None,
                 imager_config=None,
                 log=None):
        """
        imager_config: dict overriding the microscope config's imager section
            ex: {"source": "gst-toupcamsrc", "width": 5440, "height": 3648}
        """
        self._log = log
        self.bc = get_bc()
        self.microscope = Microscope(name=microscope_name,
                                     make_motion=False,
                                     make_imager=False,
                                     configure=False,
                                     log=self.log)
        self.usc = self.microscope.usc
        if imager_config:
            self.usc.imager.j.update(imager_config)
        self.motion_thread = None
        self.image_processing_thread = None
        self.imager_control_thread = None
        self.vidpip = None
        self.imager = None
        self.imager_properties = None
        self._imager_poll_thread = None
        self._imager_poll_stop = threading.Event()
        self.scan_runner = ScanRunner(self, scan_dir=self.scan_dir())

        try:
            self._init_threads(imager)
        except Exception:
            self.shutdown()
            raise

        self.objective_state = self.microscope.objective_state
        self.objective_state.reload()
        self.microscope.image_save_extension = self.image_save_extension
        self.microscope.add_subsystem(SessionSubsystem(self))

    def _init_imager(self, imager):
        """
        Returns the imager
        Property model is setup once the microscope is configured
        """
        if imager is None and self.usc.imager.source().startswith("gst-"):
            # Deferred: pulls in gstreamer
            from uscope.imager.gst_pipeline import GstImagerPipeline
            from uscope.imager.gst_imager import GstImager

            self.log(f"Imager: {self.usc.imager.source()}")
            self.vidpip = GstImagerPipeline(self, log=self.log)
            self.vidpip.setupGst()
            self.vidpip.run()
            self._wait_vidpip_playing()
            self.imager_properties = self.vidpip.imager_aplugin.get_properties()
            return GstImager(self)

        if imager is None or imager == "mock":
            width, height = self.usc.imager.final_wh()
            imager = MockImager(width=width, height=height)
        elif isinstance(imager, str):
            raise ValueError(f"Unknown imager {imager}")
        self.imager_properties = MockImagerProperties(self)
        return imager

    def _wait_vidpip_playing(self, tries=3):
        # Cameras (ex: toupcam) occasionally error on the first frames after open
        for tryi in range(tries):
            try:
                self.vidpip.wait_playing()
                return
            except Exception as e:
                if tryi == tries - 1:
                    raise
                self.log(f"WARNING: {e}. Restarting video pipeline")
                self.vidpip.recover_video_crash()

    def _init_threads(self, imager):
        self.motion_thread = SimpleMotionThread(self.microscope)
        self.motion = self.motion_thread.motion
        self.microscope.set_motion(self.motion)
        self.microscope.motion_thread = self.motion_thread

        self.imager = self._init_imager(imager)
        self.imager.microscope = self.microscope
        self.microscope.set_imager(self.imager)

        self.microscope.configure()

        self.motion_thread.start()
        tstart = time.time()
        while not self.motion_thread.running.is_set():
            if time.time() - tstart > 5.0:
                raise Exception("Motion thread failed to start")
            time.sleep(0.01)
        self.motion_thread.command("update_pos_cache", block=True)

        self.microscope.set_motion_ts(self.motion_thread.get_planner_motion())
        self.microscope.set_imager_ts(self.imager)
        self.microscope.set_kinematics_ts(self.microscope.kinematics)

        self.image_processing_thread = SimpleImageProcessingThreadBase(
            self.microscope)
        self.image_processing_thread.start()
        self.imager_control_thread = SimpleImagerControlThread(
            self.microscope)
        self.imager_control_thread.start()

        self.imager_properties.setup()
        self.imager.device_restarted()
        if self.vidpip:
            # Read back the camera state, then apply microscope / user calibration
            self.imager_properties.update_by_reading()
            self.imager_properties.cal_load()
            self._imager_poll_thread = threading.Thread(
                target=self._imager_poll_loop,
                name="imager-poll",
                daemon=True)
            self._imager_poll_thread.start()

    def _imager_poll_loop(self):
        """
        Keep the property cache (image metadata) current
        and recover from camera crashes
        """
        while not self._imager_poll_stop.wait(self.IMAGER_POLL_PERIOD):
            try:
                if not self.vidpip.ok:
                    self.log(
                        "WARNING: video pipeline crashed. Attempting restart")
                    self.recover_video_crash()
                else:
                    self.imager_properties.update_by_reading()
            except Exception as e:
                self.log(f"WARNING: imager poll failed: {e}")
                traceback.print_exc()

    def recover_video_crash(self):
        prop_cache = self.imager_properties.get_prop_cache()
        self.vidpip.recover_video_crash()
        self.imager_properties.recover_video_crash(prop_cache)

    def log(self, msg=""):
        if self._log:
            self._log(msg)
        else:
            print(msg)
        # Microscope logs before the runner exists
        scan_runner = getattr(self, "scan_runner", None)
        if scan_runner:
            scan_runner.scan_log_write(msg + "\n")

    def shutdown(self):
        # Let a running scan stop before the threads it relies on
        if self.scan_runner.is_running():
            self.microscope.stop()
            self.scan_runner.join(timeout=10.0)
        self._imager_poll_stop.set()
        if self._imager_poll_thread:
            self._imager_poll_thread.join(timeout=2.0)
            self._imager_poll_thread = None
        for thread in (self.imager_control_thread,
                       self.image_processing_thread, self.motion_thread):
            if thread is None or thread.ident is None:
                continue
            for phase in (ShutdownPhase.INITIAL, ShutdownPhase.FINAL):
                thread.shutdown_request(phase)
            thread.shutdown_join(timeout=3.0)
        if self.vidpip:
            self.vidpip.stop()
        if self.microscope.motion is not None:
            try:
                self.microscope.motion.close()
            except Exception as e:
                print(f"WARNING: motion close failed: {e}")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.shutdown()

    """
    Session interface
    """

    def objective_config(self):
        return self.objective_state.config()

    def get_active_objective(self):
        return self.objective_state.name()

    def set_active_objective(self, objective):
        self.objective_state.select(objective)

    def image_save_extension(self):
        return self.usc.imager.save_extension()

    def set_um_per_pixel_raw_1x(self, val):
        self.objective_state.set_um_per_pixel_raw_1x(val)

    def apply_capture_format_override(self, fmt):
        self.imager.set_capture_format(fmt)

    def capture_format_changed(self, fmt):
        pass

    def scan_dir(self):
        return self.bc.get_scan_dir()

    def planner_progress_ts(self):
        return self.scan_runner.progress_ts()

    def is_planner_running(self):
        return self.scan_runner.is_running()

    def run_planner_hconfigs(self, hconfigs):
        """
        Run scans in the background, one after another
        Each hconfig["callback_done"](result=...) is called when it finishes
        Stops at the first scan that doesn't complete ok
        """
        self.scan_runner.run(hconfigs)
