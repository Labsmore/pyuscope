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


def dbg(*args):
    if len(args) == 0:
        print()
    elif len(args) == 1:
        print('threading: %s' % (args[0], ))
    else:
        print('threading: ' + (args[0] % args[1:]))


# FIXME: merge this into image processing thread
class StitcherThread(CommandThreadBase, QThread):
    # stitcherDone = pyqtSignal()
    log_msg = pyqtSignal(str)

    def __init__(self, ac, parent=None):
        QThread.__init__(self, parent)
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
        cs_info,
    ):
        j = {
            #"type": "imagep",
            "directory": directory,
            "cs_info": cs_info,
        }
        self.command("imagep", j)

    def _imagep_run(self, j):
        # Taking too much CPU
        # For now let's kick off to own process
        # process_dir(directory=j["directory"], cs_info=j["cs_info"])

        self.log(f"Image processing CLI: kicking off {j['directory']}")
        # Hacky but good enough for now
        # Check terminal for process output
        print("")
        print("")
        print("")
        print(f"Image processing CLI: kicking off {j['directory']}")
        cs_info = j["cs_info"]
        args = [
            "./utils/cs_auto.py", "--access-key",
            cs_info.access_key(), "--secret-key",
            cs_info.secret_key(), "--id-key",
            cs_info.id_key(), "--notification-email",
            cs_info.notification_email(), j["directory"]
        ]
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
        self.log(f"Image processing CLI: finished job w/ code {return_code}")
        print(f"Image processing CLI: finished job w/ code {return_code}")


class QMotionThread(MotionThreadBase, QThread):
    log_msg = pyqtSignal(str)

    def __init__(self, ac, parent=None):
        QThread.__init__(self, parent)
        self.ac = ac
        MotionThreadBase.__init__(self, microscope=self.ac.microscope)

    def log(self, msg=""):
        self.log_msg.emit(msg)


class QPlannerThread(PlannerThreadBase, QThread):
    plannerDone = pyqtSignal(dict)
    log_msg = pyqtSignal(str)

    def __init__(self, planner_args, progress_cb, parent=None):
        QThread.__init__(self, parent)
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


class QImageProcessingThread(ImageProcessingThreadBase, QThread):
    log_msg = pyqtSignal(str)

    def __init__(self, ac, parent=None):
        QThread.__init__(self, parent)
        ImageProcessingThreadBase.__init__(self, microscope=ac.microscope)
        self.snapshotCaptured = ac.snapshotCaptured
        self.objective_config = ac.objective_config
        self.imaging_config = ac.imaging_config

    def log(self, msg=""):
        self.log_msg.emit(msg)

    def _do_process_image(self, j):
        obj_config = self.objective_config()
        imaging_config = self.imaging_config()
        plugins = {}
        if imaging_config.get("add_scalebar", False):
            plugins["annotate-scalebar"] = {}
        j["options"]["objective_config"] = obj_config
        j["options"]["plugins"] = plugins
        image = super()._do_process_image(j)
        data = {"image": image, "objective_config": self.objective_config()}
        # Don't emit when scripting collects a snapshot
        if j["options"].get("is_snapshot", False):
            self.snapshotCaptured.emit(data)
        return image


class QJoystickThread(JoystickThreadBase, QThread):
    log_msg = pyqtSignal(str)
    joystickDone = pyqtSignal()

    def __init__(self, ac, parent=None):
        self.ac = ac
        QThread.__init__(self, parent)
        JoystickThreadBase.__init__(self, microscope=self.ac.microscope)
        self.ac.microscope.joystick = self.joystick
        self.enabled = False

    def log(self, msg=""):
        self.log_msg.emit(msg)

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False

    def run(self):
        tlast = time.time()
        while self.running:
            try:
                waited = time.time() - tlast
                time.sleep(max(0, self.joystick.config.scan_secs() - waited))
                # It is important to check that the button is both enabled and
                # active before performing actions. This allows us to preserve
                # state by disabling and enabling the button only during scans.
                if self.enabled:
                    #self.joystick.debug_dump()
                    self.joystick.execute()
                else:
                    self.joystick.jog_controller.pause()
                tlast = time.time()
            except Exception as e:
                self.log('WARNING: joystick thread crashed: %s' % str(e))
                traceback.print_exc()
            finally:
                self.joystickDone.emit()


"""
For offloading general purpose long running tasks
Ex: setting up a scan w/ multiple autofocus steps
"""


class QTaskThread(CommandThreadBase, QThread):
    log_msg = pyqtSignal(str)

    def __init__(self, ac, parent=None):
        QThread.__init__(self, parent)
        self.ac = ac
        CommandThreadBase.__init__(self, microscope=ac.microscope)
        self.command_map = {
            "offload": self._offload,
        }

    def offload(self, function, block=False, callback=None):
        self.command("offload", function, block=block, callback=callback)

    def _offload(self, function):
        function(self.ac)
