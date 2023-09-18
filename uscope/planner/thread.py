"""
Sends events to the imaging and movement threads

rconfig: misc parmeters including complex objects
plannerj: planner configuration JSON. Written to disk
"""

from uscope.planner.planner_util import get_planner
import threading


class PlannerThreadBase:
    def __init__(self, planner_args, progress_cb):
        self.planner_args = planner_args
        self.planner = None
        self.progress_cb = progress_cb

    def log(self, msg=""):
        print(msg)

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
        self.planner = get_planner(log=self.log, **self.planner_args)
        self.planner.register_progress_callback(self.progress_cb)
        self.planner.run()


class SimplePlannerThread(PlannerThreadBase, threading.Thread):
    pass
