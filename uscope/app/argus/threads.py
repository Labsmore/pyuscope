from uscope.planner.planner_util import get_planner
from uscope.planner.planner import PlannerStop
from uscope.benchmark import Benchmark
from uscope import cloud_stitch
from uscope.imagep.thread import ImageProcessingThreadBase
from uscope.planner.thread import PlannerThreadBase
from uscope.motion.thread import MotionThreadBase
from uscope.joystick_thread import JoystickThreadBase
from PyQt5.QtCore import QThread, pyqtSignal
import traceback
import threading
import time
from queue import Queue, Empty
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
class StitcherThread(QThread):
    # stitcherDone = pyqtSignal()
    log_msg = pyqtSignal(str)

    def __init__(self, parent=None):
        QThread.__init__(self, parent)
        self.queue = Queue()
        self.running = threading.Event()
        self.running.set()

    def log(self, msg):
        self.log_msg.emit(msg)

    def shutdown(self):
        self.running.clear()

    def cli_stitch_add(self, directory, command):
        j = {
            "type": "cli",
            "directory": directory,
            "command": command,
        }
        self.queue.put(j)

    # Offload uploads etc to thread since they might take a while
    def cloud_stitch_add(
        self,
        directory,
        cs_info,
    ):

        j = {
            "type": "CloudStitch",
            "directory": directory,
            "cs_info": cs_info,
        }
        self.queue.put(j)

    def imagep_add(
        self,
        directory,
        cs_info,
    ):

        j = {
            "type": "imagep",
            "directory": directory,
            "cs_info": cs_info,
        }
        self.queue.put(j)

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

    def run(self):
        while self.running:
            try:
                j = self.queue.get(block=True, timeout=0.1)
            except Empty:
                continue
            try:
                if j["type"] == "CloudStitch":
                    cloud_stitch.upload_dir(directory=j["directory"],
                                            cs_info=j["cs_info"],
                                            log=self.log,
                                            running=self.running)
                elif j["type"] == "imagep":
                    self._imagep_run(j)
                elif j["type"] == "cli":
                    self.log(
                        f"Stitch CLI: kicking off {j['command']} {j['directory']}"
                    )
                    # Hacky but good enough for now
                    # Check terminal for process output
                    print("")
                    print("")
                    print("")
                    print(
                        f"Stitch CLI: kicking off {j['command']} {j['directory']}"
                    )
                    subprocess.check_call([j['command'], j['directory']])
                    self.log(f"Stitch CLI: finished job")
                    print(f"Stitch CLI: finished job")
                else:
                    assert 0, j

            except Exception as e:
                self.log('WARNING: stitcher thread crashed: %s' % str(e))
                traceback.print_exc()
            finally:
                # self.stitcherDone.emit()
                pass


class QMotionThread(MotionThreadBase, QThread):
    log_msg = pyqtSignal(str)

    def __init__(self, usc, parent=None):
        QThread.__init__(self, parent)
        MotionThreadBase.__init__(self, usc=usc)

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

    def log(self, msg=""):
        self.log_msg.emit(msg)


class QJoystickThread(JoystickThreadBase, QThread):
    log_msg = pyqtSignal(str)
    joystickDone = pyqtSignal()

    def __init__(self, ac, parent=None):
        self.ac = ac
        QThread.__init__(self, parent)
        JoystickThreadBase.__init__(self, microscope=self.ac.microscope)
        self.ac.microscope.joystick = self.joystick

    def log(self, msg=""):
        self.log_msg.emit(msg)

    def run(self):
        while self.running:
            try:
                time.sleep(self.ac.bc.joystick.scan_secs())
                # It is important to check that the button is both enabled and
                # active before performing actions. This allows us to preserve
                # state by disabling and enabling the button only during scans.
                if self.ac.mw.mainTab.motion_widget.joystick_listener.joystick_executing:
                    #self.joystick.debug_dump()
                    self.joystick.execute()
            except Exception as e:
                self.log('WARNING: joystick thread crashed: %s' % str(e))
                traceback.print_exc()
            finally:
                self.joystickDone.emit()
