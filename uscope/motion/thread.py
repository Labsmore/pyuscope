from uscope.motion.plugins import get_motion_hal
from uscope.motion.hal import AxisExceeded, MotionHAL, MotionCritical
from uscope.threads import CommandThreadBase

import threading
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
        MotionHAL.__init__(self,
                           log=mt.motion.log,
                           verbose=mt.motion.verbose,
                           microscope=self.mt.ac.microscope)

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

    def _get_max_velocities(self):
        return self.mt.motion.get_max_velocities()

    def _get_max_accelerations(self):
        return self.mt.motion.get_max_accelerations()

    def jog_rel(self, pos, rate):
        return self.mt.jog_rel(pos, rate)

    def jog_cancel(self):
        return self.mt.jog_cancel()

    def command(self, command):
        return self.mt.mdi(command)


"""
Several sources need to periodically send jog commands based on stimulus
Helps decide when to cancel jog
Standard period is 0.2 sec => update every 0.2 to 0.3 seconds
"""


class JogController:
    def __init__(self, motion_thread, period):
        self.motion_thread = motion_thread
        self.period = period
        self.jogging = False
        self.dts = []
        self.tlast = None

    def update(self, axes):
        # XXX: adjust jogging based on actual loop time instead of estimated?
        tthis = time.time()
        period = self.period
        this_dt = None
        if self.tlast is not None:
            this_dt = tthis - self.tlast
            if this_dt < self.period:
                """
                looks like qt can undershoot period maybe depending on how events stack up
                assert 0, f"JOG: actual loop time {dt} is less than estimated period {self.period}. GRBL jog queue may overflow"
                JOG WARNING: actual loop time 0.16197466850280762 is less than estimated period 0.2. GRBL jog queue may overflow
                JOG WARNING: actual loop time 0.189927339553833 is less than estimated period 0.2. GRBL jog queue may overflow
                JOG WARNING: actual loop time 0.18992257118225098 is less than estimated period 0.2. GRBL jog queue may overflow
                """
                0 and print(
                    f"JOG WARNING: actual loop time {this_dt} is less than estimated period {self.period}. GRBL jog queue may overflow"
                )
            if this_dt > 1.5 * self.period:
                1 and print(
                    f"JOG WARNING: actual loop time {this_dt} is significantly larger than estimated period {self.period}. Jogging may stutter"
                )

            # Try to figure out the typical loop time
            # Take the median over the last few measurements
            self.dts.append(this_dt)
            if len(self.dts) > 5:
                self.dts = self.dts[1:]
            # take median dt
            dt = sorted(self.dts)[len(self.dts) // 2]

            period = dt

        # Most of the time these will be 0
        for axis, value in list(axes.items()):
            if self.motion_thread.motion.is_zero(axis, value):
                del axes[axis]

        if len(axes):
            # XXX: what if it starts dropping commands?
            # print(self._jog_queue)
            # print("JC: submit", time.time(), this_dt)
            # TODO: consider squishing them into valid range
            for axis, value in axes.items():
                assert -1 <= value <= +1, f"bad jog value {axis} : {value}"
            self.motion_thread.jog_fractioned_lazy(axes, period=period)
            self.jogging = True
        # Was jogging but no longer?
        elif self.jogging:
            # print("JC: cancel", time.time())
            # Unclear which of these is better
            # If jogs are queued up its better to force a stop?
            # Could make it slightly less responsive if you are zero crossing
            # but the queue was going to go through there anyway
            # However the queue is also processed quickly, so a jog cancel
            # should be quick
            # self.motion_thread.jog_cancel()
            # stop will cause lots of weird side affects
            # do the cleaner jog cancel which should execute soon enough
            self.motion_thread.jog_cancel()
            self.jogging = False

        self.tlast = tthis

    def pause(self):
        self.tlast = None


class MotionThreadBase(CommandThreadBase):
    def __init__(self, microscope):
        super().__init__(microscope)
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
        self._jog_enabled = True

        # Seed state / refuse to start without motion
        self.init_motion()

    def log(self, msg=""):
        print(msg)

    def init_motion(self):
        self.motion = get_motion_hal(usc=self.ac.microscope.usc,
                                     log=self.log,
                                     microscope=self.microscope)

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

    # Set at beginning / end of scan
    # Silently drops all jog commands while set
    # Lazy way of being sure nothing gets through
    # XXX: there might be race conditions with stop()
    # need to rethink this a bit
    def jog_enable(self, val):
        self._jog_enabled = val

    def jog_fractioned(self, axes, period=1.0):
        """
        An easier to use command using fractions of max velocity
        Each axis should be in scale 0 to 1
        Where 1 represents a jog at max velocity
        timeout: if you want
        """
        self.command("jog_fractioned", axes, period)

    def jog_fractioned_lazy(self, axes, period):
        # WARNING: GRBL controller will queue
        # so with relative jogs be careful
        # Also update_pos_cache() may queue up
        # So just prevent excessive queueing
        if self.qsize() < 4:
            self.jog_fractioned(axes, period)
        else:
            print("WARNING: drop jog on backing up queue")

    def jog_rel(self, pos, rate):
        self.command("jog_rel", pos, rate)

    def jog_abs(self, pos, rate):
        self.command("jog_abs", pos, rate)

    def jog_abs_lazy(self, pos, rate):
        """
        Only jog if events haven't already stacked up high
        """
        if self.qsize() < 1:
            self.command("jog_abs", pos, rate)

    def jog_cancel(self):
        self.command("jog_cancel")

    def get_jog_controller(self, period):
        return JogController(self, period=period)

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

    def move_absolute(self, pos, block=False, callback=None, done=None):
        self.command("move_absolute",
                     pos,
                     block=block,
                     callback=callback,
                     done=done)

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

    def _jog_rel(self, *args, **kwargs):
        if self._jog_enabled:
            self.motion.jog_rel(*args, **kwargs)
        else:
            self.log("WARNING: jog disabled, dropping jog")

    def _jog_abs(self, *args, **kwargs):
        if self._jog_enabled:
            self.motion.jog_abs(*args, **kwargs)
        else:
            self.log("WARNING: jog disabled, dropping jog")

    def _jog_fractioned(self, *args, **kwargs):
        if self._jog_enabled:
            self.motion.jog_fractioned(*args, **kwargs)
        else:
            self.log("WARNING: jog disabled, dropping jog")

    def _jog_cancel(self, *args, **kwargs):
        if self._jog_enabled:
            self.motion.jog_cancel()
        else:
            self.log("WARNING: jog disabled, dropping jog cancel")

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
                if 0 and command not in ("update_pos_cache", "pos"):
                    print("running", command, args)
                #print 'cnc thread: dispatch %s' % command
                # Maybe I should just always emit the pos
                f = {
                    'update_pos_cache': update_pos_cache,
                    'move_absolute': move_absolute,
                    'move_relative': move_relative,
                    'jog_rel': self._jog_rel,
                    'jog_abs': self._jog_abs,
                    'jog_fractioned': self._jog_fractioned,
                    'jog_cancel': self._jog_cancel,
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
                tstart = time.time()
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
                # motion command update_pos_cache completed in 0.006439208984375
                # motion command jog_fractioned completed in 0.021370649337768555
                # motion command update_pos_cache completed in 0.21372413635253906
                # motion command jog_fractioned completed in 0.27429747581481934
                # why does this sometimes take much longer?
                0 and print(f"motion command {command} completed in",
                            time.time() - tstart)

                if command_done:
                    command_done(command, args, ret)

        finally:
            if self.motion:
                self.motion.stop()
                # self.motion.ar_stop()


class SimpleMotionThread(MotionThreadBase, threading.Thread):
    pass
