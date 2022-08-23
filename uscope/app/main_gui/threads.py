from uscope.planner import Planner
from uscope.benchmark import Benchmark
from uscope.motion.hal import AxisExceeded
import traceback

import queue
import threading
from PyQt5.QtCore import *
import time
import os
import json


def dbg(*args):
    if len(args) == 0:
        print()
    elif len(args) == 1:
        print('threading: %s' % (args[0], ))
    else:
        print('threading: ' + (args[0] % args[1:]))


'''
Offloads controller processing to another thread (or potentially even process)
Makes it easier to keep RT deadlines and such
However, it doesn't provide feedback completion so use with care
(other blocks until done)
TODO: should block?
'''


class MotionThread(QThread):
    log_msg = pyqtSignal(str)

    def __init__(self, hal, cmd_done):
        QThread.__init__(self)
        self.queue = queue.Queue()
        self.hal = hal
        self.running = threading.Event()
        self.idle = threading.Event()
        self.idle.set()
        self.normal_running = threading.Event()
        self.normal_running.set()
        self.cmd_done = cmd_done
        self.lock = threading.Event()
        # Let main gui get the last position from a different thread
        # It can request updates
        self.pos_cache = None

    def log(self, msg):
        self.log_msg.emit(msg)

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

    def command(self, command, *args):
        self.queue.put((command, args))

    def pos(self):
        self.lock.set()
        ret = self.hal.pos()
        self.lock.clear()
        return ret

    def mdi(self, cmd):
        self.command("mdi", cmd)

    def jog(self, pos):
        self.command("jog", pos)

    def set_jog_rate(self, rate):
        self.command("set_jog_rate", rate)

    def cancel_jog(self):
        self.command("cancel_jog")

    def update_pos_cache(self):
        self.command("update_pos_cache")

    def run(self):
        print("Motion thread started: %s" % (threading.get_ident(), ))
        self.running.set()
        self.idle.clear()
        self.hal.on()

        while self.running.is_set():
            self.lock.set()
            if not self.normal_running.isSet():
                self.normal_running.wait(0.1)
                continue
            try:
                self.lock.clear()
                (command, args) = self.queue.get(True, 0.1)
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
                    self.hal.move_absolute(pos)
                except AxisExceeded as e:
                    self.log(str(e))
                return self.hal.pos()

            def move_relative(delta):
                try:
                    self.hal.move_relative(delta)
                except AxisExceeded as e:
                    self.log(str(e))
                return self.hal.pos()

            def update_pos_cache():
                self.pos_cache = self.hal.pos()

            #print 'cnc thread: dispatch %s' % command
            # Maybe I should just always emit the pos
            f = {
                'update_pos_cache': update_pos_cache,
                'move_absolute': move_absolute,
                'move_relative': move_relative,
                'jog': self.hal.jog,
                'set_jog_rate': self.hal.set_jog_rate,
                'cancel_jog': self.hal.cancel_jog,
                'home': self.hal.home,
                'stop': self.hal.stop,
                'estop': self.hal.estop,
                'unestop': self.hal.unestop,
                'mdi': self.hal.command,
            }.get(command, default)
            try:
                ret = f(*args)
            except Exception as e:
                print("")
                print("WARNING: motion thread crashed")
                print(traceback.format_exc())
                self.cmd_done(command, args, e)
                continue

            self.cmd_done(command, args, ret)

    def stop(self):
        self.running.clear()


"""
Sends events to the imaging and movement threads

rconfig: misc parmeters including complex objects
plannerj: planner configuration JSON. Written to disk
"""


class PlannerThread(QThread):
    plannerDone = pyqtSignal()
    log_msg = pyqtSignal(str)

    def __init__(self, parent, pconfig):
        QThread.__init__(self, parent)
        self.pconfig = pconfig
        self.planner = None

    def log(self, msg):
        #print 'emitting log %s' % msg
        #self.log_buff += str(msg) + '\n'
        self.log_msg.emit(msg)

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

    def stop(self):
        if self.planner:
            self.planner.stop()

    def run(self):
        try:
            self.log('Initializing planner!')
            print("Planner thread started: %s" % (threading.get_ident(), ))

            self.planner = Planner(log=self.log, **self.pconfig)
            self.log('Running planner')
            b = Benchmark()
            self.planner.run()
            b.stop()
            self.log('Planner done!  Took : %s' % str(b))
        except Exception as e:
            self.log('WARNING: planner thread crashed: %s' % str(e))
            traceback.print_exc()
            #raise
        finally:
            self.plannerDone.emit()
