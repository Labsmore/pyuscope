from uscope.planner.planner_util import get_planner
from uscope.planner.planner import PlannerStop
from uscope.benchmark import Benchmark
from uscope import cloud_stitch
from uscope.imagep.thread import ImageProcessingThreadBase
from uscope.planner.thread import PlannerThreadBase
from uscope.motion.thread import MotionThreadBase
from uscope.joystick_thread import JoystickThreadBase
from uscope.threads import CommandThreadBase
from uscope.microscope import MicroscopeStop
from PyQt5.QtCore import QThread, pyqtSignal
import traceback
import time
import subprocess
import psutil
import sys
import json
import platform
import distro
import random
import os
import datetime


def dbg(*args):
    if len(args) == 0:
        print()
    elif len(args) == 1:
        print('threading: %s' % (args[0], ))
    else:
        print('threading: ' + (args[0] % args[1:]))


class ArgusThread(QThread):
    def shutdown_join(self, timeout=3.0):
        # seconds = milliseconds
        self.wait(int(timeout * 1000))

    def check_stress(self):
        if self.microscope.bc.stress_test():
            time.sleep(random.randint(0, 100) * 0.001)


# FIXME: merge this into image processing thread
class StitcherThread(CommandThreadBase, ArgusThread):
    # stitcherDone = pyqtSignal()
    log_msg = pyqtSignal(str)

    def __init__(self, ac, parent=None):
        ArgusThread.__init__(self, parent)
        super().__init__(ac)

        self.command_map = {
            "cloud_stitch": self._cloud_stitch,
            "imagep": self._imagep,
            "cli": self._cli,
        }

    def log(self, msg):
        self.log_msg.emit(msg)

    # Offload uploads etc to thread since they might take a while
    def cloud_stitch_add(
        self,
        directory,
        cs_info,
    ):

        j = {
            #"type": "CloudStitch",
            "directory": directory,
            "cs_info": cs_info,
        }
        self.command("cloud_stitch", j)

    def _cloud_stitch(self, j):
        cloud_stitch.upload_dir(directory=j["directory"],
                                cs_info=j["cs_info"],
                                log=self.log,
                                running=self.running)

    def _imagep(self, j):
        self._imagep_run(j)

    def cli_stitch_add(self, directory, command):
        j = {
            #"type": "cli",
            "directory": directory,
            "command": command,
        }
        self.command("cli", j)

    def _cli(self, j):
        self.log(f"Stitch CLI: kicking off {j['command']} {j['directory']}")
        # Hacky but good enough for now
        # Check terminal for process output
        print("")
        print("")
        print("")
        print(f"Stitch CLI: kicking off {j['command']} {j['directory']}")
        subprocess.check_call([j['command'], j['directory']])
        self.log(f"Stitch CLI: finished job")
        print(f"Stitch CLI: finished job")

    def imagep_add(
        self,
        directory,
        cs_info=None,
        ippj={},
    ):
        j = {
            #"type": "imagep",
            "directory": directory,
            "ipp": ippj,
        }
        if cs_info is not None:
            j["cs_info"] = cs_info
        self.command("imagep", j)

    def process_run(self, args, variant, directory_comment):
        print("")
        print("")
        print("")
        print(f"Process scan ({variant}): starting {directory_comment}")
        # subprocess.check_call(args)
        popen = subprocess.Popen(args,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,
                                 universal_newlines=True)
        p = psutil.Process(popen.pid)
        # Lower priority so GUI runs smoothly
        p.nice(10)
        # proc.communicate()
        for stdout_line in iter(popen.stdout.readline, ""):
            sys.stdout.write(stdout_line)
        popen.stdout.close()
        return_code = popen.wait()
        if return_code == 0:
            msg = f"Process scan ({variant}): ok"
        else:
            msg = f"Process scan ({variant}): error ({return_code})"
            self.log(msg)
        print(msg)
        return return_code == 0

    def _imagep_run(self, j):
        # Taking too much CPU
        # For now let's kick off to own process
        # process_dir(directory=j["directory"], cs_info=j["cs_info"])

        ok = True
        cs_auto_cli = self.microscope.bc.argus_cs_auto_path()
        simple_cli = self.microscope.bc.argus_stitch_cli()

        self.log(f"Process scan: starting {j['directory']}")

        if not cs_auto_cli and not simple_cli:
            self.log(
                "Process scan: WARNING: no image processing engines are configured"
            )
        # Plausible stitching configured?
        cs_info = j.get("cs_info")
        if not (cs_auto_cli and cs_info or simple_cli):
            self.log(
                "Process scan: WARNING: no stitching engines are configured")

        # Run this first in case user wants it to pre-process a scan
        # Originally this only did cloud stitching but now has some other stuff
        if cs_auto_cli:
            args = [
                cs_auto_cli,
            ]
            if cs_info:
                args += [
                    "--access-key",
                    cs_info.access_key(),
                    "--secret-key",
                    cs_info.secret_key(),
                    "--id-key",
                    cs_info.id_key(),
                    "--notification-email",
                    cs_info.notification_email(),
                ]

            args.append(j["directory"])
            ipp = j["ipp"]
            args.append("--json")
            args.append(json.dumps(ipp))
            ok = ok and self.process_run(args, "cs_auto", j['directory'])

        if ok and simple_cli:
            args = [
                simple_cli,
                j["directory"],
            ]
            ok = ok and self.process_run(args, "custom CLI", j['directory'])

        if ok:
            if cs_info:
                self.log(
                    f"Process scan: processed and uploaded {j['directory']}")
            else:
                self.log(f"Process scan: completed {j['directory']}")
        else:
            self.log(f"Process scan: error on {j['directory']}")


