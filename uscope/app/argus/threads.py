from uscope.planner.planner_util import get_planner
from uscope.benchmark import Benchmark
from uscope.motion.hal import AxisExceeded, MotionHAL
from PyQt5.QtCore import QThread, pyqtSignal
import traceback
try:
    import boto3
except ImportError:
    boto3 = None
import datetime
import io
import os
import queue
import threading
import time


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


class MotionThreadMotion(MotionHAL):
    def __init__(self, mt):
        self.mt = mt
        MotionHAL.__init__(
            self,
            # Don't re-apply pipeline (scaling, etc)
            options={},
            log=mt.motion.log,
            verbose=mt.motion.verbose)

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


class MotionThread(QThread):
    log_msg = pyqtSignal(str)

    def __init__(self, motion):
        QThread.__init__(self)
        self.verbose = False
        self.queue = queue.Queue()
        self.motion = motion
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

    def command(self, command, *args, block=False):
        command_done = None
        if block:
            ready = threading.Event()
            ret = []

            def command_done(command, args, ret_e):
                ret.append(ret_e)
                ready.set()

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

    def jog(self, pos):
        self.command("jog", pos)

    def stop(self):
        # self.command("stop")
        self._stop = True

    def estop(self):
        # self.command("estop")
        self._estop = True

    def home(self, block=False):
        self.command("home", block=block)

    def move_absolute(self, pos, block=False):
        self.command("move_absolute", pos, block=block)

    def move_relative(self, pos, block=False):
        self.command("move_relative", pos, block=block)

    def set_jog_rate(self, rate):
        self.command("set_jog_rate", rate)

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
                    'pos': self.motion.pos,
                    'set_jog_rate': self.motion.set_jog_rate,
                    'home': self.motion.home,
                    # 'stop': self.motion.stop,
                    # 'estop': self.motion.estop,
                    'unestop': self.motion.unestop,
                    'mdi': self.motion.command,
                }.get(command, default)
                try:
                    ret = f(*args)
                except Exception as e:
                    print("")
                    print("WARNING: motion thread crashed")
                    print(traceback.format_exc())
                    if command_done:
                        command_done(command, args, e)
                    continue

                if command_done:
                    command_done(command, args, ret)

        finally:
            self.motion.stop()

    def thread_stop(self):
        self.running.clear()


"""
Sends events to the imaging and movement threads

rconfig: misc parmeters including complex objects
plannerj: planner configuration JSON. Written to disk
"""


class PlannerThread(QThread):
    plannerDone = pyqtSignal()
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

    def stop(self):
        if self.planner:
            self.planner.stop()

    def run(self):
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
            self.planner.run()
            b.stop()
            self.log('Planner done!  Took : %s' % str(b))
        except Exception as e:
            self.log('WARNING: planner thread crashed: %s' % str(e))
            traceback.print_exc()
            #raise
        finally:
            self.plannerDone.emit()


class StitcherThread(QThread):
    stitcherDone = pyqtSignal()
    log_msg = pyqtSignal(str)

    def __init__(self,
                 directory,
                 access_key,
                 secret_key,
                 id_key,
                 notification_email,
                 parent=None):
        QThread.__init__(self, parent)
        self.directory = directory
        self.access_key = access_key
        self.secret_key = secret_key
        self.id_key = id_key
        self.notification_email = notification_email

    def log(self, msg):
        self.log_msg.emit(msg)

    def run(self):
        try:
            self.log("Sending cloud stitching job...")
            if not boto3:
                raise Exception("Requires boto3 library")
            S3BUCKET = 'labsmore-mosaic-service'
            DEST_DIR = self.id_key + '/' + os.path.basename(
                os.path.abspath(self.directory))
            s3 = boto3.client('s3',
                              aws_access_key_id=self.access_key,
                              aws_secret_access_key=self.secret_key)

            for root, _, files in os.walk(self.directory):
                for file in files:
                    self.log('Uploading {} to {}/{} '.format(
                        os.path.join(root, file), S3BUCKET,
                        DEST_DIR + '/' + file))
                    s3.upload_file(os.path.join(root, file), S3BUCKET,
                                   DEST_DIR + '/' + file)

            MOSAIC_RUN_CONTENT = u'{{ "email": "{}" }}'.format(
                self.notification_email)
            mosaic_run_json = io.BytesIO(
                bytes(MOSAIC_RUN_CONTENT, encoding='utf8'))
            s3.upload_fileobj(mosaic_run_json, S3BUCKET,
                              DEST_DIR + '/' + 'mosaic_run.json')
            self.log("Sent stitching job.")
        except Exception as e:
            self.log('WARNING: stitcher thread crashed: %s' % str(e))
            traceback.print_exc()
        finally:
            self.stitcherDone.emit()
