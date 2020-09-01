from uscope.planner import Planner
from uscope.benchmark import Benchmark
from uscope.hal.cnc.hal import AxisExceeded
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


class CncThread(QThread):
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

    def cmd(self, cmd, *args):
        self.queue.put((cmd, args))

    def pos(self):
        self.lock.set()
        ret = self.hal.pos()
        self.lock.clear()
        return ret

    def run(self):
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
                (cmd, args) = self.queue.get(True, 0.1)
            except queue.Empty:
                self.idle.set()
                continue
            finally:
                self.lock.set()

            self.idle.clear()

            def default(*args):
                raise Exception("Bad command %s" % (cmd, ))

            def mv_abs(pos):
                try:
                    self.hal.mv_abs(pos)
                except AxisExceeded as e:
                    self.log(str(e))
                return self.hal.pos()

            def mv_rel(delta):
                try:
                    self.hal.mv_rel(delta)
                except AxisExceeded as e:
                    self.log(str(e))
                return self.hal.pos()

            def home(axes):
                self.hal.home(axes)
                return self.hal.pos()

            def forever(*args):
                self.hal.forever(*args)
                return self.hal.pos()

            #print 'cnc thread: dispatch %s' % cmd
            # Maybe I should just always emit the pos
            ret = {
                'mv_abs': mv_abs,
                'mv_rel': mv_rel,
                'forever': forever,
                'home': home,
                'stop': self.hal.stop,
                'estop': self.hal.estop,
                'unestop': self.hal.unestop,
            }.get(cmd, default)(*args)
            self.cmd_done(cmd, args, ret)

    def stop(self):
        self.running.clear()


# Sends events to the imaging and movement threads
class PlannerThread(QThread):
    plannerDone = pyqtSignal()
    log_msg = pyqtSignal(str)

    def __init__(self, parent, rconfig, imagerj={}):
        QThread.__init__(self, parent)
        self.rconfig = rconfig
        self.imagerj = imagerj
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

            scan_config = json.load(open('scan.json'))

            rconfig = self.rconfig
            # FIXME: ideally should prioritize objective
            im_scalar = float(rconfig['uscope']['imager']['scalar'])
            obj = rconfig['uscope']['objective'][rconfig['obj']]
            im_w_pix = int(rconfig['uscope']['imager']['width']) * im_scalar
            im_h_pix = int(rconfig['uscope']['imager']['height']) * im_scalar
            x_um = float(obj['x_view'])
            self.planner = Planner(scan_config=scan_config,
                                   hal=rconfig['cnc_hal'],
                                   imager=rconfig['imager'],
                                   img_sz=(im_w_pix, im_h_pix),
                                   unit_per_pix=(x_um / im_w_pix),
                                   out_dir=rconfig['out_dir'],
                                   progress_cb=rconfig['progress_cb'],
                                   dry=rconfig['dry'],
                                   log=self.log,
                                   verbosity=2,
                                   imagerj=self.imagerj)
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
