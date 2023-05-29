#!/usr/bin/env python3

from uscope.util import add_bool_arg
from uscope.imager import gst
import uscope.planner
from uscope.config import get_usc
from uscope.planner.planner_util import microscope_to_planner_config, get_planner
from uscope.motion.plugins import get_motion_hal
from uscope.util import default_date_dir
import os
from collections import OrderedDict
from uscope.motion.hal import Imager, MotionHAL
from uscope.microscope import Microscope
from uscope.util import tostr
import time


class MyGCodeHalImager(Imager):
    def __init__(self, hal):
        self.hal = hal
        self.last_properties_change = time.time()
        self.nimages = 0

    def take(self):
        print("take called")
        self.nimages += 1
        self.hal._line(f"(Dwell to snap picture {self.nimages})")
        self.hal._dwell(10)

    def wh(self):
        return (5472, 3648)

    def remote(self):
        return True


'''
http://linuxcnc.org/docs/html/gcode/gcode.html

Static gcode generator using coolant hack
Not to be confused LCncHal which uses MDI g-code in real time

M7 (coolant on): tied to focus / half press pin
M8 (coolant flood): tied to snap picture
    M7 must be depressed first
M9 (coolant off): release focus / picture
'''


class MyGCodeHal(MotionHAL):
    def __init__(self, axes='xyz', fnout=None, **kwargs):
        self._axes = list(axes)
        self._buff = ""
        self.fnout = fnout
        self._pos_cache = {}
        # Assume starting at 0.0 until causes problems
        for axis in self._axes:
            self._pos_cache[axis] = 0.0

        MotionHAL.__init__(self, **kwargs)

        self._line('(pyuscope g-code generator)')

    def flush(self):
        with open(self.fnout, "w") as f:
            f.write(self._buff)

    def __del__(self):
        self.flush()

    def imager(self):
        return MyGCodeHalImager(self)

    def comment(self, s=''):
        if len(s) == 0:
            self._line()
        else:
            self._line('(%s)' % s)

    def _line(self, s=''):
        # self.log(s)
        self._buff += s + '\n'

    def begin(self):
        pass

    def actual_end(self):
        self._line()
        self._line('(Done!)')
        self._line('M2')

    def _dwell(self, seconds):
        # P => ms, S => sec ?
        # self._line('G4 P%0.3f' % (seconds, ))
        self._line('G4 P%u' % (seconds, ))

    def get(self):
        return str(self._buff)

    def axes(self):
        return self._axes

    def _log(self, msg):
        self.log('Mock: ' + msg)

    def home(self):
        for axis in self._axes:
            self._pos_cache[axis] = 0.0

    def take_picture(self, file_name):
        self._log('taking picture to %s' % file_name)

    def _move_absolute(self, pos):
        print("move absolute called")
        for axis, apos in pos.items():
            self._pos_cache[axis] = apos
        self._line(
            'G90 G0 ' +
            ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in pos.items()]))

    def _move_relative(self, pos):
        print("move rel called")
        for axis, delta in pos.items():
            self._pos_cache[axis] += delta
        self._line(
            'G91 G0 ' +
            ' '.join(['%c%0.3f' % (k.upper(), v) for k, v in pos.items()]))

    def _pos(self):
        return self._pos_cache

    def settle(self):
        # No hardware to let settle
        pass

    def ar_stop(self):
        pass


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run a plannr job from CLI")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    add_bool_arg(parser,
                 "--center",
                 default=False,
                 help="start/end are center as opposed to canvas coordinates")
    parser.add_argument('--start',
                        default="0,0",
                        help="countour.start x,y. Default: 0,0")
    parser.add_argument('--end',
                        default="0,0",
                        help="countour.end x,y. Default: 0,0")
    parser.add_argument(
        '--corners',
        default=None,
        help=
        "points-3p corners as ll,ul,lr as x0,y0:x1,y1:x2,y2 or x0,y0,z0,...")
    parser.add_argument('--postfix', default="", help="Log file postfix")
    parser.add_argument('--microscope', help="Which microscope config to use")
    parser.add_argument('--objective',
                        default=None,
                        help="Objective to use (by name), objective required")
    parser.add_argument('--objectivei',
                        default=None,
                        help="Objective to use (by index), objective required")
    add_bool_arg(parser,
                 "--dry",
                 default=False,
                 help="Must set to enable real motion")
    parser.add_argument("out", nargs="?", help="File to save to")
    args = parser.parse_args()

    contour = None
    corners = None
    if args.corners:
        corners = OrderedDict()
        big_parts = args.corners.split(":")
        assert len(big_parts) == 3, big_parts

        for cornerk, pointstr in zip(["ll", "ul", "ur"], big_parts):
            parts = [float(x) for x in pointstr.split(",")]
            if len(parts) == 2:
                corners[cornerk] = {"x": parts[0], "y": parts[1]}
            elif len(parts) == 3:
                corners[cornerk] = {
                    "x": parts[0],
                    "y": parts[1],
                    "z": parts[2]
                }
            else:
                assert 0
    else:
        x0, y0 = [float(x) for x in args.start.split(",")]
        x1, y1 = [float(x) for x in args.end.split(",")]
        contour = {
            "start": {
                "x": x0,
                "y": y0,
            },
            "end": {
                "x": x1,
                "y": y1,
            },
        }

    usc = get_usc(name=args.microscope)
    usj = usc.usj
    objectivei = args.objectivei
    if args.objectivei:
        args.objectivei = int(args.objectivei)
    pconfig = microscope_to_planner_config(usj,
                                           contour=contour,
                                           corners=corners,
                                           objectivestr=args.objective,
                                           objectivei=objectivei,
                                           center=args.center)
    root_dir = "out"
    if not os.path.exists(root_dir):
        os.mkdir(root_dir)
    out_dir = default_date_dir(root_dir, "", args.postfix)
    print("Writing files to %s" % out_dir)

    print("Initializing motion/imager...")
    motion = MyGCodeHal(fnout=args.out)
    imager = motion.imager()
    print("System ready")

    microscope = Microscope(imager=imager, motion=motion, auto=True)
    # microscope.kinematics.microscope = microscope

    planner = get_planner(pconfig=pconfig,
                          motion=motion,
                          imager=imager,
                          out_dir=out_dir,
                          dry=args.dry,
                          microscope=microscope)

    planner.run()
    motion.actual_end()
    motion.flush()


if __name__ == "__main__":
    main()
