import time
from uscope.imager.imager import Imager
import os
from collections import OrderedDict
from uscope.util import time_str


class AxisExceeded(ValueError):
    pass


def format_t(dt):
    s = dt % 60
    m = int(dt / 60 % 60)
    hr = int(dt / 60 / 60)
    return '%02d:%02d:%02d' % (hr, m, s)


class NotSupported(Exception):
    pass


class HomingAborted(Exception):
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
    return ' '.join(
        ['%c%0.3f' % (k.upper(), v) for k, v in sorted(pos.items())])


def sign(delta):
    if delta > 0:
        return +1
    else:
        return -1


# An un-recoverable error
# Ex: socket closed, serial port removed
# Restart motion controller to recover
class MotionCritical(Exception):
    pass


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

    def jog_rel(self, pos, rate_ref, options={}):
        pass

    def jog_abs(self, pos, rate_ref, options={}):
        pass

    def update_status(self, status):
        """
        General metadata broadcast
        """
        pass

    """
    absolute versions are too hard to track
    some coordinates have WCS rel some don't
    use specialized conversions for those
    """

    def munge_axes_user2machine_rel(self, axes):
        pass

    def munge_axes_user2machine_abs(self, axes):
        pass

    def munge_axes_machine2user_rel(self, axes):
        pass

    def munge_axes_machine2user_abs(self, axes):
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
        self.pending_compensation = None

    def set_enabled(self, axes):
        self.enabled = axes
        self.compensated = {}

    def set_all_enabled(self, val=True):
        for axis in self.motion.axes():
            self.enabled[axis] = val

    def set_compendsation(self, compensation):
        self.compensation = compensation
        self.compensated = {}

    def should_compensate(self, move_to, cur_pos=None):
        if cur_pos is None:
            cur_pos = self.motion.cur_pos_cache()
        ret = {}
        for axis, val in move_to.items():
            ret[axis] = {
                # Will the movement itself compensate?
                "auto": False,
                # Is compensation needed to make reliable?
                "needed": False,
            }
            # Skip check?
            if not self.enabled.get(axis, False):
                # maybe this should be in post
                self.compensated[axis] = False
                continue
            if self.backlash[axis] == 0.0:
                continue
            # Should this state be disallowed?
            if self.compensation[axis] == 0:
                continue
            delta = val - cur_pos[axis]

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
                # self.compensated[axis] = True
                ret[axis]["auto"] = True
                continue

            # Rounding error that a movement won't improve?
            if self.compensated[axis] and self.motion.equivalent_axis_pos(
                    axis=axis, value1=val, value2=cur_pos[axis]):
                continue

            if 0 and axis == "z":
                self.log("z comp trig ")
                self.log("  compensation: %s" % (self.compensation[axis], ))
                self.log("  backlash: %f" % (self.backlash[axis], ))
                self.log("  compensated: %s" % (self.compensated[axis], ))
                self.log("  delta: %f" % (delta, ))
                self.log("  val: %f" % (val, ))
                self.log("  cur_pos: %f" % (cur_pos[axis], ))
            ret[axis]["needed"] = True
            ret[axis]["delta"] = delta
        return ret

    def move_x_pre(self, dst_abs_pos, options={}):
        if self.recursing:
            return
        """
        Simple model for now:
        -Assume move completes
        -Only track when big moves move us into a clear state
            ie moves need to be bigger than the backlash threshold to count
        """
        corrections_abs = {}
        all_abs = {}
        self.pending_compensation = {}
        comp_res = self.should_compensate(move_to=dst_abs_pos).items()
        for axis, axis_compensation in comp_res:
            backlash_pos = dst_abs_pos[
                axis] - self.compensation[axis] * self.backlash[axis]

            if axis_compensation["auto"]:
                self.pending_compensation[axis] = True

            if axis_compensation["needed"]:
                # Need to manually compensate
                # ex: +compensation => need to do negative backlash move first
                # FIXME: this might be excessive in some cases
                # really should be relatively move of min(delta, full step)
                corrections_abs[axis] = backlash_pos
                # corrections_rel[axis] = -self.compensation[axis] * self.backlash[axis]
                all_abs[axis] = backlash_pos
            else:
                all_abs[axis] = dst_abs_pos[axis]

        if 0:
            print("DEBUG")
            print("  cur ", self.motion.cur_pos_cache())
            print("  dst ", dst_abs_pos)
            print("  res ", comp_res)
            print("  cor ", corrections_abs)
            print("  com ", self.compensation)
            print("  is  ", self.compensated)

        # Did we calculate any backlash moves?
        if len(corrections_abs):
            self.recursing = True
            # https://github.com/Labsmore/pyuscope/issues/181
            # without this movements can take a long time when mixing
            # compensated and non-compensated movements
            # self.motion.move_absolute(corrections_abs)
            self.motion.move_absolute(all_abs)
            self.recursing = False
            for axis in corrections_abs.keys():
                self.compensated[axis] = True

    def move_absolute_pre(self, pos, options={}):
        if self.recursing:
            return
        self.move_x_pre(dst_abs_pos=pos, options=options)

    def move_relative_pre(self, pos, options={}):
        assert 0, "FIXME: unsupported"
        # backlash will overshoot
        if self.recursing:
            return
        final_abs_pos = self.motion.estimate_relative_pos(
            pos, cur_pos=self.motion.cur_pos_cache())
        self.move_x_pre(dst_abs_pos=final_abs_pos, options=options)

    def jog_rel(self, pos, rate_ref, options={}):
        # Don't modify jog commands, but invalidate compensation
        for axis in pos.keys():
            self.compensated[axis] = False

    def jog_abs(self, pos, rate_ref, options={}):
        # Don't modify jog commands, but invalidate compensation
        for axis in pos.keys():
            self.compensated[axis] = False

    def move_absolute_post(self, ok, options={}):
        if self.recursing:
            return
        for axis in self.pending_compensation.keys():
            self.compensated[axis] = True
        self.pending_compensation = None

    def move_relative_post(self, ok, options={}):
        assert 0, "FIXME: unsupported"
        if self.recursing:
            return
        for axis in self.pending_compensation.keys():
            self.compensated[axis] = True
        self.pending_compensation = None


