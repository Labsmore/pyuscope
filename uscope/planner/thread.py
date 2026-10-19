"""
Sends events to the imaging and movement threads

rconfig: misc parmeters including complex objects
plannerj: planner configuration JSON. Written to disk
"""

from uscope.planner.planner_util import get_planner
from uscope.threads import SimpleThread


class PlannerThreadBase:
    def __init__(self, planner_args, progress_cb):
        self.planner_args = planner_args
        self.planner = None
        self.progress_cb = progress_cb

    def log(self, msg=""):
        print(msg)

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

    def shutdown_request(self, phase):
        if self.planner:
            self.planner.shutdown_request(phase)

    def shutdown_join(self, timeout=3.0):
        if self.planner:
            self.planner.shutdown_join()
        # Thread implementation (QThread / threading.Thread mixin)
        super().shutdown_join(timeout=timeout)

    def run(self):
        self.planner = get_planner(log=self.log, **self.planner_args)
        self.planner.register_progress_callback(self.progress_cb)
        self.planner.run()


class SimplePlannerThread(PlannerThreadBase, SimpleThread):
    def __init__(self, planner_args, progress_cb):
        SimpleThread.__init__(self)
        PlannerThreadBase.__init__(self,
                                   planner_args=planner_args,
                                   progress_cb=progress_cb)
