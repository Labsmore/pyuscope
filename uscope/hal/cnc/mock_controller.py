from .controller import Controller
from .axis import Axis


class MockController(Controller):
    def __init__(self, debug=False, log=None):
        Controller.__init__(self, debug=debug, log=log)
        self.x = DummyAxis('X', log=log)
        self.y = DummyAxis('Y', log=log)
        self.axes = [self.x, self.y]


class DummyAxis(Axis):
    def __init__(self, name='dummy', log=None):
        Axis.__init__(self, name, log=log)
        self.net = 0

    def jog(self, units):
        self.log('Dummy axis %s: jogging %s' % (self.name, units))

    def step(self, steps):
        self.log('Dummy axis %s: stepping %s' % (self.name, steps))

    def set_pos(self, units):
        self.log('Dummy axis %s: set_pos %s' % (self.name, units))

    def stop(self):
        self.log('Dummy axis %s: stop' % (self.name, ))

    def estop(self):
        self.log('Dummy axis %s: emergency stop' % (self.name, ))

    def unestop(self):
        self.log('Dummy axis %s: clearing emergency stop' % (self.name, ))

    def set_home(self):
        self.log('Dummy axis %s: set home' % (self.name, ))

    def home(self):
        self.log('Dummy axis %s: home' % (self.name, ))

    def forever_neg(self, done, progress_notify):
        self.log('Dummy axis %s: forever_neg' % (self.name, ))
        while not done.is_set():
            done.wait(0.05)
            progress_notify() if progress_notify() else None

    def forever_pos(self, done, progress_notify=None):
        self.log('Dummy axis %s: forever_pos' % (self.name, ))
        while not done.is_set():
            done.wait(0.05)
            progress_notify() if progress_notify() else None
