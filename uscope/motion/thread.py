from uscope.motion.plugins import get_motion_hal
from uscope.motion.hal import AxisExceeded, MotionHAL, MotionCritical

import threading
from PyQt5.QtCore import QThread, pyqtSignal
import queue
import time
import datetime
import traceback
'''
Offloads controller processing to another thread (or potentially even process)
Makes it easier to keep RT deadlines and such
However, it doesn't provide feedback completion so use with care
(other blocks until done)
TODO: should block?
'''


class MotionThreadMotion(MotionHAL):
    def __init__(self, mt):
        self.mt = mt
        MotionHAL.__init__(self, log=mt.motion.log, verbose=mt.motion.verbose)

        # Don't re-apply pipeline (scaling, etc)
        self.configure({})

    def axes(self):
        return self.mt.motion.axes()

    def home(self, axes):
        self.mt.home(block=True)

    def _move_absolute(self, pos):
        self.mt.move_absolute(pos, block=True)

    def _move_relative(self, pos):
        self.mt.move_relative(pos, block=True)

    def _pos(self):
        # return self.mt.pos_cache
        return self.mt.pos()

    def settle(self):
        # No hardware to let settle
        pass

    def ar_stop(self):
        pass

    def backlash_disable(self):
        self.mt.backlash_disable(block=True)

    def backlash_enable(self):
        self.mt.backlash_enable(block=True)


