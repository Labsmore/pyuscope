from uscope.microscope import MicroscopeStop
import threading
import queue
import traceback


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

    def log(self, msg=""):
        print(msg)

    def shutdown(self):
        self.running.clear()

    def command(self, command, *args, block=False, callback=None, done=None):
        """
        block: don't return until offloaded task completes?
        callback: simple callback taking no args
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

        self.queue.put((command, args, command_done))
        if block:
            ready.wait()
            ret = ret[0]
            if type(ret) is Exception:
                raise Exception("oopsie: %s" % (ret, ))
            return ret

    def run(self):
        self.verbose and print("Task thread started: %s" %
                               (threading.get_ident(), ))
        self.running.set()

        while self.running.is_set():
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
