import time
from uscope.imager.imager import Imager
import os


class AxisExceeded(ValueError):
    pass


def format_t(dt):
    s = dt % 60
    m = int(dt / 60 % 60)
    hr = int(dt / 60 / 60)
    return '%02d:%02d:%02d' % (hr, m, s)


class NotSupported(Exception):
    pass


'''
Planner hardware abstraction layer (HAL)
At this time there is no need for unit conversions
Operate in whatever the native system is

MotionHAL is not thread safe with exception of the following:
-stop
-estop
(since it needs to be able to interrupt an active operation)
'''


class MotionHAL:

    def __init__(self, log, verbose=None):
        if log is None:

            def log(msg='', lvl=2):
                print(msg)

        self.verbose = verbose if verbose is not None else bool(
            int(os.getenv("MOTION_VERBOSE", "0")))

        self.log = log
        # seconds to wait before snapping picture
        self.t_settle = 4.0
        self.rt_sleep = 0.0

        # Overwrite to get updates while moving
        # (if supported)
        self.progress = lambda pos: None

        self.mv_lastt = time.time()
        # Per axis? Currently is global
        self.jog_rate = 0

    def axes(self):
        '''Return supported axes'''
        raise Exception("Required")

    def home(self, axes):
        '''Set current position to 0.0'''
        raise Exception("Required for tuning")

    def ret0(self):
        '''Return to origin'''
        self.move_absolute(dict([(k, 0.0) for k in self.axes()]))

    def move_absolute(self, pos):
        '''Absolute move to positions specified by pos dict'''
        raise NotSupported("Required for planner")

    def move_relative(self, delta):
        '''Relative move to positions specified by delta dict'''
        raise NotSupported("Required for planner")

    '''
    In modern systems the first is almost always used
    The second is supported for now while porting legacy code
    '''
    """
    def img_get(self):
        '''Take a picture and return a PIL image'''
        raise Exception("Required")

    def img_take(self):
        '''Take a picture and save it to internal.  File name is generated automatically'''
        raise Exception("Unsupported")
    """

    def pos(self):
        '''Return current position for all axes'''
        raise NotSupported("Required for planner")

    def on(self):
        '''Call at start of MDI phase, before planner starts'''
        pass

    def off(self):
        '''Call at program exit / user request to completely shut down machine.  Motors can lose position'''
        pass

    def begin(self):
        '''Call at start of active planer use (not dry)'''
        pass

    def actual_end(self):
        '''Called after machine is no longer in planer use.  Motors must maintain position for MDI'''
        pass

    def stop(self):
        '''Stop motion as soon as convenient.  Motors must maintain position'''
        pass

    def estop(self):
        '''Stop motion ASAP.  Motors are not required to maintain position'''
        pass

    def unestop(self):
        '''Allow system to move again after estop'''
        pass

    def meta(self):
        '''Supplementary info to add to run log'''
        return {}

    def jog(self, axes):
        '''
        axes: dict of axis with value to move
        WARNING: under development / unstable API
        '''
        raise NotSupported("Required for jogging")

    def cancel_jog(self):
        raise NotSupported("Required for jogging")

    def set_jog_rate(self, rate):
        self.jog_rate = rate

    def settle(self):
        '''Check last move time and wait if its not safe to take picture'''
        sleept = self.t_settle + self.mv_lastt - time.time()
        if sleept > 0.0:
            self.sleep(sleept, 'settle')

    def limit(self, axes=None):
        # FIXME: why were dummy values put here?
        # is this even currently used?
        raise NotSupported("")
        if axes is None:
            axes = self.axes()
        return dict([(axis, (-1000, 1000)) for axis in axes])

    def command(self, s):
        """MDI

        Machine dependent definition, but generally a single line of g-code
        Some machines only support binary => may be not supported
        """
        raise NotSupported("")


'''
Has no actual hardware associated with it
'''