class MotionThreadBase:
    def __init__(self, usc):
        self.usc = usc
        self.verbose = False
        self.queue = queue.Queue()
        self.motion = None
        self.running = threading.Event()
        self.idle = threading.Event()
        self.idle.set()
        self.normal_running = threading.Event()
        self.normal_running.set()
        self.lock = threading.Event()
        # Let main gui get the last position from a different thread
        # It can request updates
        self.pos_cache = None
        self._stop = False
        self._estop = False
        # XXX: add config directive
        self.allow_motion_reboot = False

        # Seed state / refuse to start without motion
        self.init_motion()

    def log(self, msg):
        print(msg)

    def init_motion(self):
        self.motion = get_motion_hal(usc=self.usc, log=self.log)

    def log(self, msg):
        self.log_msg.emit(msg)

    def log_info(self):
        self.command("log_info")

    def setRunning(self, running):
        if running:
            self.normal_running.set()
        else:
            self.normal_running.clear()

    def wait_idle(self):
        while True:
            time.sleep(0.15)
            if self.idle.is_set():
                break

    def command(self, command, *args, block=False, callback=None):
        command_done = None
        if block or callback:
            ready = threading.Event()
            ret = []

            def command_done(command, args, ret_e):
                ret.append(ret_e)
                ready.set()
                if callback:
                    callback()

        self.queue.put((command, args, command_done))
        if block:
            ready.wait()
            ret = ret[0]
            if type(ret) is Exception:
                raise Exception("oopsie: %s" % (ret, ))
            return ret

    def pos(self):
        # XXX: this caused crashes but I'm not sure why
        # Just offload to the thread to avoid this special case
        if 0:
            self.lock.set()
            ret = self.motion.pos()
            self.lock.clear()
            return ret
        else:
            return self.command("pos", block=True)

    def mdi(self, cmd):
        self.command("mdi", cmd)

    def jog_fractioned(self, axes, period=1.0):
        """
        An easier to use command using fractions of max velocity
        Each axis should be in scale 0 to 1
        Where 1 represents a jog at max velocity
        timeout: if you want
        """
        self.command("jog_fractioned", axes, period)

    def jog_fractioned_lazy(self, axes, period=1.0):
        if self.qsize() < 1:
            self.jog_fractioned(axes, period)

    def jog(self, pos, rate):
        self.command("jog", pos, rate)

    def jog_lazy(self, pos, rate):
        """
        Only jog if events haven't already stacked up high
        """
        if self.qsize() < 1:
            self.command("jog", pos, rate)

    def stop(self):
        # self.command("stop")
        self._stop = True

    def estop(self):
        # self.command("estop")
        self._estop = True

    def home(self, block=False):
        self.command("home", block=block)

    def backlash_disable(self, block=False):
        self.command("backlash_disable", block=block)

    def backlash_enable(self, block=False):
        self.command("backlash_enable", block=block)

    def move_absolute(self, pos, block=False, callback=None):
        self.command("move_absolute", pos, block=block, callback=callback)

    def move_relative(self, pos, block=False, callback=None):
        self.command("move_relative", pos, block=block, callback=callback)

    def update_pos_cache(self):
        self.command("update_pos_cache")

    def qsize(self):
        return self.queue.qsize()

    def queue_clear(self):
        while True:
            try:
                self.queue.get(block=False)
            except queue.Empty:
                break

    def get_planner_motion(self):
        return MotionThreadMotion(self)

    def shutdown(self):
        self.running.clear()

    def run(self):
        self.verbose and print("Motion thread started: %s" %
                               (threading.get_ident(), ))
        self.running.set()
        self.idle.clear()
        self.motion.on()

        def motion_status(status):
            # print("register_status_cb: via motion-status: %s" % (status,))
            self.pos_cache = status["pos"]

        self.motion.register_status_cb(motion_status)

        try:
            while self.running.is_set():
                self.lock.set()

                if not self.motion:
                    if not self.allow_motion_reboot:
                        self.log("Fatal error: motion controller is dead")
                        break
                    else:
                        # See if its back...
                        try:
                            self.init_motion()
                            self.motion.on()
                        except Exception as e:
                            self.log(
                                "Failed to reboot motion controller :( %s" %
                                (str(e), ))
                            time.sleep(3)
                        continue

                if self._estop:
                    self.motion.estop()
                    self.queue_clear()
                    self._estop = False
                    continue

                if self._stop:
                    self.motion.stop()
                    self.queue_clear()
                    self._stop = False
                    continue

                if not self.normal_running.isSet():
                    self.normal_running.wait(0.1)
                    continue
                try:
                    self.lock.clear()
                    (command, args, command_done) = self.queue.get(True, 0.1)
                except queue.Empty:
                    self.idle.set()
                    continue
                finally:
                    self.lock.set()

                self.idle.clear()

                def default(*args):
                    raise Exception("Bad command %s" % (command, ))

                def move_absolute(pos):
                    try:
                        self.motion.move_absolute(pos)
                    except AxisExceeded as e:
                        self.log(str(e))
                    return self.motion.pos()

                def move_relative(pos):
                    try:
                        self.motion.move_relative(pos)
                    except AxisExceeded as e:
                        self.log(str(e))
                    return self.motion.pos()

                def update_pos_cache():
                    pos = self.motion.pos()
                    self.pos_cache = pos
                    # print("register_status_cb: via update_pos_cache: %s" % (pos,))

                self.verbose and print("")
                self.verbose and print(
                    "process @ %s" % datetime.datetime.utcnow().isoformat())
                #print 'cnc thread: dispatch %s' % command
                # Maybe I should just always emit the pos
                f = {
                    'update_pos_cache': update_pos_cache,
                    'move_absolute': move_absolute,
                    'move_relative': move_relative,
                    'jog': self.motion.jog,
                    'jog_fractioned': self.motion.jog_fractioned,
                    'pos': self.motion.pos,
                    'home': self.motion.home,
                    'backlash_disable': self.motion.backlash_disable,
                    'backlash_enable': self.motion.backlash_enable,
                    # 'stop': self.motion.stop,
                    # 'estop': self.motion.estop,
                    'unestop': self.motion.unestop,
                    'mdi': self.motion.command,
                    'log_info': self.motion.log_info,
                }.get(command, default)
                try:
                    ret = f(*args)
                # Depending on the motion controller this may be a bad idea
                # Only some of them retain the old coordinate system / may need re-home
                except MotionCritical as e:
                    self.log(
                        f"ERROR: motion thread crashed with critical error: {e}"
                    )
                    print("")
                    print("ERROR: motion controller crashed w/ critical error")
                    print(traceback.format_exc())
                    self.motion.close()
                    self.motion = None
                    if command_done:
                        command_done(command, args, e)
                    continue
                except AxisExceeded as e:
                    self.log(f"Motion command failed: {e}")
                except Exception as e:
                    self.log(f"WARNING: motion thread crashed: {e}")
                    print("")
                    print("WARNING: motion thread crashed")
                    print(traceback.format_exc())
                    if command_done:
                        command_done(command, args, e)
                    continue

                if command_done:
                    command_done(command, args, ret)

        finally:
            if self.motion:
                self.motion.stop()
                # self.motion.ar_stop()


class SimpleMotionThread(MotionThreadBase, threading.Thread):
    pass


class QMotionThread(MotionThreadBase, QThread):
    log_msg = pyqtSignal(str)

    def __init__(self, usc):
        QThread.__init__(self)
        MotionThreadBase.__init__(self, usc=usc)

    def log(self, msg):
        self.log_msg.emit(msg)