class QMotionThread(MotionThreadBase, ArgusThread):
    log_msg = pyqtSignal(str)

    def __init__(self, ac, parent=None):
        ArgusThread.__init__(self, parent)
        self.ac = ac
        MotionThreadBase.__init__(self, microscope=self.ac.microscope)

    def log(self, msg=""):
        self.log_msg.emit(msg)


class QPlannerThread(PlannerThreadBase, ArgusThread):
    plannerDone = pyqtSignal(dict)
    log_msg = pyqtSignal(str)

    def __init__(self, planner_args, progress_cb, parent=None):
        ArgusThread.__init__(self, parent)
        PlannerThreadBase.__init__(self,
                                   planner_args=planner_args,
                                   progress_cb=progress_cb)

    def log(self, msg=""):
        self.log_msg.emit(msg)

    def run(self):
        ret = {
            "result": None,
        }
        try:
            self.log('Initializing planner!')
            # print("Planner thread started: %s" % (threading.get_ident(), ))

            self.planner = get_planner(log=self.log, **self.planner_args)
            self.planner.register_progress_callback(self.progress_cb)
            self.log('Running planner')
            b = Benchmark()
            self.log()
            self.log()
            self.log()
            self.log()
            ret["meta"] = self.planner.run()
            ret["result"] = "ok"
            b.stop()
            self.log('Planner done!  Took : %s' % str(b))
        except MicroscopeStop:
            ret["result"] = "stopped"
        except PlannerStop as e:
            ret["result"] = "stopped"
        except Exception as e:
            self.log('WARNING: planner thread crashed: %s' % str(e))
            traceback.print_exc()
            ret["result"] = "exception"
            ret["exception"] = e
            #raise
        finally:
            self.plannerDone.emit(ret)


class QImageProcessingThread(ImageProcessingThreadBase, ArgusThread):
    log_msg = pyqtSignal(str)

    def __init__(self, ac, parent=None):
        self.ac = ac
        ArgusThread.__init__(self, parent)
        ImageProcessingThreadBase.__init__(self, microscope=ac.microscope)

    def log(self, msg=""):
        self.log_msg.emit(msg)


class QJoystickThread(JoystickThreadBase, ArgusThread):
    log_msg = pyqtSignal(str)
    joystickDone = pyqtSignal()

    def __init__(self, ac, parent=None):
        self.ac = ac
        ArgusThread.__init__(self, parent)
        JoystickThreadBase.__init__(self, microscope=self.ac.microscope)
        self.ac.microscope.joystick = self.joystick
        self.enabled = False
        self.ac.microscope.statistics.add_getj(self.statistics_getj)

    def shutdown_request(self):
        self.running.clear()
        self.enabled = False

    def log(self, msg=""):
        self.log_msg.emit(msg)

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False

    def statistics_getj(self, statj):
        j = statj.setdefault("joystick", {})
        j["thread_running"] = self.running
        j["enabled"] = self.enabled
        j["slow_jogs"] = self.joystick.jog_controller.slow_jogs

    def run(self):
        tlast = time.time()
        while self.running.is_set():
            # is this useful?
            # self.check_stress()
            try:
                waited = time.time() - tlast
                time.sleep(max(0, self.joystick.config.poll_secs() - waited))
                # It is important to check that the button is both enabled and
                # active before performing actions. This allows us to preserve
                # state by disabling and enabling the button only during scans.
                #self.joystick.debug_dump()
                self.joystick.execute(paused=not self.enabled)
                tlast = time.time()
            except Exception as e:
                self.log('WARNING: joystick thread crashed: %s' % str(e))
                traceback.print_exc()
            finally:
                self.joystickDone.emit()
        self.running = False


