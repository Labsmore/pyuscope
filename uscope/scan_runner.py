"""
Front end independent scan (planner) runner

Runs a queue of scan hconfigs one after another in a background thread
(see ScriptingPlugin.run_planner_hconfig() for the hconfig format)
Used by Argus (ImagingTaskWidget) and HeadlessSession
"""

from uscope.benchmark import Benchmark
from uscope.microscope import MicroscopeStop
from uscope.planner.planner import PlannerStop
from uscope.planner.planner_util import get_planner
from uscope.scan_util import out_dir_config_to_dir

from io import StringIO
import os
import threading
import time
import traceback


class ScanRunnerListener:
    """
    Override what you need
    Called from the scan thread
    """
    def scan_started(self, hconfig):
        pass

    def scan_progress(self, state):
        """Planner progress callback state (type: begin, image, ...)"""
        pass

    def scan_done(self, hconfig, result):
        pass

    def queue_done(self, results):
        pass


class ScanRunner:
    def __init__(self, session, scan_dir):
        """
        session: provides microscope, imager, imager_properties
        scan_dir: base directory scans are written into
        """
        self.session = session
        self.microscope = session.microscope
        self.scan_dir = scan_dir
        self.listeners = []
        self.lock = threading.RLock()
        self.thread = None
        self.planner = None
        self.hconfig = None
        self.progress = None
        self.bench = None
        self.log_fd_scan = None
        self.restore_properties = None

    def log(self, msg=""):
        self.microscope.log(msg)

    def add_listener(self, listener):
        self.listeners.append(listener)

    def _notify(self, method, *args):
        for listener in list(self.listeners):
            try:
                getattr(listener, method)(*args)
            except Exception:
                traceback.print_exc()

    """
    Control
    """

    def is_running(self):
        return self.thread is not None

    def is_paused(self):
        with self.lock:
            return bool(self.planner and self.planner.is_paused())

    def pause(self):
        with self.lock:
            if self.planner:
                self.planner.pause()

    def unpause(self):
        with self.lock:
            if self.planner:
                self.planner.unpause()

    def run(self, hconfigs, dry=False, abort_on_failure=True):
        """
        Queue hconfigs and start running them
        Each hconfig["callback_done"](result=...) is called when it finishes
        abort_on_failure: stop the queue at the first scan that doesn't complete ok
        """
        hconfigs = list(hconfigs)
        if not hconfigs:
            return
        if self.is_running():
            raise RuntimeError("Planner is already running")
        if self.session.imager_properties.auto_exposure_enabled():
            self.log(
                "WARNING: auto-exposure is enabled. This may result in an unevently exposed panorama"
            )
        # If user had started some movement before hitting run wait until its done
        self.microscope.motion_thread.wait_idle()
        with self.lock:
            if self.thread is not None:
                raise RuntimeError("Planner is already running")
            self.restore_properties = None
            if not dry:
                self.restore_properties = self.session.imager.get_properties()
                if not os.path.exists(self.scan_dir):
                    os.mkdir(self.scan_dir)
            self.microscope.motion_thread.jog_enable(False)
            self.thread = threading.Thread(target=self._run_queue,
                                           args=(hconfigs, dry,
                                                 abort_on_failure),
                                           name="scan-runner",
                                           daemon=True)
            self.thread.start()

    def join(self, timeout=None):
        thread = self.thread
        if thread is not None:
            thread.join(timeout=timeout)

    def shutdown_request(self, phase):
        with self.lock:
            if self.planner:
                self.planner.shutdown_request(phase)

    def shutdown_join(self, timeout=10.0):
        with self.lock:
            planner = self.planner
        if planner:
            planner.shutdown_join()
        self.join(timeout=timeout)

    """
    Status
    """

    def progress_ts(self):
        """
        Current scan progress (images_to_capture, images_captured, remaining_time, eta_message)
        None if not running
        """
        with self.lock:
            if self.thread is None or self.progress is None:
                return None
            return dict(self.progress)

    def scan_log_write(self, s):
        """Copy a log message into the running scan's uscan.log"""
        with self.lock:
            if self.log_fd_scan is not None:
                self.log_fd_scan.write(s)
                self.log_fd_scan.flush()

    """
    Scan thread
    """

    def _run_queue(self, hconfigs, dry, abort_on_failure):
        results = []
        # The last callback must run after cleanup: callers may queue another scan from it
        pending_callback = None
        try:
            for hconfig in hconfigs:
                if pending_callback:
                    pending_callback()
                    pending_callback = None
                result = self._run_hconfig(hconfig, dry)
                results.append(result)
                # Restore defaults between each run
                # Ex: if HDR doesn't clean up simplifies things
                if self.restore_properties:
                    self.session.imager.set_properties(
                        self.restore_properties)
                self._notify("scan_done", hconfig, result)
                callback = hconfig.get("callback_done")
                if callback:
                    pending_callback = lambda callback=callback, result=result: callback(
                        result=result)
                if result.get("hard_fail"):
                    break
                if result["result"] != "ok" and abort_on_failure:
                    break
        finally:
            with self.lock:
                self.thread = None
                self.planner = None
                self.hconfig = None
                self.progress = None
                self.restore_properties = None
            self.microscope.motion_thread.jog_enable(True)
            self._notify("queue_done", results)
            if pending_callback:
                pending_callback()

    def _run_hconfig(self, hconfig, dry):
        ret = {"result": None}
        hconfig["dry"] = dry
        try:
            out_dir_config = hconfig.get("out_dir_config") or {
                "dt_prefix": True,
                "user_basename": "scan",
            }
            out_dir = out_dir_config_to_dir(out_dir_config, self.scan_dir)
            hconfig["out_dir"] = out_dir
            pconfig = hconfig["pconfig"]
            if os.path.exists(out_dir):
                self.log("Run aborted: directory already exists")
                ret["result"] = "init_failure"
                return ret
            if "hdr" in pconfig.get(
                    "imager",
                {}) and self.session.imager_properties.auto_exposure_enabled():
                self.log("Run aborted: requested HDR but auto-exposure enabled")
                ret["result"] = "init_failure"
                return ret
            if not dry:
                os.mkdir(out_dir)
        except Exception as e:
            self.log(f"WARNING: scan setup failed: {e}")
            traceback.print_exc()
            return {"result": "init_failure", "hard_fail": True}

        with self.lock:
            self.hconfig = hconfig
            self.progress = {}
            if dry:
                self.log_fd_scan = StringIO()
            else:
                self.log_fd_scan = open(os.path.join(out_dir, "uscan.log"),
                                        "w")
        self._notify("scan_started", hconfig)
        try:
            self.log('Initializing planner!')
            planner = get_planner(
                pconfig=pconfig,
                microscope=self.microscope,
                out_dir=out_dir,
                # Includes microscope.json in the output
                meta_base={"microscope": self.microscope.usc.usj},
                # Set to true if should try to mimimize hardware actions
                dry=dry,
                log=self.log)
            planner.register_progress_callback(self._progress_cb)
            with self.lock:
                self.planner = planner
            self.log(f"Planner: running to {out_dir}")
            b = Benchmark()
            ret["meta"] = planner.run()
            ret["result"] = "ok"
            b.stop()
            self.log('Planner done!  Took : %s' % str(b))
        except (MicroscopeStop, PlannerStop):
            ret["result"] = "stopped"
        except Exception as e:
            self.log('WARNING: planner thread crashed: %s' % str(e))
            traceback.print_exc()
            ret["result"] = "exception"
            ret["exception"] = e
        finally:
            with self.lock:
                self.planner = None
                if self.log_fd_scan:
                    self.log_fd_scan.close()
                self.log_fd_scan = None
        self.log("RX planner done, result: %s" % (ret["result"], ))
        return ret

    def _progress_cb(self, state):
        with self.lock:
            progress = self.progress
            if state["type"] == "begin":
                progress["images_to_capture"] = state["images_to_capture"]
                progress["images_captured"] = 0
                self.bench = Benchmark(state["images_to_capture"])
            elif state["type"] == "image":
                cur_time = time.time()
                progress["images_captured"] = state["images_captured"]
                self.bench.set_cur_items(state["images_captured"])
                progress["remaining_time"] = self.bench.remaining_time(
                    cur_time=cur_time)
                progress["eta_message"] = self.bench.__str__(
                    cur_time=cur_time)
        self._notify("scan_progress", state)