"""
Throw an exception if axis out of expected range
"""


class SoftLimitMM(MotionModifier):
    def __init__(self, motion, soft_limits):
        super().__init__(motion)
        self.soft_limits = soft_limits

    def move_absolute_pre(self, pos, options={}):
        cur_pos_xyz = self.motion.cur_pos_cache()
        for axis, axpos in pos.items():
            limit = self.soft_limits.get(axis)
            if not limit:
                continue
            axmin, axmax = limit
            cur_pos = cur_pos_xyz[axis]
            # Reject if its out of bounds and making things worse
            # Otherwise user can't recover
            if not self.motion.axis_pos_in_range(axis, axmin, axpos, axmax):
                # But maybe its making it better?
                if axpos < axmin and axpos < cur_pos or axpos > axmax and axpos > cur_pos:
                    raise AxisExceeded(
                        "axis %s: move violates %0.3f <= new pos %0.3f <= %0.3f"
                        % (axis, axmin, axpos, axmax))

    def move_relative_pre(self, pos, cur_pos=None, options={}):
        assert 0, "FIXME: unsupported"
        if cur_pos is None:
            cur_pos = self.motion.cur_pos_cache()
        self.move_absolute_pre(
            self.motion.estimate_relative_pos(pos, cur_pos=cur_pos))

    def jog_rel(self, pos, rate_ref, options={}):
        """
        # Don't allow going beyond machine limits
        # Jogs are relative though so we need to check against global coordinates
        # and maybe do an adjustment

        WARNING: due to queuing this logic isn't perfect

        Two major adjustment cases:
        -We are close to end. Reduce jog to a safe value
        -We have already exceed axis and are trying to make it worse. Zero jog
        """
        if options.get("trim", True):
            requested_final_pos = {}
            cur_pos = self.motion.cur_pos_cache()
            for axis, delta in pos.items():
                assert abs(delta) < 1e6, (axis, delta)
                start = None
                if self.motion.jog_estimated_end:
                    start = self.motion.jog_estimated_end.get(axis, None)
                    # print("estimated end", axis, start)
                if start is None:
                    start = cur_pos[axis]
                assert abs(start) < 1e6, (axis, start, cur_pos)
                requested_final_pos[axis] = start + delta
            actual_final_pos = dict(requested_final_pos)
            self.jog_abs(actual_final_pos, rate_ref)
            # print("trim check", requested_final_pos, actual_final_pos)
            # Now calculate the adjusted jogs (if any adjustment)
            for axis in set(pos.keys()):
                # May have been trimmed as out of range
                # Note if all are trimmed will throw AxisExceeded
                if axis not in actual_final_pos:
                    del pos[axis]
                else:
                    # If jog was trimmed, make relative adjustment
                    adjustment = actual_final_pos[axis] - requested_final_pos[
                        axis]
                    #if adjustment:
                    #    print(f"DEBUG: jog adjustment {adjustment}")
                    pos[axis] += adjustment
        else:
            self.move_relative_pre(pos)

    def jog_abs(self, pos, rate_ref, options={}):
        """
        # Don't allow going beyond machine limits
        # Jogs are relative though so we need to check against global coordinates
        # and maybe do an adjustment

        WARNING: due to queuing this logic isn't perfect

        Two major adjustment cases:
        -We are close to end. Reduce jog to a safe value
        -We have already exceed axis and are trying to make it worse. Zero jog
        """
        pos_orig = dict(pos)
        if options.get("trim", True):
            assert len(pos)
            # print("")
            # print("initial jog vals", scalars)
            soft_limits = self.motion.get_soft_limits()
            # print("soft_limits", soft_limits)
            cur_pos = self.motion.cur_pos_cache()
            # print("cur_pos", cur_pos)
            # print("new_pos", new_pos)
            for axis in list(pos.keys()):
                # Make the jog reach no further than the limit
                ax_min = soft_limits["mins"].get(axis)
                if ax_min is not None and pos[axis] < ax_min:
                    # In red zone but getting better? Allow it
                    if pos[axis] >= cur_pos[axis]:
                        pass
                    # Could we move closer but not exceed?
                    elif ax_min < cur_pos[axis]:
                        pos[axis] = ax_min
                    # Out of range and making it worse
                    # Drop it
                    else:
                        del pos[axis]
                        continue
                ax_max = soft_limits["maxs"].get(axis)
                if ax_max is not None and pos[axis] > ax_max:
                    # In red zone but getting better? Allow it
                    if pos[axis] <= cur_pos[axis]:
                        pass
                    # Could we move closer but not exceed?
                    elif ax_max > cur_pos[axis]:
                        pos[axis] = ax_max
                    # Out of range and making it worse
                    # Drop it
                    else:
                        del pos[axis]
                        continue
            # print("final jog vals", scalars)
            if len(pos) == 0:
                axes = ", ".join(list(pos_orig.keys()))
                raise AxisExceeded(
                    f"Jog dropped: all moves ({axes}) would exceed axis")
        else:
            self.move_absolute_pre(pos)