"""
For offloading general purpose long running tasks
Ex: setting up a scan w/ multiple autofocus steps

TODO:
-Add errors such as number of camera disconnects
-Statistics such as number of images taken
"""


class Profiler:
    def __init__(self, microscope=None):
        self.microscope = microscope
        self.time_last = None
        self.interval = 10.0
        log_file = os.path.join(self.microscope.usc.bc.get_data_dir(),
                                "profile.jl")
        self.f = open(log_file, "a+")
        self.process = psutil.Process()
        self.log_header()
        self.poll()

    def logj(self, j):
        self.f.write(json.dumps(j) + "\n")
        self.f.flush()

    def host_virtual_memory(self):
        mem = psutil.virtual_memory()
        return {
            "percent": mem.percent,
            "total": mem.total,
            "available": mem.available,
            "used": mem.used,
            "free": mem.free,
            "active": mem.active,
            "inactive": mem.inactive,
            "buffers": mem.buffers,
            "cached": mem.cached,
            "shared": mem.shared,
            "slab": mem.slab,
        }

    def host_swap_memory(self):
        mem = psutil.swap_memory()
        return {
            "total": mem.total,
            "used": mem.used,
            "free": mem.free,
            "percent": mem.percent,
            "sin": mem.sin,
            "sout": mem.sout,
        }

    def host_disk_usage(self):
        mem = psutil.disk_usage('/')
        return {
            "total": mem.total,
            "used": mem.used,
            "free": mem.free,
            "percent": mem.percent,
        }

    def process_info(self):
        mi = self.process.memory_info()
        return {
            "rss": mi.rss,
            "vms": mi.vms,
            "shared": mi.shared,
            "text": mi.text,
            "lib": mi.lib,
            "data": mi.data,
            "dirty": mi.dirty,
        }

    def log_header(self):
        j = {
            "type": "profile.header",
            "time": time.time(),
            "utcnow": datetime.datetime.utcnow().isoformat(),
            "microscope": {
                "configuration": self.microscope.config_name(),
                "sn": self.microscope.serial(),
            },
            "host": {
                "sys.version": str(sys.version),
                "sys.platform": str(sys.platform),
                "distro.linux_distribution": distro.linux_distribution(),
                "platform.machine": str(platform.machine),
                "platform.version": str(platform.version()),
                "platform.platform": str(platform.platform()),
                "platform.uname": str(platform.uname()),
                "platform.system": str(platform.system()),
                "platform.release": str(platform.release()),
                "platform.processor": str(platform.processor()),
                "psutil.cpu_count.logical": psutil.cpu_count(logical=True),
                "psutil.cpu_count.physical": psutil.cpu_count(logical=False),
                "psutil.virtual_memory": self.host_virtual_memory(),
                "psutil.swap_memory": self.host_swap_memory(),
                "psutil.disk_usage": self.host_disk_usage(),
            },
            "argus": {
                "profile": self.microscope.bc.profile(),
                "checking_threads": self.microscope.bc.check_threads(),
                "stress_test": self.microscope.bc.stress_test(),
            }
        }
        self.logj(j)

    def poll(self):
        if not (self.time_last is None
                or time.time() - self.time_last >= self.interval):
            return
        j = {
            "type": "profile.poll",
            "time": time.time(),
            "utcnow": datetime.datetime.utcnow().isoformat(),
            "host": {
                "psutil.virtual_memory": self.host_virtual_memory(),
                "psutil.swap_memory": self.host_swap_memory(),
                "psutil.cpu_freq.current": psutil.cpu_freq().current,
                "psutil.cpu_percent": psutil.cpu_percent(),
            },
            "argus": {
                "process": self.process_info(),
            },
            "statistics": self.microscope.statistics.getj()
        }
        self.logj(j)
        self.time_last = time.time()
        return j


