import time
from uscope.imager.imager import Imager
import os
from collections import OrderedDict


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


def sign(delta):
    if delta > 0:
        return +1
    else:
        return -1


class MotionModifier:
    def __init__(self, motion):
        self.motion = motion
        self.log = self.motion.log

    def pos(self, pos):
        pass

    def move_absolute_pre(self, pos, options={}):
        pass

    def move_absolute_post(self, ok, options={}):
        pass

    def move_relative_pre(self, pos, options={}):
        pass

    def move_relative_post(self, ok, options={}):
        pass

    def jog(self, scalars, options={}):
        pass

    def update_status(self, status):
        """
        General metadata broadcast
        """
        pass


"""
Do active backlash compensation on absolute and relative moves
"""


# TODO: consider simplifying options
# ex: eliminated enabled if not actually being used
class BacklashMM(MotionModifier):
    def __init__(self, motion, backlash, compensation):
        super().__init__(motion)

        # Per axis
        self.backlash = backlash
        """
        Dictionary of axis to bool
        Default: false
        """
        self.enabled = {}
        """
        Dictionary of bool to direction
        -1 => +axis then move backward to position
        +1 => -axis then move forward to position
        """
        self.compensation = compensation
        """
        Set when the axis is already compensated
        Can be used to avoid extra backlash compensation when going the same direction
        """
        self.compensated = {}
        for axis in self.motion.axes():
            self.enabled[axis] = True
            # Make default since it works well with Z and XY issomewhat arbitrary
            self.compensation.setdefault(axis, -1)
            self.compensated[axis] = False
        self.recursing = False

    def set_enabled(self, axes):
        self.enabled = axes
        self.compensated = {}

    def set_all_enabled(self, val=True):
        for axis in self.motion.axes():
            self.enabled[axis] = val

    def set_compendsation(self, compensation):
        self.compensation = compensation
        self.compensated = {}

    def move_absolute_pre(self, pos, options={}):
        if self.recursing:
            return
        """
        Simple model for now:
        -Assume move completes
        -Only track when big moves move us into a clear state
            ie moves need to be bigger than the backlash threshold to count
        """
        cur_pos = self.motion.cur_pos_cache()
        corrections = {}
        for axis, val in pos.items():
            if axis not in pos:
                continue
            if not self.enabled.get(axis, False):
                self.compensated[axis] = False
                continue
            if self.backlash[axis] == 0.0:
                continue
            # Should this state be disallowed?
            if self.compensation[axis] == 0:
                continue
            delta = val - cur_pos[axis]
            # No: if we have moved here w/o backlash correction may be wrong
            # if delta == 0.0:
            #     continue

            # Already compensated and still moving in the same direction?
            # No compensation necessary
            if self.compensated[axis] and (
                    delta == 0.0 or sign(delta) == self.compensation[axis]):
                continue
            # A correction is possibly needed then
            # Will the movement itself compensate?
            if self.compensation[axis] == +1 and delta >= self.backlash[
                    axis] or self.compensation[
                        axis] == -1 and delta <= -self.backlash[axis]:
                self.compensated[axis] = True
                continue

            if 1 and axis == "z":
                self.log("z comp trig ")
                self.log("  compensation: %s" % (self.compensation[axis], ))
                self.log("  backlash: %f" % (self.backlash[axis], ))
                self.log("  compensated: %s" % (self.compensated[axis], ))
                self.log("  delta: %f" % (delta, ))
                self.log("  val: %f" % (val, ))
                self.log("  cur_pos: %f" % (cur_pos[axis], ))

            # Need to manually compensate
            # ex: +compensation => need to do negative backlash move first
            corrections[axis] = -self.compensation[axis] * self.backlash[axis]

        # Did we calculate any backlash moves?
        if len(corrections):
            self.recursing = True
            self.motion.move_relative(corrections)
            self.recursing = False
            for axis in corrections.keys():
                self.compensated[axis] = True

    def move_relative_pre(self, pos, options={}):
        if self.recursing:
            return
        self.move_absolute_pre(
            self.motion.estimate_relative_pos(
                pos, cur_pos=self.motion.cur_pos_cache()))

    def jog(self, scalars, options={}):
        # Don't modify jog commands, but invalidate compensation
        for axis in scalars.keys():
            self.compensated[axis] = False


