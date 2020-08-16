'''
pr0ndexer controller
Higher level wrapper around the primitive indexer interface to adapt to pr0nscope
Introduces things like units and concurrency
'''

import threading
from .controller import Controller
from .axis import Axis
from uscope.pr0ndexer import Indexer


class PDC(Controller):
    def __init__(self, debug=False, log=None, config=None):
        Controller.__init__(self, debug=False)

        self.indexer = Indexer(debug=debug, log=log)
        if self.indexer.serial is None:
            raise Exception("USBIO missing serial")

        c = config['cnc']['pr0ndexer']
        self.indexer.configure(acl=int(c.get('acl', '325')),
                               velmin=int(c.get('velmin', '10')),
                               velmax=int(c.get('velmax', '9250')),
                               hstep_c=int(c.get('hstep_c', '740000')))

        self.x = PDCAxis('X', self.indexer, config=config)
        self.y = PDCAxis('Y', self.indexer, config=config)
        self.z = PDCAxis('Z', self.indexer, config=config)

        self.axes = [self.x, self.y, self.z]
        # enforce some initial state?

        self.um()

    def __del__(self):
        self.off()


class PDCAxis(Axis):
    def __init__(self, name, indexer, config=None):
        Axis.__init__(self, name, steps_per_um=config['cnc']['steps_per_um'])
        self.indexer = indexer
        if self.indexer.serial is None:
            raise Exception("Indexer missing serial")
        self.movement_notify = lambda: None

        # Ensure stopped
        self.indexer.step(self.name, 0)

        self._stop = threading.Event()
        self._estop = threading.Event()

    def forever_pos(self, done, progress_notify=None):
        '''Go forever in the positive direction until stopped'''
        self.forever_dir(done, progress_notify, 1)

    def forever_neg(self, done, progress_notify):
        '''Go forever in the negative direction until stopped'''
        self.forever_dir(done, progress_notify, -1)

    def forever_dir(self, done, progress_notify, sign):
        # Because we are free-wheeling we need to know how many were completed to maintain position
        steps_orig = self.indexer.net_tostep(self.name)
        net_orig = self.net

        i = 0
        while not done.is_set():
            if self._estop.is_set():
                self.indexer.step(self.name, 0)
                return
            # Step for half second at a time
            # last value overwrites though
            self.indexer.step(self.name,
                              sign * self.indexer.steps_a_second(),
                              wait=False)
            done.wait(0.05)
            # Update position occasionally as we go
            if i % 5 == 0:
                self.net = net_orig + self.indexer.net_tostep(
                    self.name) - steps_orig
                self.movement_notify()
            i += 1
        self._stop.clear()
        # make a clean stop
        self.indexer.step(self.name, sign * 30, wait=True)
        # Fib a little by reporting where we will end up, should be good enough
        # as we will end there shortly
        # if we change plan before then its in charge of updating position
        # XXX: wraparound issue?  should not be issue in practice since stage would crash first
        self.net = net_orig + self.indexer.net_tostep(self.name) - steps_orig
        if progress_notify:
            progress_notify()
        self.movement_notify()

    def stop(self):
        self.indexer.step(self.name, 0)

    def estop(self):
        self._estop.set()

    def unestop(self):
        self._estop.clear()

    def unstop(self):
        self._stop.clear()

    def step(self, steps):
        self.indexer.step(self.name, steps)
        self.net += steps
        self.movement_notify()

    # pretend we are at 0
    def set_home(self):
        self.net = 0

    def set_pos(self, units):
        '''
        Ex:
        old position is 2 we want 10
        we need to move 10 - 2 = 8
        '''
        self.jog(units - self.get_um())

    def home(self):
        self.step(-self.net)