class QTaskThread(CommandThreadBase, ArgusThread):
    log_msg = pyqtSignal(str)

    def __init__(self, ac, parent=None):
        ArgusThread.__init__(self, parent)
        self.ac = ac
        CommandThreadBase.__init__(self, microscope=ac.microscope)
        self.command_map = {
            "offload": self._offload,
            "diagnostic_info": self._diagnostic_info,
        }
        self.profiler = None
        self.rss_last = None
        if self.microscope.bc.profile():
            self.profiler = Profiler(self.microscope)

    def offload(self, function, block=False, callback=None):
        self.command("offload", function, block=block, callback=callback)

    def _offload(self, function):
        function(self.ac)

    def diagnostic_info(self, metadata, block=False, callback=None):
        self.command("diagnostic_info",
                     metadata,
                     block=block,
                     callback=callback)

    def _diagnostic_info(self, metadata):
        """
            "argus_cachej": copy.deepcopy(self.ac.mw.cachej),
            "scan_config": scan_config,
            "verbose": verbose,


        imager_state = {
            }
        imager_state["sn"] = self.ac.microscope.imager.get_sn()
        imager_state["prop_cache"] = self.ac.control_scroll.get_prop_cache()

        """
        verbose = metadata.get("verbose", False)
        log = self.ac.microscope.log
        # Note: header was already printed in main thread
        log("System configuration / status")
        log("Microscope")
        log(f"  Configuration: {self.ac.microscope.config_name()}")
        log(f"  Serial: {self.ac.microscope.serial()}")
        log("Host system")
        ver = sys.version.split('\n')[0]
        log(f"  sys.version: {ver}")
        log(f"  sys.platform: {sys.platform}")
        log(f"  distro.linux_distribution: {distro.linux_distribution()}")
        log(f"  platform.machine: {platform.machine()}")
        if verbose:
            log(f"  platform.version: {platform.version()}")
            log(f"  platform.platform: {platform.platform()}")
            log(f"  platform.uname: {platform.uname()}")
            log(f"  platform.system: {platform.system()}")
            log(f"  platform.release: {platform.release()}")
            log(f"  platform.processor: {platform.processor()}")
        log(f"  psutil.cpu_count(logical, physical): {psutil.cpu_count(logical=True)}, {psutil.cpu_count(logical=False)}"
            )
        log(f"  psutil.virtual_memory.total: {psutil.virtual_memory().total}")
        log(f"  psutil.swap_memory.total: {psutil.swap_memory().total}")
        log(f"  psutil.disk_usage: {psutil.disk_usage('/')}")
        log("Imager")
        imager_state = metadata["imager_state"]
        log("  Serial number: " + str(imager_state.get("sn")))
        log("Motion")
        if verbose:
            log("Kinematics")
            self.ac.kinematics.diagnostic_info(indent="  ",
                                               verbose=verbose,
                                               log=log)

        if verbose:
            log("")
            log("")
            log("")
            log("*" * 80)
            log("*" * 80)
            log("Verbose dump")
            log("")
            log("")
            log("")
            log("*" * 80)
            log("Argus GUI state")
            log(
                json.dumps(metadata.get("argus_cachej", {}),
                           sort_keys=True,
                           indent=4,
                           separators=(",", ": ")))
            log("")
            log("")
            log("")
            log("*" * 80)
            log("Objective database")
            try:
                objective_db = self.ac.microscope.get_objectives(
                ).get_full_config()
                log(
                    json.dumps(objective_db,
                               sort_keys=True,
                               indent=4,
                               separators=(",", ": ")))
            except:
                self.log("Exception getting objective database")
            log("")
            log("")
            log("")
            log("*" * 80)
            log("Imager properties cache (as displayed)")
            log(
                json.dumps(imager_state["prop_cache"].get("disp", {}),
                           sort_keys=True,
                           indent=4,
                           separators=(",", ": ")))
            log("")
            log("")
            log("")
            log("*" * 80)
            log("Imager properties cache (raw / low level)")
            log(
                json.dumps(imager_state["prop_cache"].get("raw", {}),
                           sort_keys=True,
                           indent=4,
                           separators=(",", ": ")))
            log("")
            log("")
            log("")
            log("*" * 80)
            log("Next planner configuration")
            log(
                json.dumps(metadata.get("scan_config", {}),
                           sort_keys=True,
                           indent=4,
                           separators=(",", ": ")))
            log("")
            log("")
            log("")
            log("*" * 80)
            log("GRBL configuration")
            self.ac.motion_thread.log_info(block=True)

    def loop_poll(self):
        if self.profiler:
            j = self.profiler.poll()
            if j is not None:
                rss = j["argus"]["process"]["rss"]
                if self.rss_last is None:
                    self.rss_last = rss
                delta = (rss - self.rss_last) / 1e6
                self.microscope.log("Profile: memory usage delta: %0.1f MB" %
                                    (delta, ))