"""
Throw an exception if axis out of expected range
"""


class SoftLimitMM(MotionModifier):
    def __init__(self, motion, soft_limits):
        super().__init__(motion)
        self.soft_limits = soft_limits

    def move_absolute_pre(self, pos, options={}):
        for axis, axpos in pos.items():
            limit = self.soft_limits.get(axis)
            if not limit:
                continue
            axmin, axmax = limit
            if axpos < axmin or axpos > axmax:
                raise AxisExceeded(
                    "axis %s: move violates %0.3f <= new pos %0.3f <= %0.3f" %
                    (axis, axmin, axpos, axmax))

    def move_relative_pre(self, pos, options={}):
        self.move_absolute_pre(
            self.motion.estimate_relative_pos(
                pos, cur_pos=self.motion.cur_pos_cache()))

    def jog(self, scalars, options={}):
        self.move_absolute_pre(
            self.motion.estimate_relative_pos(
                scalars, cur_pos=self.motion.cur_pos_cache()))


"""
Scale axes such as for a gearbox or to reverse direction
"""


class ScalarMM(MotionModifier):
    def __init__(self, motion, scalars):
        super().__init__(motion)
        self.scalars = scalars

    def scale_e2i(self, pos):
        """
        Scale an external coordinate system to an internal coordinate system
        External: what user sees
        Internal: what the machine uses
        Fixup layer for gearboxes and such
        """
        for k, v in dict(pos).items():
            pos[k] = v * self.scalars.get(k, 1.0)

    def scale_i2e(self, pos):
        """
        Opposite of scale_e2i
        """
        for k, v in dict(pos).items():
            pos[k] = v / self.scalars.get(k, 1.0)

    def pos(self, pos):
        # print('pos scaling1 %s' % pos)
        self.scale_i2e(pos)
        # print('pos scaling2 %s' % pos)

    def move_absolute_pre(self, pos, options={}):
        self.scale_e2i(pos)

    def move_relative_pre(self, pos, options={}):
        self.scale_e2i(pos)

    def update_status(self, status):
        if "pos" in status:
            # print('status scaling1 %s' % status["pos"])
            self.scale_i2e(status["pos"])
            # print('status scaling2 %s' % status["pos"])


