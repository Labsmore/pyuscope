from uscope.planner.planner_util import get_planner
from uscope.planner.planner import PlannerStop
from uscope.benchmark import Benchmark
from uscope.joystick import Joystick, JoystickNotFound
from uscope import cloud_stitch
from uscope import config
from uscope.imager.autofocus import choose_best_image
from PyQt5.QtCore import QThread, pyqtSignal
import traceback
import datetime
import queue
import threading
import time
from queue import Queue, Empty
import subprocess
from uscope.planner.planner_util import microscope_to_planner_config
from uscope.kinematics import Kinematics
from uscope.imagep.pipeline import process_dir
import psutil
import sys


def dbg(*args):
    if len(args) == 0:
        print()
    elif len(args) == 1:
        print('threading: %s' % (args[0], ))
    else:
        print('threading: ' + (args[0] % args[1:]))


"""
Sends events to the imaging and movement threads

rconfig: misc parmeters including complex objects
plannerj: planner configuration JSON. Written to disk
"""


class PlannerThread(QThread):
    plannerDone = pyqtSignal(dict)
    log_msg = pyqtSignal(str)

    def __init__(self, parent, planner_args, progress_cb):
        QThread.__init__(self, parent)
        self.planner_args = planner_args
        self.planner = None
        self.progress_cb = progress_cb

    def log(self, msg=""):
        #print 'emitting log %s' % msg
        #self.log_buff += str(msg) + '\n'
        self.log_msg.emit(str(msg))

    def setRunning(self, running):
        planner = self.planner
        if planner:
            planner.setRunning(running)

    def is_paused(self):
        if self.planner:
            return self.planner.is_paused()
        return False

    def pause(self):
        if self.planner:
            self.planner.pause()

    def unpause(self):
        if self.planner:
            self.planner.unpause()

    def shutdown(self):
        if self.planner:
            self.planner.stop()

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


class ImageProcessingThread(QThread):
    log_msg = pyqtSignal(str)

    def __init__(self, motion_thread, ac, parent=None):
        QThread.__init__(self, parent)
        self.queue = Queue()
        self.running = threading.Event()
        self.running.set()
        self.motion_thread = motion_thread
        self.ac = ac
        self.imager = self.ac.imager
        self.kinematics = None

        self.kinematics = Kinematics(
            microscope=self.ac.microscope,
            log=self.log,
        )
        self.kinematics.configure()

    def log(self, msg):
        self.log_msg.emit(msg)

    def shutdown(self):
        self.running.clear()

    def command(self, command, block=False, callback=None):
        command_done = None
        if block or callback:
            ready = threading.Event()
            ret = []

            def command_done(command, ret_e):
                ret.append(ret_e)
                ready.set()
                if callback:
                    callback()

        self.queue.put((command, command_done))
        if block:
            ready.wait()
            ret = ret[0]
            if type(ret) is Exception:
                raise Exception("oopsie: %s" % (ret, ))
            return ret

    def auto_focus(self, block=False, callback=None):
        j = {
            "type": "auto_focus",
        }
        self.command(j, block=block, callback=callback)

    def move_absolute(self, pos):
        self.motion_thread.move_absolute(pos, block=True)
        self.kinematics.wait_imaging_ok()

    def pos(self):
        return self.motion_thread.pos()

    def auto_focus_pass(self, step_size, step_pm):
        """
        for outer_i in range(3):
            self.log("autofocus: try %u / 3" % (outer_i + 1,))
            # If we are reasonably confident we found the local minima stop
            # TODO: if repeats should bias further since otherwise we are repeating steps
            if abs(step_pm - fni) <= 2:
                self.log("autofocus: converged")
                return
        self.log("autofocus: timed out")
        """

        # Very basic short range
        start_pos = self.pos()["z"]
        steps = step_pm * 2 + 1

        # Doing generator allows easier to process images as movement is done / settling
        def gen_images():
            for focusi in range(steps):
                # FIXME: use backlash compensation direction here
                target_pos = start_pos + -(focusi - step_pm) * step_size
                self.log("autofocus round %u / %u: try %0.6f" %
                         (focusi + 1, steps, target_pos))
                self.move_absolute({"z": target_pos})
                im_pil = self.imager.get()["0"]
                yield target_pos, im_pil

        target_pos, fni = choose_best_image(gen_images())
        self.log("autofocus: set %0.6f at %u / %u" %
                 (target_pos, fni + 1, steps))
        self.move_absolute({"z": target_pos})

    def do_auto_focus(self):
        # MVP intended for 20x
        # 2 um is standard focus step size
        self.log("autofocus: coarse")
        self.auto_focus_pass(step_size=0.006, step_pm=3)
        self.log("autofocus: medium")
        self.auto_focus_pass(step_size=0.002, step_pm=3)
        self.log("autofocus: done")

    def run(self):
        while self.running:
            try:
                j, command_done = self.queue.get(block=True, timeout=0.1)
            except Empty:
                continue
            try:
                if j["type"] == "auto_focus":
                    self.do_auto_focus()
                else:
                    assert 0, j

                if command_done:
                    command_done(j, None)

            except Exception as e:
                self.log('WARNING: image processing thread crashed: %s' %
                         str(e))
                traceback.print_exc()
                if command_done:
                    command_done(j, e)
            finally:
                # self.stitcherDone.emit()
                pass


class JoystickThread(QThread):
    joystickDone = pyqtSignal()
    log_msg = pyqtSignal(str)

    def __init__(self, ac):
        QThread.__init__(self)
        self.joystick = None
        self.ac = ac
        self.queue = Queue()
        self.running = threading.Event()
        self.running.set()
        try:
            self.joystick = Joystick(microscope=self.ac.microscope)
        except JoystickNotFound:
            raise JoystickNotFound()
        self.ac.microscope.joystick = self.joystick

    def log_info(self):
        self.log("Joystick")
        self.log(f"  Name: {self.joystick.joystick.name}")
        self.log(f"  Axes: {self.joystick.joystick.numaxes}")
        self.log(f"  Trackballs: {self.joystick.joystick.numballs}")
        self.log(f"  Hats: {self.joystick.joystick.numhats}")
        self.log(f"  Buttons: {self.joystick.joystick.numbuttons}")

    def log(self, msg):
        self.log_msg.emit(msg)

    def shutdown(self):
        self.running.clear()

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