"""
Scale axes such as for a gearbox or to reverse direction
"""


class ScalarMM(MotionModifier):
    def __init__(self, motion, scalars=None):
        """
        scalar: dict of each axis scalar to convert from user to machine coordinate system
            ex: "x": 2.0 => if user commands 1.2 mm move machine 2.4 mm
        machine_wcs_offsets: dict of machine offsets that should be subtracted to get absolute machine position
            ex: "x": 120.0 => a move to absolute position 200.0 will be at machine coordinate 80.0
            Be very careful as GRBL MPos is always in machine position
            However moves are in WCS positions
        """

        super().__init__(motion)
        if not scalars:
            scalars = {}
        self.scalars = scalars

    def scale_user2machine_rel(self, pos):
        """
        Scale an external coordinate system to an internal coordinate system
        External: what user sees
        Internal: what the machine uses
        Fixup layer for gearboxes and such
        """
        for k, v in dict(pos).items():
            pos[k] = v * self.scalars.get(k, 1.0)

    def scale_user2machine_abs(self, pos):
        """
        Scale an external coordinate system to an internal coordinate system
        External: what user sees
        Internal: what the machine uses
        Fixup layer for gearboxes and such
        """
        # pos_in = dict(pos)
        for k, v in dict(pos).items():
            pos[k] = v * self.scalars.get(
                k, 1.0) + self._machine_wcs_offsets.get(k, 0.0)
        # print(f"tmp scale_user2machine_abs {pos_in} => {pos}")

    def scale_machine2user_rel(self, pos):
        """
        Opposite of scale_e2i
        """
        for k, v in dict(pos).items():
            pos[k] = v / self.scalars.get(k, 1.0)

    def scale_machine2user_abs(self, pos):
        """
        Opposite of scale_e2i
        """
        for k, v in dict(pos).items():
            pos[k] = v / self.scalars.get(k, 1.0)

    def pos(self, pos):
        # Pos is reported in MPos
        for k, v in dict(pos).items():
            pos[k] = v / self.scalars.get(k, 1.0)

    def move_absolute_pre(self, pos, options={}):
        for k, v in dict(pos).items():
            pos[k] = v * self.scalars.get(k, 1.0)

    def move_relative_pre(self, pos, options={}):
        for k, v in dict(pos).items():
            pos[k] = v * self.scalars.get(k, 1.0)

    def update_status(self, status):
        if "pos" in status:
            # print('status scaling1 %s' % status["pos"])
            self.scale_machine2user_abs(status["pos"])
            # print('status scaling2 %s' % status["pos"])

    def jog_abs(self, pos, rate_ref, options={}):
        # print("jog scale in", scalars)
        self.scale_user2machine_abs(pos)
        """
        Rate is tricky since it applies to all axes but they may not be at the same scalar
        In practice jogged axes should be similar but who knows
        Favor the lowest possible rate to avoid going too fast?
        """
        # assert rate_ref[0] > 0, rate_ref[0]
        rate_candidates = dict([(axis, rate_ref[0]) for axis in pos.keys()])
        self.scale_user2machine_rel(rate_candidates)
        rate_ref[0] = min([abs(x) for x in rate_candidates.values()])
        # assert rate_ref[0] > 0, rate_ref[0]
        # print("jog scale out", scalars)

    def jog_rel(self, pos, rate_ref, options={}):
        # print("jog scale in", scalars)
        # print("jog rel in", pos)
        self.scale_user2machine_rel(pos)
        # print("jog rel out", pos)
        """
        Rate is tricky since it applies to all axes but they may not be at the same scalar
        In practice jogged axes should be similar but who knows
        Favor the lowest possible rate to avoid going too fast?
        """
        # assert rate_ref[0] > 0, rate_ref[0]
        rate_candidates = dict([(axis, rate_ref[0]) for axis in pos.keys()])
        self.scale_user2machine_rel(rate_candidates)
        rate_ref[0] = min([abs(x) for x in rate_candidates.values()])
        # assert rate_ref[0] > 0, rate_ref[0]
        # print("jog scale out", scalars)

    def munge_axes_user2machine_rel(self, axes):
        self.scale_user2machine_rel(axes)

    def munge_axes_user2machine_abs(self, axes):
        self.scale_user2machine_abs(axes)

    def munge_axes_machine2user_rel(self, axes):
        self.scale_machine2user_rel(axes)

    def munge_axes_machine2user_abs(self, axes):
        self.scale_machine2user_abs(axes)


