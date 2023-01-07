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


def pos_str(pos):
    return ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in pos.items()])


class MotionHAL:
    def __init__(self, scalars=None, soft_limits=None, log=None, verbose=None):
        # Per axis? Currently is global
        self.jog_rate = 0
        self.stop_on_del = True

        self.scalars = scalars
        # dict containing (min, min) for each axis
        self.soft_limits = soft_limits
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
        # self.progress = lambda pos: None
        self.status_cbs = []
        self.mv_lastt = time.time()

    def __del__(self):
        self.close()

    def unregister_status_cb(self, cb):
        index = self.status_cbs.find(cb)
        del self.status_cbs[index]

    def register_status_cb(self, cb):
        """
        Notify callback cb on long moves
        cb(d)
        where status = {
        "pos": {"x": 1.0, ...},
        }
        and can add other fields later
        """
        self.status_cbs.append(cb)

    def update_status(self, status):
        if "pos" in status:
            status["pos"] = self.scale_i2e(status["pos"])
        for cb in self.status_cbs:
            cb(status)

    def close(self):
        # Most users want system to idle if they lose control
        if self.stop_on_del:
            self.stop()

    def axes(self):
        '''Return supported axes'''
        raise Exception("Required")

    def home(self, axes):
        '''Set current position to 0.0'''
        raise Exception("Required for tuning")

    def ret0(self):
        '''Return to origin'''
        self.move_absolute(dict([(k, 0.0) for k in self.axes()]))

    def scale_e2i(self, pos):
        """
        Scale an external coordinate system to an internal coordinate system
        External: what user sees
        Internal: what the machine uses
        Fixup layer for gearboxes and such
        """
        if not self.scalars:
            return pos
        ret = {}
        for k, v in pos.items():
            ret[k] = v * self.scalars.get(k, 1.0)
        return ret

    def scale_i2e(self, pos):
        """
        Opposite of scale_e2i
        """
        if not self.scalars:
            return pos
        ret = {}
        for k, v in pos.items():
            ret[k] = v / self.scalars.get(k, 1.0)
        return ret

    def pos(self):
        '''Return current position for all axes'''
        return self.scale_i2e(self._pos())

    def _pos(self):
        '''Return current position for all axes'''
        raise NotSupported("Required for planner")

    def move_absolute(self, pos):
        '''Absolute move to positions specified by pos dict'''
        if len(pos) == 0:
            return
        self.validate_axes(pos.keys())
        if self.soft_limits:
            for axis, axpos in pos.items():
                limit = self.soft_limits.get(axis)
                if not limit:
                    continue
                axmin, axmax = limit
                if axpos < axmin or axpos > axmax:
                    raise AxisExceeded(
                        "axis %s: absolute violates %0.3f <= new pos %0.3f <= %0.3f"
                        % (axis, axmin, axpos, axmax))
        self.verbose and print("motion: move_absolute(%s)" % (pos_str(pos)))
        return self._move_absolute(self.scale_e2i(pos))

    def _move_absolute(self, pos):
        '''Absolute move to positions specified by pos dict'''
        raise NotSupported("Required for planner")

    def move_relative(self, pos):
        '''Absolute move to positions specified by pos dict'''
        if len(pos) == 0:
            return
        self.validate_axes(pos.keys())
        if self.soft_limits:
            cur_pos = self.pos()
            for axis, axdelta in pos.items():
                limit = self.soft_limits.get(axis)
                if not limit:
                    continue
                axmin, axmax = limit
                axpos = cur_pos[axis] + axdelta
                # New position under min and making worse?
                # New position above max and making worse?
                if axpos < axmin and axpos < 0 or axpos > axmax and axpos > 0:
                    raise AxisExceeded(
                        "axis %s: delta %+0.3f violates %0.3f <= new pos %0.3f <= %0.3f"
                        % (axis, axdelta, axmin, axpos, axmax))
        self.verbose and print("motion: move_relative(%s)" % (pos_str(pos)))
        return self._move_relative(self.scale_e2i(pos))

    def _move_relative(self, delta):
        '''Relative move to positions specified by delta dict'''
        raise NotSupported("Required for planner")

    def validate_axes(self, axes):
        for axis in axes:
            if axis not in self.axes():
                raise ValueError("Got axis %s but expect axis in %s" %
                                 (axis, self.axes()))

    def jog(self, scalars):
        """
        scalars: generally either +1 or -1 per axis to jog
        Final value is globally multiplied by the jog_rate and individually by the axis scalar
        """
        # Try to estimate if jog would go over limit
        # Always allow moving away from the bad area though if we are already in there
        if len(scalars) == 0:
            return
        self.validate_axes(scalars.keys())
        if self.soft_limits:
            cur_pos = self.pos()
            for axis, scalar in scalars.items():
                limit = self.soft_limits.get(axis)
                if not limit:
                    continue
                axmin, axmax = limit
                axpos = cur_pos[axis] + scalar
                # New position under min and making worse?
                # New position above max and making worse?
                if axpos < axmin and scalar < 0 or axpos > axmax and scalar > 0:
                    raise AxisExceeded(
                        "axis %s: jog %+0.3f violates %0.3f <= new pos %0.3f <= %0.3f"
                        % (axis, scalar, axmin, axpos, axmax))

        self._jog(self.scale_e2i(scalars))

    def _jog(self, axes):
        '''
        axes: dict of axis with value to move
        WARNING: under development / unstable API
        '''
        raise NotSupported("Required for jogging")

    def set_jog_rate(self, rate):
        self.jog_rate = rate

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
    def __init__(self, axes='xy', **kwargs):
        MotionHAL.__init__(self, **kwargs)

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

    def _move_absolute(self, pos):
        for axis, apos in pos.items():
            self._pos[axis] = apos
        0 and self._log('absolute move to ' + pos_str(pos))

    def _move_relative(self, delta):
        for axis, adelta in delta.items():
            self._pos[axis] += adelta
        0 and self._log('relative move to ' + pos_str(delta))

    def _pos(self):
        return self._pos

    def settle(self):
        # No hardware to let settle
        pass

    def ar_stop(self):
        pass


"""
Based on a real HAL but does no movement
Ex: inherits movement
"""


class DryHal(MotionHAL):
    def __init__(self, hal, log=None):
        super().__init__(log)

        self.hal = hal
        self.scalars = hal.scalars

        self._posd = {}
        # Assume starting at 0.0 until causes problems
        for axis in self.axes():
            self._posd[axis] = 0.0

    def _log(self, msg):
        self.log('Dry: ' + msg)

    def axes(self):
        return self.hal.axes()

    def home(self, axes):
        for axis in axes:
            self._posd[axis] = 0.0

    def take_picture(self, file_name):
        self._log('taking picture to %s' % file_name)

    def _move_absolute(self, pos):
        for axis, apos in pos.items():
            self._posd[axis] = apos
        0 and self._log(
            'absolute move to ' +
            ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in pos.items()]))

    def _move_relative(self, delta):
        for axis, adelta in delta.items():
            self._posd[axis] += adelta
        0 and self._log(
            'relative move to ' +
            ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in delta.items()]))

    def _pos(self):
        return self._posd

    def settle(self):
        # No hardware to let settle
        pass

    def ar_stop(self):
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

    def _move_absolute(self, pos):
        for axis, apos in pos.items():
            self._pos[axis] = apos
        self._line(
            'G90 G0' +
            ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in pos.items()]))

    def _move_relative(self, pos):
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
