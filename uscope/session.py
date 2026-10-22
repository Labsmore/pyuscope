"""
Headless microscope session
Qt free counterpart of ArgusCommon (uscope/gui/common.py)
Provides the session interface used by the scripting engine (see uscope/scripting.py)

The imager defaults to MockImager
A real (gstreamer) imager can be passed in with imager=
"""

from uscope.config import get_bc
from uscope.microscope import Microscope, MicroscopeStop
from uscope.motion.thread import SimpleMotionThread
from uscope.imagep.thread import SimpleImageProcessingThreadBase
from uscope.imager.imager import MockImager
from uscope.planner.planner_util import get_planner
from uscope.planner.planner import PlannerStop
from uscope.scan_util import out_dir_config_to_dir
from uscope.subsystem import Subsystem
from uscope.threads import ShutdownPhase
from uscope.benchmark import Benchmark

import os
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
    def __init__(self, microscope_name=None, imager=None, log=None):
        self._log = log
        self.bc = get_bc()
        self.microscope = Microscope(name=microscope_name,
                                     make_motion=False,
                                     make_imager=False,
                                     configure=False,
                                     log=self.log)
        self.usc = self.microscope.usc
        self.motion_thread = None
        self.image_processing_thread = None
        self.planner_thread = None
        self._planner_progress = None
        self._planner_lock = threading.Lock()

        try:
            self._init_threads(imager)
        except Exception:
            self.shutdown()
            raise

        objectives = self.microscope.get_objectives()
        self.set_active_objective(objectives.default_name())
        self.microscope.image_save_extension = self.image_save_extension
        self.microscope.add_subsystem(SessionSubsystem(self))

    def _init_threads(self, imager):
        self.motion_thread = SimpleMotionThread(self.microscope)
        self.motion = self.motion_thread.motion
        self.microscope.set_motion(self.motion)
        self.microscope.motion_thread = self.motion_thread

        if imager is None:
            width, height = self.usc.imager.final_wh()
            imager = MockImager(width=width, height=height)
        self.imager = imager
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

    def log(self, msg=""):
        if self._log:
            self._log(msg)
        else:
            print(msg)

    def shutdown(self):
        # Let a running scan stop before the threads it relies on
        if self.planner_thread is not None:
            self.microscope.stop()
            self.planner_thread.join(timeout=10.0)
        for thread in (self.image_processing_thread, self.motion_thread):
            if thread is None:
                continue
            for phase in (ShutdownPhase.INITIAL, ShutdownPhase.FINAL):
                thread.shutdown_request(phase)
            thread.shutdown_join(timeout=3.0)
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
        return self.microscope.get_objectives().get_config(
            self.get_active_objective())

    def get_active_objective(self):
        return self.microscope.get_active_objective()

    def set_active_objective(self, objective):
        if objective not in self.microscope.get_objectives().names():
            raise ValueError(f"Unknown objective {objective}")
        self.microscope.set_active_objective(objective)

    def image_save_extension(self):
        return self.usc.imager.save_extension()

    def set_um_per_pixel_raw_1x(self, val):
        self.microscope.get_objectives().set_um_per_pixel_raw_1x(val)

    def apply_capture_format_override(self, fmt):
        self.imager.set_capture_format(fmt)

    def scan_dir(self):
        return self.bc.get_scan_dir()

    def planner_progress_ts(self):
        with self._planner_lock:
            if self.planner_thread is None or self._planner_progress is None:
                return None
            return dict(self._planner_progress)

    def is_planner_running(self):
        return self.planner_thread is not None

    def run_planner_hconfigs(self, hconfigs):
        """
        Run scans in the background, one after another
        Each hconfig["callback_done"](result=...) is called when it finishes
        Stops at the first scan that doesn't complete ok
        """
        hconfigs = list(hconfigs)
        with self._planner_lock:
            if self.planner_thread is not None:
                raise RuntimeError("Planner is already running")
            self.planner_thread = threading.Thread(target=self._run_hconfigs,
                                                   args=(hconfigs, ),
                                                   name="planner",
                                                   daemon=True)
        self.planner_thread.start()

    def _run_hconfigs(self, hconfigs):
        try:
            for hconfig in hconfigs:
                result = self._run_hconfig(hconfig)
                callback = hconfig.get("callback_done")
                if callback:
                    callback(result=result)
                if result["result"] != "ok":
                    break
        finally:
            with self._planner_lock:
                self.planner_thread = None
                self._planner_progress = None

    def _run_hconfig(self, hconfig):
        ret = {"result": None}
        try:
            dry = hconfig.get("dry", False)
            out_dir_config = hconfig.get("out_dir_config") or {
                "dt_prefix": True,
                "user_basename": "scan",
            }
            out_dir = out_dir_config_to_dir(out_dir_config, self.scan_dir())
            if os.path.exists(out_dir):
                self.log("Run aborted: directory already exists")
                ret["result"] = "init_failure"
                return ret
            if not dry:
                os.makedirs(out_dir)
            hconfig["out_dir"] = out_dir

            planner = get_planner(
                microscope=self.microscope,
                pconfig=hconfig["pconfig"],
                out_dir=out_dir,
                dry=dry,
                meta_base={"microscope": self.usc.usj},
                log=self.log)
            planner.register_progress_callback(self._planner_progress_cb)
            self.log(f"Planner: running to {out_dir}")
            ret["meta"] = planner.run()
            ret["result"] = "ok"
            self.log("Planner: done")
        except (MicroscopeStop, PlannerStop):
            ret["result"] = "stopped"
        except Exception as e:
            self.log(f"WARNING: planner crashed: {e}")
            traceback.print_exc()
            ret["result"] = "exception"
            ret["exception"] = e
        return ret

    def _planner_progress_cb(self, state):
        with self._planner_lock:
            if self._planner_progress is None:
                self._planner_progress = {}
            progress = self._planner_progress
            if state["type"] == "begin":
                progress["images_to_capture"] = state["images_to_capture"]
                progress["images_captured"] = 0
                self._bench = Benchmark(state["images_to_capture"])
            elif state["type"] == "image":
                cur_time = time.time()
                progress["images_captured"] = state["images_captured"]
                self._bench.set_cur_items(state["images_captured"])
                progress["remaining_time"] = self._bench.remaining_time(
                    cur_time=cur_time)
                progress["eta_message"] = self._bench.__str__(
                    cur_time=cur_time)
