"""
[HLP:$$ $# $G $I $N $x=val $Nx=line $J=line $SLP $C $X $H ~ ! ? ctrl-x]
ok

Case insensitive best I can tell
"""

from uscope.motion.hal import MotionHAL, format_t, AxisExceeded

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

    def __init__(self,
                 port="/dev/ttyUSB0",
                 ser_timeout=0.5,
                 reset=True,
                 verbose=False):
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
        if reset:
            # Reset which also checks communication
            self.reset()

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
        self.verbose and print("tx '%s'" % (out, ))
        if nl:
            out = out + '\r'
        out = out.encode('ascii')
        # util.hexdump(out)
        self.serial.write(out)
        self.serial.flush()

    def readline(self):
        return self.serial.readline().decode("ascii").strip()

    def txrxs(self, out, nl=True, trim_data=True):
        """
        Send a command and return array of lines before ok line
        """
        self.tx(out, nl=nl)
        ret = []
        while True:
            l = self.readline().strip()
            self.verbose and print("rx '%s'" % (l, ))
            if not l:
                raise Timeout()
            elif l == "ok":
                return ret
            elif l.find("error") == 0:
                raise Exception(l)
            else:
                if trim_data:
                    ret.append(trim_data_line(l))
                else:
                    ret.append(l)

    def txrx0(self, out, nl=True):
        """
        Send a command and expect nothing back before ok
        """
        ret = self.txrxs(out, nl=nl)
        assert len(ret) == 0

    def txrx(self, out, nl=True):
        """
        Send a command and expect one line back before ok
        """
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
        self.tx("\x18", nl=False)
        l = self.readline().strip()
        assert l == ""
        l = self.readline().strip()
        assert "Grbl" in l

    def dollar(self):
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
        return self.txrxs("$$", trim_data=False)

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
        self.verbose and print("rx '%s'" % (l, ))
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


    def cancel(self):
        # From Yusuf
        self.tx("\x85")


class GRBL:

    def __init__(self, verbose=False):
        self.gs = GRBLSer(verbose=verbose)

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
        mpos = (float(x) for x in mpos.split(":")[1].split(","))
        mpos = dict([(k, v) for k, v in zip("xyz", mpos)])
        return {
            # Idle, Jog
            "status": ij,
            "MPos": mpos,
            "FS": fs,
        }

    def move_absolute(self, moves, f, blocking=True):
        # implies G1
        ax_str = ''.join(
            [' %c%0.3f' % (k.upper(), v) for k, v in moves.items()])
        self.gs.j("G90 %s F%u" % (ax_str, f))
        if blocking:
            self.wait_idle()

    def move_relative(self, moves, f, blocking=True):
        # implies G1
        ax_str = ''.join(
            [' %c%0.3f' % (k.upper(), v) for k, v in moves.items()])
        self.gs.j("G91 %s F%u" % (ax_str, f))
        if blocking:
            self.wait_idle()

    def wait_idle(self):
        while self.qstatus()["status"] != "Idle":
            time.sleep(0.1)


class GrblHal(MotionHAL):

    def __init__(self, log=None, dry=False):
        self.verbose = 0
        self.feedrate = None
        self.grbl = GRBL()
        MotionHAL.__init__(self, log, dry)

    def axes(self):
        return {'x', 'y', 'z'}

    def sleep(self, sec, why):
        ts = format_t(sec)
        s = 'Sleep %s: %s' % (why, ts)
        self.log(s, 3)
        self.rt_sleep += sec
        if not self.dry:
            time.sleep(sec)

    def command(self, cmd):
        if self.dry:
            if self.verbose:
                self.log(cmd)
        else:
            self._command(cmd)
            self.mv_lastt = time.time()

    def _command(self, cmd):
        raise Exception("Required")

    def move_absolute(self, moves, limit=True):
        if len(moves) == 0:
            return
        if limit:
            limit = self.limit()
            for k, v in moves.items():
                if v < limit[k][0] or v > limit[k][1]:
                    raise AxisExceeded("Axis %c to %s exceeds liimt (%s, %s)" %
                                       (k, v, limit[k][0], limit[k][1]))

        if self.dry:
            for k, v in moves.items():
                self._dry_pos[k] = v
        self.grbl.move_absolute(moves, f=1000)

    def move_relative(self, moves):
        if len(moves) == 0:
            return

        limit = self.limit()
        pos = self.pos()
        for k, v in moves.items():
            dst = pos[k] + v
            if dst < limit[k][0] or dst > limit[k][1]:
                raise AxisExceeded(
                    "Axis %c to %s (%s + %s) exceeds liimt (%s, %s)" %
                    (k, dst, pos[k], v, limit[k][0], limit[k][1]))

        if self.dry:
            for k, v in moves.items():
                self._dry_pos[k] += v
        self.grbl.move_relative(moves, f=1000)

    def pos(self):
        return self.grbl.qstatus()["MPos"]