class MotionHAL:
    def __init__(self, log=None, verbose=None, microscope=None):
        # Per axis? Currently is global
        assert microscope
        self.microscope = microscope
        self.stop_on_del = True
        self.modifiers = None
        self.options = None
        self.jog_estimated_end = None

        # Cache some computed values
        self._hal_max_velocities = None
        self._hal_max_accelerations = None
        self._hal_machine_limits = None
        self._steps_per_mm = None
        self._epsilon = None

        # Used to cache position while computing motion modifiers
        self._cur_pos_cache = None

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
        # An *estimate* of where jogs will land us if they all complete
        # There are several ways this can go wrong
        # ex: if we start jogging when not idle
        self.jog_estimated_end = None
        self.last_jog_time = None
        self.ask_home = self.default_ask_home
        self.home_progress = self.default_home_progress

    def __del__(self):
        self.close()

    def default_ask_home(self):
        while True:
            print("")
            print("System is not homed. Enter Y to home or N to abort")
            print(
                "Ensure system is clear of fingers, cables, etc before proceeding"
            )
            got = input().upper()
            if got == "Y":
                break
            elif got == "N":
                raise HomingAborted("Homing aborted")
            print("Invalid response")
            print("")

    # FIXME: not ready yet
    def default_home_progress(self, state, percent, remaining):
        print("Homing: %0.1f%% done, %s remaining" %
              (percent, time_str(remaining)))

    def set_ask_home(self, func):
        self.ask_home = func

    def set_home_progress(self, func):
        self.home_progress = func

    def epsilon(self):
        """
        The most precise system is currently 125 nm
        Set a 10 nm default epsilon for now
        """
        return self._epsilon

    def axes_abs(self, axes):
        for k, v in axes.items():
            axes[k] = abs(v)

    def munge_axes_user2machine_rel(self, axes, abs_=False):
        for modifier in self.iter_active_modifiers():
            modifier.munge_axes_user2machine_rel(axes)
        if abs_:
            self.axes_abs(axes)
        return axes

    def munge_axes_user2machine_abs(self, axes, abs_=False):
        for modifier in self.iter_active_modifiers():
            modifier.munge_axes_user2machine_abs(axes)
        if abs_:
            self.axes_abs(axes)
        return axes

    def munge_axes_machine2user_rel(self, axes, abs_=False):
        for modifier in self.iter_active_modifiers():
            modifier.munge_axes_machine2user_rel(axes)
        if abs_:
            self.axes_abs(axes)
        return axes

    def munge_axes_machine2user_abs(self, axes, abs_=False):
        for modifier in self.iter_active_modifiers():
            modifier.munge_axes_machine2user_abs(axes)
        if abs_:
            self.axes_abs(axes)
        return axes

    def _get_machine_limits(self):
        return {"mins": {}, "maxs": {}}

    def _get_steps_per_mm(self):
        """
        These values are always positive / relative
        """
        return {
            "x": int(1 / 0.000010),
            "y": int(1 / 0.000010),
            "z": int(1 / 0.000010),
        }

    def get_machine_limits(self):
        """
        return like
        {
            "mins": {
                "x": 0.0,
                ...
            },
            "maxs": {
                "x": 123.0,
                ...
            },
        """
        assert self.options is not None, "Not configured"
        return self._hal_machine_limits

    def get_soft_limits(self):

        # Unlike machine limits which are up to the HAL,
        # these are user specified in the "final" coordinate system
        """
        config is like
        "soft_limits": {
            "xmin": -5.0,
            "xmax": 35.0,
            "ymin": -5.0,
            "ymax": 35.0,
        },
        but returned like
        {'x': (-5.0, 35.0), 'y': (-5.0, 35.0)}

        but transform to be like above function
        """
        assert self.options is not None, "Not configured"
        return self._pyuscope_soft_limits

    def _get_max_velocities(self):
        """
        assume GRBL units: 110: "X Max rate, mm/min",
        """
        assert 0, "Required"

    def get_max_velocities(self):
        """
        assume GRBL units: 120: "X Acceleration, mm/sec^2",
        """
        assert self.options is not None, "Not configured"
        return self._hal_max_velocities

    def _get_max_accelerations(self):
        assert 0, "Required"

    def get_max_accelerations(self):
        assert self.options is not None, "Not configured"
        return self._hal_max_accelerations

    def equivalent_axis_pos(self, axis, value1, value2):
        """
        Is value within rounding error of the other?
        Ex: GRBL will have an accurate step count but GUI still still report in mm
        """
        delta = abs(value2 - value1)
        return delta / 2 <= self.epsilon()[axis]

    def is_zero(self, axis, value):
        return self.equivalent_axis_pos(axis, value, 0.0)

    def axis_pos_in_range(self, axis, axis_min, value, axis_max):
        """
        Return true if the position is in range subject to minimum movement size
        Prevents rounding errors when checking valid positions
        """
        return axis_min <= value <= axis_max or self.equivalent_axis_pos(
            axis, axis_min, value) or self.equivalent_axis_pos(
                axis, axis_max, value)

    def cache_constants(self):
        def calc_epsilon():
            # More precise but harder to use in most contexts
            self._steps_per_mm = self._get_steps_per_mm()

            self._epsilon = {}
            for axis in self.axes():
                self._epsilon[axis] = abs(1 / self._steps_per_mm[axis])
            self.assert_all_axes(self._epsilon)

        calc_epsilon()

        def calc_max_velocities():
            self._hal_max_velocities = self._get_max_velocities()
            # print("calc max velocities", self._hal_max_velocities)
            for v in self._hal_max_velocities.values():
                assert v > 0, self._hal_max_velocities
            self.munge_axes_machine2user_rel(self._hal_max_velocities,
                                             abs_=True)
            # print("calc max velocities", self._hal_max_velocities)
            for v in self._hal_max_velocities.values():
                assert v > 0, self._hal_max_velocities
            # Jogging depends on this
            self.assert_all_axes(self._hal_max_velocities)

        calc_max_velocities()

        def calc_max_accelerations():
            self._hal_max_accelerations = self._get_max_accelerations()
            self.munge_axes_machine2user_rel(self._hal_max_accelerations,
                                             abs_=True)
            # 2023-09-18: not currently used
            # Could drop this requirement if good reason
            self.assert_all_axes(self._hal_max_accelerations)

        calc_max_accelerations()

        def calc_machine_limits():
            # XXX: min / max for an inverted axis gets nasty
            # but currently we don't actively use machine limits
            # in favor of higher level pyuscope soft limits
            # xxx: think this is always defined?
            # unclear if this is accurate on all machines though
            self._hal_machine_limits = dict(self._get_machine_limits())
            if "mins" in self._hal_machine_limits:
                self.munge_axes_machine2user_abs(
                    self._hal_machine_limits["mins"])
            if "maxs" in self._hal_machine_limits:
                self.munge_axes_machine2user_abs(
                    self._hal_machine_limits["maxs"])

        calc_machine_limits()

        def calc_soft_limits():
            limits = self.options.get("soft_limits", {})
            if limits is None:
                limits = {}
            mins = OrderedDict()
            maxs = OrderedDict()
            for axis in "xyz":
                this = limits.get(axis, None)
                if this is not None:
                    mins[axis] = this[0]
                    maxs[axis] = this[1]
            self._pyuscope_soft_limits = {
                "mins": mins,
                "maxs": maxs,
            }

        calc_soft_limits()

    def configure(self, options):
        # MotionModifier's
        self.modifiers = OrderedDict()
        self.disabled_modifiers = set()
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

        # need to let GRBL fetch values
        self.cache_constants()

    def _configured(self):
        pass

    def disable_modifier(self, name):
        self.disabled_modifiers.add(name)

    def enable_modifier(self, name, lazy=True):
        try:
            self.disabled_modifiers.remove(name)
        except KeyError:
            if not lazy:
                raise

    def backlash_disable(self):
        """
        Temporarily disable backlash correction, if any
        """
        self.disable_modifier("backlash")

    def backlash_enable(self):
        """
        Revert above
        """
        self.enable_modifier("backlash", lazy=True)

    def iter_active_modifiers(self):
        assert self.modifiers is not None, "Not configured yet"
        for modifier_name, modifier in self.modifiers.items():
            if modifier_name in self.disabled_modifiers:
                continue
            yield modifier

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

    def since_last_motion(self):
        return time.time() - self.mv_lastt

    def update_status(self, status):
        # Ignore updates before configured
        if self.modifiers is None:
            return
        # print("update_status begin: %s" % (status,))
        for modifier in self.iter_active_modifiers():
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

    def only_used_axes(self, d):
        """
        Filter a dict containing raw machine axes into a dict
        containing axes actually in use
        """
        ret = {}
        for axis in self.axes():
            if axis in d:
                ret[axis] = d[axis]
        return ret

    def assert_all_axes(self, axes):
        """
        Assert that axes contains one key for each active axis and nothing else  
        """
        axes = set(axes)
        have = set(self.axes())
        assert axes == have, (f"expected axes {have}, but was given {axes}")

    def home(self):
        '''Set current position to 0.0'''
        raise Exception("Required for tuning")

    def ret0(self):
        '''Return to origin'''
        self.move_absolute(dict([(k, 0.0) for k in self.axes()]))

    def process_pos(self, pos):
        # print("pos init %s" % (pos,))
        for modifier in self.iter_active_modifiers():
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
        assert self.jog_estimated_end is None, f"Can't move while jogging ({self.jog_estimated_end})"
        if len(pos) == 0:
            return
        self.validate_axes(pos.keys())
        self.verbose and print("motion: move_absolute(%s)" % (pos_str(pos)))
        self.cur_pos_cache_invalidate()
        self._move_absolute_wrap(pos, options=options)

    def _move_absolute_wrap(self, pos, options={}):
        '''Absolute move to positions specified by pos dict'''
        try:
            for modifier in self.iter_active_modifiers():
                modifier.move_absolute_pre(pos, options=options)
            _ret = self._move_absolute(pos)
            for modifier in self.iter_active_modifiers():
                modifier.move_absolute_post(True, options=options)
            self.mv_lastt = time.time()
        finally:
            self.cur_pos_cache_invalidate()

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
            # print(f"estimate_relative_pos() {cur_pos[axis]} + {axdelta}")
            abs_pos[axis] = cur_pos[axis] + axdelta
        return abs_pos

    def move_relative(self, pos, options={}):
        '''Absolute move to positions specified by pos dict'''
        assert self.jog_estimated_end is None, "Can't move while jogging"
        if len(pos) == 0:
            return
        self.validate_axes(pos.keys())

        self.verbose and print("motion: move_relative(%s)" % (pos_str(pos)))
        # XXX: invalidates on recursion
        self.cur_pos_cache_invalidate()
        final_abs_pos = self.estimate_relative_pos(
            pos, cur_pos=self.cur_pos_cache())
        # Relative move full stack just too hard to support well for now
        # Ex: setting up w/ backlash compensation is difficult
        # And don't see a real reason to support it
        return self._move_absolute_wrap(final_abs_pos, options=options)
        """
        try:
            for modifier in self.iter_active_modifiers():
                modifier.move_relative_pre(pos, options=options)
            _ret = self._move_relative(pos)
            for modifier in self.iter_active_modifiers():
                modifier.move_relative_post(True, options=options)
            self.mv_lastt = time.time()
        finally:
            self.cur_pos_cache_invalidate()
        """

    def _move_relative(self, delta):
        '''Relative move to positions specified by delta dict'''
        raise NotSupported("Required for planner")

    def validate_axes(self, axes):
        for axis in axes:
            if axis not in self.axes():
                raise ValueError("Got axis %s but expect axis in %s" %
                                 (axis, self.axes()))

    def jog_rel(self, axes, rate, options={}, keep_pos_cache=False):
        """
        scalars: generally either +1 or -1 per axis to jog
        Final value is globally multiplied by the jog_rate and individually by the axis scalar
        """
        # Try to estimate if jog would go over limit
        # Always allow moving away from the bad area though if we are already in there
        if len(axes) == 0:
            return
        assert rate >= 0
        axes = dict(axes)
        # XXX: invalidates on recursion
        if not keep_pos_cache:
            self.cur_pos_cache_invalidate()
        try:
            # print("jog in", scalars, rate)
            current_position = self.cur_pos_cache()
            self.validate_axes(axes.keys())
            rate_ref = [rate]
            for modifier in self.iter_active_modifiers():
                modifier.jog_rel(axes, rate_ref, options=options)
            rate = rate_ref[0]
            # print("jog to grbl", scalars, rate)
            self._jog_rel(axes, rate)

            # May have been trimmed
            # Convert it back in lieu of the original value
            # print("jog_rel finishing, estimated end", self.jog_estimated_end)
            # print("jog_rel finishing, pre-munge", axes)
            self.munge_axes_machine2user_rel(axes)
            # print("jog_rel finishing, post-munge", axes)

            # Jog was accepted. Update estimate
            if self.jog_estimated_end is None:
                self.jog_estimated_end = {}
            for k, v in axes.items():
                if k in self.jog_estimated_end:
                    self.jog_estimated_end[k] += v
                else:
                    self.jog_estimated_end[k] = current_position[k] + v
            # print("jog_rel finishing, estimated end", self.jog_estimated_end)
        finally:
            self.cur_pos_cache_invalidate()

    def _jog_rel(self, pos, rate):
        '''
        axes: dict of axis with value to move
        WARNING: under development / unstable API
        '''
        raise NotSupported("Required for jogging")

    # WARNING: jog_abs is generally not reccomended
    # this API may be removed

    def jog_abs(self, axes, rate, options={}, keep_pos_cache=False):
        """
        scalars: generally either +1 or -1 per axis to jog
        Final value is globally multiplied by the jog_rate and individually by the axis scalar
        """
        # Try to estimate if jog would go over limit
        # Always allow moving away from the bad area though if we are already in there
        if len(axes) == 0:
            return
        assert rate >= 0
        axes = dict(axes)
        # XXX: invalidates on recursion
        if not keep_pos_cache:
            self.cur_pos_cache_invalidate()
        try:
            # print("jog in", scalars, rate)
            self.validate_axes(axes.keys())
            rate_ref = [rate]
            for modifier in self.iter_active_modifiers():
                modifier.jog_abs(axes, rate_ref, options=options)
            rate = rate_ref[0]
            # print("jog to grbl", scalars, rate)
            self._jog_abs(axes, rate)

            # May have been trimmed
            # Convert it back in lieu of the original value
            self.munge_axes_machine2user_abs(axes)

            # Jog was accepted. Update estimate
            if self.jog_estimated_end is None:
                self.jog_estimated_end = {}
            for k, v in axes.items():
                self.jog_estimated_end[k] = v
        finally:
            self.cur_pos_cache_invalidate()

    def _jog_abs(self, pos, rate):
        '''
        axes: dict of axis with value to move
        WARNING: under development / unstable API
        '''
        raise NotSupported("Required for jogging")

    def jog_fractioned(self, axes, period=1.0):
        """
        Axes: values containing jog rate in rane [-1.0 to +1.0]
            +1.0 => jog at max speed
        period: how often commands will be issued
            longer period => jog further to keep constant
        """
        # print("")
        # print("jog_fractioned", axes, period, time.time())
        tstart = time.time()
        # Slightly under-stuff so that the queue is not stacking
        # FIXME: this is more than it should be and still stacking
        # Investigate why
        global_scalar = 0.70
        self.validate_axes(axes.keys())
        for axis, frac in axes.items():
            assert -1.0 <= frac <= +1.0, (axis, frac)
        # How fast, in machine units, were we requested to go?
        # GRBL: machine reports velocity in mm / min, feed rate is also mm / min
        # However note that acceleration is in mm / sec2
        velocities_per_minute = dict([(axis,
                                       self.get_max_velocities()[axis] * frac)
                                      for axis, frac in axes.items()])
        velocities_per_second = dict([
            (axis, self.get_max_velocities()[axis] / 60.0 * frac)
            for axis, frac in axes.items()
        ])
        rel_pos = dict([(axis,
                         velocities_per_second[axis] * period * global_scalar)
                        for axis, frac in velocities_per_second.items()])

        # print("scalars", scalars)
        # Usually only XY is jogged together and they typically have the same max velocity
        # Take the smallest possible value so they all go the same scaled speed
        # rate = min([abs(velocity) for velocity in velocities.values()])
        # min => max: the other axis is already a smaller step
        # this will just hurt the major axis
        rate = max(
            [abs(velocity) for velocity in velocities_per_minute.values()])
        rate = int(round(max(1, rate)))

        if 0:
            print("jog_fractioned()")
            print("  now", time.time())
            if self.last_jog_time:
                last_dt = self.last_jog_time - tstart
                print("  last", last_dt)
                if last_dt > 0.3:
                    print("    *****")
            print("  fractions", axes)
            print("  max_velocities per minute", self.get_max_velocities())
            print("  velocities_per_second desired", velocities_per_second)
            print("  period", period)
            print("  rel_pos", rel_pos)
            print("  rate", rate)
        self.jog_rel(rel_pos, rate, keep_pos_cache=True)
        # tend = time.time()
        # print("jog took", tend - tstart)
        self.last_jog_time = tstart

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

    def _jog_cancel(self):
        raise NotSupported("Required for jogging")

    def jog_cancel(self):
        self._jog_cancel()
        # No longer jogging
        self.jog_estimated_end = None
        self.last_jog_time = None

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

    def _stop(self):
        '''Stop motion as soon as convenient.  Motors must maintain position'''
        pass

    def stop(self):
        self._stop()
        self.jog_estimated_end = None
        self.last_jog_time = None

    def _estop(self):
        self.stop()

    def estop(self):
        '''Stop motion ASAP.  Motors are not required to maintain position'''
        self._estop()

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


