#!/usr/bin/env python3
'''
75 XY Controller serial port interface
Was used for debugging while upgrading controller firmware

Unit back:
75 XY Controller

PCB silkscreen:
ESC-4 2400

Serial power on message:
X-Y (T-R) Stage, Version 2.11a, 09/11/98 09:43:04
Part# 80W03091/81P03092
'''
"""
rea
!
    some sort of command modifier?
    looks like I can input commands
#
    echos command, excluding #
%
    echos command, including %
"""

import uscope.planner
from uscope.hal.img.imager import Imager
from uscope.hal.cnc.hal import MotionHAL
from uscope.util import add_bool_arg

import argparse
import json
import os
import shutil
import time
import binascii
import serial
import time


class Timeout(Exception):
    pass


"""
Units
100000 => looks like about 10 mm
So looks like native units might be um
Takes about 9.0 seconds to move 10 mm
"""


class XY75:
    def __init__(self, port='/dev/ttyUSB0', init=True, verbose=False):
        self.verbose = verbose
        self.serial = serial.Serial(
            port,
            baudrate=9600,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            rtscts=False,
            dsrdtr=False,
            xonxoff=False,
            timeout=0,
            # Blocking writes
            writeTimeout=None)
        self.serial.flushInput()
        self.serial.flushOutput()
        # self.idle()

        if init:
            self.who()
            self.wait_idle()
            self.je(0)

    def idle(self):
        pass

    def flush(self):
        """
        Wait to see if there is anything in progress
        """

        timeout = self.serial.timeout
        try:
            self.serial.timeout = 0.1
            while True:
                c = self.serial.read()
                if not c:
                    return
        finally:
            self.serial.timeout = timeout

    def send(self, out):
        if self.verbose:
            print('xy75 DEBUG: sending: %s' % (out, ))
        self.serial.write((out + "\n").encode('ascii'))
        self.serial.flush()

    def recv(self, timeout=1.0):
        ret = ''
        tstart = time.time()
        while True:
            c = self.serial.read(1)
            if not c:
                if timeout is not None and time.time() - tstart >= timeout:
                    raise Timeout('Timed out (%0.1f). Read %u' %
                                  (timeout, len(ret)))
                continue
            c = c.decode("ascii")
            if c == '\r':
                return ret.strip()
            ret += c
            if self.verbose:
                print('xy75 DEBUG: recv: %s' % (ret, ))

    def command(self, out):
        self.send(out)
        ret = []
        while True:
            l = self.recv()
            # print("got", l, l == "READY")
            if l == 'READY':
                return ret
            # print("not READY")
            ret.append(l)

    def cmd_intline(self, out, nparts):
        lines = self.command(out)
        assert len(lines) == 1
        ret = [int(x) for x in lines[0].split(',')]
        assert len(ret) == nparts
        return tuple(ret)

    def cmd_line(self, out):
        lines = self.command(out)
        assert len(lines) == 1
        return lines[0]

    def who(self):
        '''
        who
        Automated stage controller.
        X-Y (T-R) Stage, Version 2.11a, 09/11/98 09:43:04
        Part# 80W03091/81P03092
        Copyright (c) RJ Lee Group, Inc, 1993,1994.  ALL RIGHTS RESERVED.
        READY
        '''
        ret = self.command("who")
        assert len(ret) == 4
        assert ret[0] == "Automated stage controller."
        assert ret[1] == "X-Y (T-R) Stage, Version 2.11a, 09/11/98 09:43:04"
        assert ret[2] == "Part# 80W03091/81P03092"
        assert ret[
            3] == "Copyright (c) RJ Lee Group, Inc, 1993,1994.  ALL RIGHTS RESERVED."
        return ret

    def p(self):
        '''
        p
        1, -1
        -980, -4752

        p
        2, -1
        READY

        p [-3, -10]
        p [-2, 9]
        p [-3, 8]
        p [-4, -6]
        '''
        return self.cmd_intline("p", 2)

    def is_idle(self):
        return self.c() == (0, 0)

    def wait_idle(self, timeout=10.0):
        tstart = time.time()
        while True:
            self.p()
            dt = time.time() - tstart
            if self.is_idle():
                print("Idle after %0.1f sec" % (dt, ))
                return
            if dt >= timeout:
                print("status")
                print(self.c())
                raise Timeout()

    def a(self):
        assert len(self.command("a")) == 0

    def c(self):
        """
        FIXME: ret

        c
        0.0

        bit  flags maybe?
        c [1, 1]
        c [3, 1]
        READY
        """
        return self.cmd_intline("c", 2)

    def d(self):
        """
        d
        0 Y:21   -1188   -1182     65 2fda6  X:21       0       0      0 1fe77
        """
        return self.cmd_line("d")

    def e(self):
        """
        FIXME: ret
        e
        0.0
        """
        assert len(self.command("e")) == 0

    def rea(self):
        assert len(self.command("rea")) == 0

    def asterix(self):
        """
        A bunch of info

        errorcode = 0, warning = 0
        X=-980     PWM0=0    XDIR=1     Y=-4752    PWM1=0    YDIR=1
        dx=   0  dy=  0
        state = 0, 0
        traj.XY.v = 0, 0
        traj.XY.p = -980, -4752
        XFF =64   800   XPID=300  0    600 
        prof.x.d0=128000  v0=46811  v1=5120  a0=2048
        YFF =64   800   YPID=300  0    600 
        prof.y.d0=128000  v0=46811  v1=5120  a0=2048
        """
        assert len(self.command("*")) == 10

    def je(self, n):
        '''
        je 0
        Not sure what this is but its clear at init
        clear errors maybe?
        '''
        assert len(self.command("je %u" % n)) == 0

    def get_m(self):
        """
        m
        1, -1
        1, -3956
        1, -4752
        turning axis by hand moves this
        x, y
        
        Looks like it might be an error term (ie distance to go), not position
        If you move, rotate by hand, and then move again to same position
        It will correct on the second move
        So tracks position when not moving but
        """
        return self.cmd_intline("m", 2)

    def m(self, x, y):
        '''
        Abosolute move in um
        Signed
        m 14186,28373
        '''
        assert len(self.command("m %u,%u" % (x, y))) == 0

    def move_to(self, x, y):
        self.wait_idle()
        self.m(x, y)
        self.wait_idle()

    def xh(self):
        """
        xh
        -10000, -12800, -700, -360000, 160, 120
        """
        assert len(self.command("xh")) == 1

    def xl(self):
        """
        xl
        500, 12320, 1120, 39200
        """
        assert len(self.command("xl")) == 1

    def xp(self):
        """
        xp
        300, 0, 600
        """
        assert len(self.command("xp")) == 1

    def yp(self):
        """
        yp
        300, 0, 600
        """
        assert len(self.command("yp")) == 1


def init_sequence(xy):
    """
    Roughly what was observed at startup
    """

    xy.who()
    for i in range(4):
        print("")
        print("p", xy.p())
        print("c", xy.c())
        time.sleep(1)

    xy.je(0)

    print("")
    xy.m(1, 1)
    print("p", xy.p())
    print("c", xy.c())

    time.sleep(1)

    print("p", xy.p())
    print("c", xy.c())

    print("")
    xy.m(1, 2)
    print("p", xy.p())
    print("c", xy.c())

    time.sleep(1)

    print("p", xy.p())
    print("c", xy.c())


def main():
    parser = argparse.ArgumentParser(description='Planner module command line')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('x', type=int, help="In um")
    parser.add_argument('y', type=int, help="In um")
    args = parser.parse_args()

    xy = XY75(verbose=args.verbose)
    xy.move_to(args.x, args.y)
    print("Final position", xy.p())


if __name__ == "__main__":
    main()
