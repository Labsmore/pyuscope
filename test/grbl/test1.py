#!/usr/bin/env python3
"""
[HLP:$$ $# $G $I $N $x=val $Nx=line $J=line $SLP $C $X $H ~ ! ? ctrl-x]
ok

Case insensitive best I can tell
"""

from uscope import util
import serial
import time
import os


class Timeout(Exception):
    pass


def default_port():
    return "/dev/ttyUSB0"


def trim_data_line(l):
    # print("test", l)
    assert l[0] == "["
    assert l[-1] == "]"
    return l[1:-1]


def trim_status_line(l):
    # print("test", l)
    assert l[0] == "<"
    assert l[-1] == ">"
    return l[1:-1]


class GRBLSer:

    def __init__(self, port="/dev/ttyUSB0", ser_timeout=0.1, verbose=False):
        self.verbose = verbose
        self.verbose and print("opening", port)
        self.serial = serial.Serial(
            port,
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            rtscts=False,
            dsrdtr=False,
            xonxoff=False,
            timeout=ser_timeout,
            # Blocking writes
            writeTimeout=None)
        self.serial.flushInput()
        self.serial.flushOutput()
        self.flush()

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

    def tx(self, out, nl=True):
        if nl:
            out = out + '\r'
        self.serial.write((out).encode('ascii'))
        self.serial.flush()

    def readline(self):
        return self.serial.readline().decode("ascii").strip()

    def txrxs(self, out, nl=True):
        self.tx(out, nl=nl)
        ret = []
        while True:
            l = self.readline()
            if not l:
                raise Timeout()
            elif l == "ok":
                return ret
            elif l.find("error") == 0:
                raise Exception(l)
            else:
                ret.append(trim_data_line(l))

    def txrx0(self, out, nl=True):
        ret = self.txrxs(out, nl=nl)
        assert len(ret) == 0

    def txrx(self, out, nl=True):
        ret = self.txrxs(out, nl=nl)
        assert len(ret) == 1
        return ret[0]

    def help(self):
        """
        [HLP:$$ $# $G $I $N $x=val $Nx=line $J=line $SLP $C $X $H ~ ! ? ctrl-x]
        """
        return self.txrx("$")

    def reset(self):
        """
        ^X
        Grbl 1.1f ['$' for help]
        """

    def dollar(self, command):
        """
        $$
        $0=10
        $1=25
        $2=0
        $3=2
        $4=0
        $5=0
        $6=0
        $10=1
        $11=0.010
        $12=0.002
        $13=0
        $20=0
        $21=0
        $22=0
        $23=0
        $24=25.000
        $25=500.000
        $26=250
        $27=1.000
        $30=1000
        $31=0
        $32=0
        $100=800.000
        $101=800.000
        $102=800.000
        $110=1000.000
        $111=1000.000
        $112=600.000
        $120=30.000
        $121=30.000
        $122=30.000
        $130=200.000
        $131=200.000
        $132=200.000
        """
        pass

    def hash(self):
        """
        [G54:0.000,0.000,0.000]
        [G55:0.000,0.000,0.000]
        [G56:0.000,0.000,0.000]
        [G57:0.000,0.000,0.000]
        [G58:0.000,0.000,0.000]
        [G59:0.000,0.000,0.000]
        [G28:0.000,0.000,0.000]
        [G30:0.000,0.000,0.000]
        [G92:0.000,0.000,0.000]
        [TLO:0.000]
        [PRB:0.000,0.000,0.000:0]
        ok
        """
        return self.txrxs("$#")

    def question(self):
        """
        <Idle|MPos:0.000,0.000,0.000|FS:0,0|WCO:0.000,0.000,0.000>
        <Idle|MPos:0.000,0.000,0.000|FS:0,0|Ov:100,100,100>
        <Idle|MPos:0.000,0.000,0.000|FS:0,0>
        """
        self.tx("?", nl=False)
        l = self.readline()
        return trim_status_line(l)

    def c(self):
        """
        $C
        """

    def g(self):
        """
        $G
        [GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]
        """
        return self.txrx0("$")

    def h(self):
        """
        """

    def i(self):
        """
        [VER:1.1f.20170801:]
        [OPT:V,15,128]
        ok
        """
        return self.txrxs("$I")

    def info(self):
        ver, opt = self.i()
        return ver, opt

    def j(self, command):
        """
        $J=G90 X0.0 Y0.0 F1
        """
        self.txrx0("$J=" + command)

    def n(self):
        """
        $N
        $N0=
        $N1=
        """
        return self.txrxs("$N")


class GRBL:

    def __init__(self):
        self.gs = GRBLSer()
        pass

    def qstatus(self):
        """
        Idle|MPos:8.000,0.000,0.000|FS:0,0|WCO:0.000,0.000,0.000
        Idle|MPos:8.000,0.000,0.000|FS:0,0|Ov:100,100,100
        Idle|MPos:8.000,0.000,0.000|FS:0,0
        """
        raw = self.gs.question()
        parts = raw.split("|")
        # FIXME: extra
        ij, mpos, fs = parts[0:3]
        return {
            # Idle, Jog
            "status": ij,
            "MPos": mpos,
            "FS": fs,
        }

    def move_abs(self, x, f, blocking=True):
        # implies G1
        self.gs.j("G90 X%.1f F%u" % (x, f))
        if blocking:
            while self.qstatus()["status"] != "Idle":
                time.sleep(0.1)

    def move_rel(self, x, f, blocking=True):
        # implies G1
        self.gs.j("G91 X%.1f F%u" % (x, f))
        if blocking:
            while self.qstatus()["status"] != "Idle":
                time.sleep(0.1)


if 0:
    gs = GRBLSer()
    """
    grbl.tx("$")
    while True:
        rx = grbl.readline()
        if not rx:
            continue
        print(rx)
    """
    print(gs.help())
    print(gs.question())
    gs.j("G91 X+2.0 F1000")
    print(gs.question())
    gs.j("G91 X-2.0 F1000")
    print(gs.question())
    time.sleep(1)
    print(gs.question())

if 1:
    grbl = GRBL()
    print("move 1")
    grbl.move_abs(x=0.0, f=1000.0)
    print("move 2")
    grbl.move_rel(x=2.0, f=1000.0)
    print("move 3")
    grbl.move_rel(x=-2.0, f=1000.0)
    print("Done")