'''
Has no actual hardware associated with it
'''


class MockHal(MotionHAL):
    def __init__(self, axes='xyz', **kwargs):
        self._axes = list(axes)
        MotionHAL.__init__(self, **kwargs)

        self._pos_cache = {}
        # Assume starting at 0.0 until causes problems
        for axis in self._axes:
            self._pos_cache[axis] = 0.0

    def _log(self, msg):
        self.log('Mock: ' + msg)

    def axes(self):
        return self._axes

    def _get_max_velocities(self):
        return {'x': 100.0, 'y': 100.0, 'z': 60.0}

    def _get_max_accelerations(self):
        return {'x': 100.0, 'y': 100.0, 'z': 60.0}

    def home(self):
        for axis in self._axes:
            self._pos_cache[axis] = 0.0

    def take_picture(self, file_name):
        self._log('taking picture to %s' % file_name)

    def _move_absolute(self, pos):
        for axis, apos in pos.items():
            self._pos_cache[axis] = apos
        0 and self._log('absolute move to ' + pos_str(pos))

    def _move_relative(self, delta):
        for axis, adelta in delta.items():
            self._pos_cache[axis] += adelta
        0 and self._log('relative move to ' + pos_str(delta))

    def _jog(self, axes, rate):
        for axis, adelta in axes.items():
            self._pos_cache[axis] += adelta

    def _pos(self):
        return self._pos_cache

    def settle(self):
        # No hardware to let settle
        pass

    def ar_stop(self):
        pass

    def log_info(self):
        """
        Print some high level debug info
        """
        self.log("Motion: no additional info")


"""
Based on a real HAL but does no movement
Ex: inherits movement
"""


class DryHal(MotionHAL):
    def __init__(self, hal, log=None):
        self.hal = hal
        self.stop_on_del = True

        self._posd = self.hal.pos()

        super().__init__(log=log, verbose=hal.verbose)

        # Don't re-apply pipeline (scaling, etc)
        self.configure({})

    def _log(self, msg):
        self.log('Dry: ' + msg)

    def axes(self):
        return self.hal.axes()

    def filter_axes(self, vals):
        ret = {}
        for k in self.axes():
            if k in vals:
                ret[k] = vals[k]
        return ret

    def home(self):
        for axis in self._axes:
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

    def _get_max_velocities(self):
        return self.hal.get_max_velocities()

    def _get_max_accelerations(self):
        return self.hal.get_max_accelerations()


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
