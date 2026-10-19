from uscope.microscope import MicroscopeStop
import threading
import queue
import traceback
import random
import time
import enum


class ShutdownPhase(enum.Enum):
    INITIAL = enum.auto()
    FINAL = enum.auto()


class CommandThreadDead(Exception):
    """
    The worker thread is no longer processing commands
    Ex: the hardware it controls had an unrecoverable error
    """
    pass


class CommandThreadBase:
    def __init__(self, microscope):
        assert microscope
        self.microscope = microscope
        self.verbose = False
        self.queue = queue.Queue()
        self.running = threading.Event()
        self.idle = threading.Event()
        self.idle.set()
        self.command_map = {}
        # Set once the thread can no longer process commands
        # Pending and future commands will fail with this exception
        self.dead = None

    def log(self, msg=""):
        print(msg)

    def shutdown_request(self, phase):
        if phase == ShutdownPhase.FINAL:
            self.running.clear()

    # shutdown_join(timeout) must be provided by the threading implementation
    # (ex: ArgusThread for QThread, SimpleThread for threading.Thread)
    # It is intentionally not defined here: this class is listed first in the
    # MRO and would shadow the implementation

    def shutdown(self, timeout=3.0):
        self.shutdown_request(ShutdownPhase.FINAL)
        self.shutdown_join(timeout=timeout)

    def command(self, command, *args, block=False, callback=None, done=None):
        """
        block: don't return until offloaded task completes?
            If the command raised an exception, it is re-raised here
        callback: callback(command, args, ret_or_exception)
        done: threading.Event()
        """
        command_done = None
        if block or callback or done:
            ready = threading.Event()
            ret = []

            def command_done(command, args, ret_e):
                ret.append(ret_e)
                ready.set()
                if callback:
                    callback(command, args, ret_e)
                if done:
                    done.set()

        if self.dead is not None:
            # Fail fast instead of queueing something that will never run
            if command_done:
                command_done(command, args, self.dead)
            if block:
                raise self.dead
            return None

        self.queue.put((command, args, command_done))
        # Close the race with the thread dying between the check above and
        # the put: whoever observes dead drains the queue
        if self.dead is not None:
            self.fail_pending(self.dead)
        if block:
            ready.wait()
            ret = ret[0]
            if isinstance(ret, BaseException):
                raise ret
            return ret

    def fail_pending(self, exception):
        """
        Mark the thread as dead and fail all queued commands
        Blocking callers will raise rather than hang forever
        """
        self.dead = exception
        while True:
            try:
                (command, args, command_done) = self.queue.get(block=False)
            except queue.Empty:
                break
            if command_done:
                command_done(command, args, exception)
        self.idle.set()

    def check_stress(self):
        if self.microscope.bc.stress_test():
            time.sleep(random.randint(0, 100) * 0.001)

    def loop_poll(self):
        """
        Called every loop before checking queue
        """
        pass

    def run(self):
        self.verbose and print("Task thread started: %s" %
                               (threading.get_ident(), ))
        self.running.set()

        while self.running.is_set():
            self.check_stress()
            self.loop_poll()
            try:
                (command, args, command_done) = self.queue.get(True, 0.1)
            except queue.Empty:
                self.idle.set()
                continue

            self.idle.clear()

            def default(*args):
                raise Exception("Bad command %s" % (command, ))

            f = self.command_map.get(command, default)
            try:
                ret = f(*args)
            # Graceful abort
            except MicroscopeStop as e:
                if command_done:
                    command_done(command, args, e)
                continue
            # :( abort
            except Exception as e:
                self.log(f"WARNING: {self.__class__} thread crashed: {e}")
                print("")
                print(f"WARNING: {self.__class__} thread crashed")
                print(traceback.format_exc())
                if command_done:
                    command_done(command, args, e)
                continue
            if command_done:
                command_done(command, args, ret)


class SimpleThread(threading.Thread):
    """
    Plain threading.Thread flavor of the Argus QThread wrappers
    Mix in after a *ThreadBase class

    Note: __init__ must be called explicitly by the subclass before the
    base class __init__ as the *ThreadBase classes don't cooperatively init
    """
    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True

    def shutdown_join(self, timeout=3.0):
        self.join(timeout=timeout)


# Modeled after queue interface
# Force kwargs callback to simplify misc things
class CBSync:
    def __init__(self, block=False, callback=None, done=None):
        """
        block: don't return until offloaded task completes?
        callback: simple callback taking no args
        done: threading.Event()
        """
        self.block = block
        self._callback = callback
        self.done_args = None
        self.done_kwargs = None
        if done is None:
            done = threading.Event()
        self.done = done

    def callback(self, *args, **kwargs):
        self.done_args = args
        self.done_kwargs = kwargs
        # better to set condition before callback?
        self.done.set()
        if self._callback:
            self._callback(*args, **kwargs)

    def wait_done(self):
        self.done.wait()

    def check_return_kw1(self):
        """
        Call when launching the job
        Some context may choose to immediately block
        """
        if self.block:
            self.done.wait()
            # Return the single kwarg value
            assert len(self.done_args) == 0, "Expect kwargs only"
            assert len(self.done_kwargs) == 1, "Expect single kwarg"
            return list(self.done_kwargs.values())[0]
        else:
            return None