class MockHal(MotionHAL):

    def __init__(self, axes='xy', log=None):
        MotionHAL.__init__(self, log)

        self._axes = list(axes)
        self._pos = {}
        # Assume starting at 0.0 until causes problems
        for axis in self._axes:
            self._pos[axis] = 0.0

    def _log(self, msg):
        self.log('Mock: ' + msg)

    def axes(self):
        return self._axes

    def home(self, axes):
        for axis in axes:
            self._pos[axis] = 0.0

    def take_picture(self, file_name):
        self._log('taking picture to %s' % file_name)

    def move_absolute(self, pos):
        for axis, apos in pos.items():
            self._pos[axis] = apos
        self._log(
            'absolute move to ' +
            ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in pos.items()]))

    def move_relative(self, delta):
        for axis, adelta in delta.items():
            self._pos[axis] += adelta
        self._log(
            'relative move to ' +
            ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in delta.items()]))

    def pos(self):
        return self._pos

    def settle(self):
        # No hardware to let settle
        pass

    def ar_stop(self):
        pass

    def cancel_jog(self):
        pass


"""
Based on a real HAL but does no movement
Ex: inherits movement
"""


class DryHal(MotionHAL):

    def __init__(self, hal, log=None):
        super().__init__(log)

        self.hal = hal

        self._pos = {}
        # Assume starting at 0.0 until causes problems
        for axis in self.axes():
            self._pos[axis] = 0.0

    def _log(self, msg):
        self.log('Dry: ' + msg)

    def axes(self):
        return self.hal.axes()

    def home(self, axes):
        for axis in axes:
            self._pos[axis] = 0.0

    def take_picture(self, file_name):
        self._log('taking picture to %s' % file_name)

    def move_absolute(self, pos):
        for axis, apos in pos.items():
            self._pos[axis] = apos
        self._log(
            'absolute move to ' +
            ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in pos.items()]))

    def move_relative(self, delta):
        for axis, adelta in delta.items():
            self._pos[axis] += adelta
        self._log(
            'relative move to ' +
            ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in delta.items()]))

    def pos(self):
        return self._pos

    def settle(self):
        # No hardware to let settle
        pass

    def ar_stop(self):
        pass

    def cancel_jog(self):
        pass


class GCodeHalImager(Imager):

    def __init__(self, hal):
        self.hal = hal

    def take(self):
        # Focus (coolant mist)
        self.hal._line('M7')
        self.hal._dwell(2)

        # Snap picture (coolant flood)
        self.hal._line('M8')
        self.hal._dwell(3)

        # Release shutter (coolant off)
        self.hal._line('M9')


'''
http://linuxcnc.org/docs/html/gcode/gcode.html

Static gcode generator using coolant hack
Not to be confused LCncHal which uses MDI g-code in real time

M7 (coolant on): tied to focus / half press pin
M8 (coolant flood): tied to snap picture
    M7 must be depressed first
M9 (coolant off): release focus / picture
'''


class GCodeHal(MotionHAL):

    def __init__(self, axes='xy', log=None):
        MotionHAL.__init__(self, log)
        self._axes = list(axes)

        self._pos = {}
        # Assume starting at 0.0 until causes problems
        for axis in self._axes:
            self._pos[axis] = 0.0
        self._buff = bytearray()

    def imager(self):
        return GCodeHalImager(self)

    def move_absolute(self, pos):
        for axis, apos in pos.items():
            self._pos[axis] = apos
        self._line(
            'G90 G0' +
            ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in pos.items()]))

    def move_relative(self, pos):
        for axis, delta in pos.items():
            self._pos[axis] += delta
        self._line(
            'G91 G0' +
            ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in pos.items()]))

    def comment(self, s=''):
        if len(s) == 0:
            self._line()
        else:
            self._line('(%s)' % s)

    def _line(self, s=''):
        #self.log(s)
        self._buff += s + '\n'

    def begin(self):
        pass

    def actual_end(self):
        self._line()
        self._line('(Done!)')
        self._line('M2')

    def _dwell(self, seconds):
        self._line('G4 P%0.3f' % (seconds, ))

    def get(self):
        return str(self._buff)