class MotionHAL:
    def __init__(self, options, log=None, verbose=None):
        # Per axis? Currently is global
        self.jog_rate = 0
        self.stop_on_del = True

        # dict containing (min, min) for each axis
        if log is None:

            def log(msg='', lvl=2):
                print(msg)

        self.verbose = verbose if verbose is not None else bool(
            int(os.getenv("MOTION_VERBOSE", "0")))

        self.log = log

        # Overwrite to get updates while moving
        # (if supported)
        # self.progress = lambda pos: None
        self.status_cbs = []
        self.mv_lastt = time.time()

        # MotionModifier's
        self.modifiers = OrderedDict()
        """
        Order is important
        Do soft limits after backlash in case backlash compensation would cause a crash
        Scalar is applied last since its a low level detail
        Inputs will be applied in forward order, outputs in reverse order
        """
        self.options = options
        backlash = self.options.get("backlash")
        if backlash:
            backlash_compensation = self.options.get("backlash_compensation")
            self.modifiers["backlash"] = BacklashMM(
                self, backlash=backlash, compensation=backlash_compensation)
        soft_limits = self.options.get("soft_limits")
        if soft_limits:
            self.modifiers["soft-limit"] = SoftLimitMM(self,
                                                       soft_limits=soft_limits)
        scalars = self.options.get("scalars")
        if scalars:
            self.modifiers["scalar"] = ScalarMM(self, scalars=scalars)

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

    def cur_pos_cache(self):
        """
        Intended to be used during modifiers operations to keep pos() queries low
        (since it can be expensive)
        """
        if self._cur_pos_cache is None:
            self._cur_pos_cache = self.pos()
        return self._cur_pos_cache

    def cur_pos_cache_invalidate(self):
        self._cur_pos_cache = None

    def update_status(self, status):
        # print("update_status begin: %s" % (status,))
        for modifier in self.modifiers.values():
            modifier.update_status(status)
        for cb in self.status_cbs:
            cb(status)
        # print("update_status end: %s" % (status,))

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

    def process_pos(self, pos):
        # print("pos init %s" % (pos,))
        for modifier in self.modifiers.values():
            modifier.pos(pos)
        # print("pos final %s" % (pos,))

    def pos(self):
        '''Return current position for all axes'''
        # print("")
        pos = self._pos()
        self.process_pos(pos)
        return pos

    def _pos(self):
        '''Return current position for all axes'''
        raise NotSupported("Required for planner")

    def move_absolute(self, pos, options={}):
        '''Absolute move to positions specified by pos dict'''
        if len(pos) == 0:
            return
        self.validate_axes(pos.keys())
        self.verbose and print("motion: move_absolute(%s)" % (pos_str(pos)))
        try:
            self.cur_pos_cache_invalidate()
            for modifier in self.modifiers.values():
                modifier.move_absolute_pre(pos, options=options)
            _ret = self._move_absolute(pos)
            for modifier in self.modifiers.values():
                modifier.move_absolute_post(True, options=options)
            self.mv_lastt = time.time()
        finally:
            self.cur_pos_cache_invalidate()
        # return ret

    def _move_absolute(self, pos):
        '''Absolute move to positions specified by pos dict'''
        raise NotSupported("Required for planner")

    def update_backlash(self, cur_pos, abs_pos):
        pass

    def estimate_relative_pos(self, pos, cur_pos=None):
        abs_pos = {}
        if cur_pos is None:
            cur_pos = self.pos()
        for axis, axdelta in pos.items():
            abs_pos[axis] = cur_pos[axis] + axdelta
        return abs_pos

    def move_relative(self, pos, options={}):
        '''Absolute move to positions specified by pos dict'''
        if len(pos) == 0:
            return
        self.validate_axes(pos.keys())

        self.verbose and print("motion: move_relative(%s)" % (pos_str(pos)))
        self.cur_pos_cache_invalidate()
        try:
            for modifier in self.modifiers.values():
                modifier.move_relative_pre(pos, options=options)
            _ret = self._move_relative(pos)
            for modifier in self.modifiers.values():
                modifier.move_relative_post(True, options=options)
            self.mv_lastt = time.time()
        finally:
            self.cur_pos_cache_invalidate()
        # return ret

    def _move_relative(self, delta):
        '''Relative move to positions specified by delta dict'''
        raise NotSupported("Required for planner")

    def validate_axes(self, axes):
        for axis in axes:
            if axis not in self.axes():
                raise ValueError("Got axis %s but expect axis in %s" %
                                 (axis, self.axes()))

    def jog(self, scalars, options={}):
        """
        scalars: generally either +1 or -1 per axis to jog
        Final value is globally multiplied by the jog_rate and individually by the axis scalar
        """
        # Try to estimate if jog would go over limit
        # Always allow moving away from the bad area though if we are already in there
        if len(scalars) == 0:
            return
        self.cur_pos_cache_invalidate()
        try:
            self.validate_axes(scalars.keys())
            for modifier in self.modifiers.values():
                modifier.jog(scalars, options=options)
            self._jog(scalars)
        finally:
            self.cur_pos_cache_invalidate()

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

    def rc_commands(self, commands):
        for command in commands:
            self.command(command)


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
        self.hal = hal

        self._posd = {}
        # Assume starting at 0.0 until causes problems
        for axis in self.axes():
            self._posd[axis] = 0.0

        super().__init__(
            # Don't re-apply pipeline
            options={},
            log=log,
            verbose=hal.verbose)

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
