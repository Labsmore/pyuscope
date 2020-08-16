from .hal import Hal, format_t, AxisExceeded

import time


# Camera always local
class LcncHal(Hal):
    def __init__(self, log=None, dry=False):
        self.verbose = 0
        self.feedrate = None
        Hal.__init__(self, log, dry)

    def sleep(self, sec, why):
        ts = format_t(sec)
        s = 'Sleep %s: %s' % (why, ts)
        self.log(s, 3)
        self.rt_sleep += sec
        if not self.dry:
            time.sleep(sec)

    def cmd(self, cmd):
        if self.dry:
            if self.verbose:
                self.log(cmd)
        else:
            self._cmd(cmd)
            self.mv_lastt = time.time()

    def _cmd(self, cmd):
        raise Exception("Required")

    def mv_abs(self, pos, limit=True):
        if len(pos) == 0:
            return
        if limit:
            limit = self.limit()
            for k, v in pos.items():
                if v < limit[k][0] or v > limit[k][1]:
                    raise AxisExceeded("Axis %c to %s exceeds liimt (%s, %s)" %
                                       (k, v, limit[k][0], limit[k][1]))

        if self.dry:
            for k, v in pos.items():
                self._dry_pos[k] = v
        self.cmd('G90 ' + self.g_feed() +
                 ''.join([' %c%0.3f' % (k.upper(), v)
                          for k, v in pos.items()]))

    def mv_rel(self, delta):
        if len(delta) == 0:
            return

        limit = self.limit()
        pos = self.pos()
        for k, v in delta.items():
            dst = pos[k] + v
            if dst < limit[k][0] or dst > limit[k][1]:
                raise AxisExceeded(
                    "Axis %c to %s (%s + %s) exceeds liimt (%s, %s)" %
                    (k, dst, pos[k], v, limit[k][0], limit[k][1]))

        if self.dry:
            for k, v in delta.items():
                self._dry_pos[k] += v
        # Unlike DIY controllers, all axes can be moved concurrently
        # Don't waste time moving them individually
        self.cmd(
            'G91 ' + self.g_feed() +
            ''.join([' %c%0.3f' % (k.upper(), v) for k, v in delta.items()]))

    def g_feed(self):
        if self.feedrate is None:
            return 'G0'
        else:
            return 'G1 F%0.3f' % self.feedrate


# http://linuxcnc.org/docs/html/common/python-interface.html
# LinuxCNC python connection
# Currently the rpc version emulates stat and command channels
# making these identical for the time being
class LcncPyHal(LcncHal):
    def __init__(self, linuxcnc, log=None, dry=False):
        self.ax_c2i = {'x': 0, 'y': 1}
        self.ax_i2c = {0: 'x', 1: 'y'}

        self.linuxcnc = linuxcnc
        self.stat = self.linuxcnc.stat()
        self.command = self.linuxcnc.command()
        '''
        breaks homing?
        if not dry:
            self.command.state(self.linuxcnc.STATE_ON)
        '''
        self.stat.poll()
        if self.verbose:
            print('Enabled: %s' % self.stat.enabled)

        # Do this explicitly: in many setups I'm already homed
        # prevent "can't do that (EMC_AXIS_HOME:123) in MDI mode"
        # You must home all axes, not just those used
        '''
        if not dry:
            self.command.mode(self.linuxcnc.MODE_MANUAL)
            for axisi in xrange(self.stat.axes):
                self._home(axisi=axisi, lazy=True)
            self.command.mode(self.linuxcnc.MODE_MDI)
        '''

        self.stat.poll()
        if self.verbose:
            print('Enabled: %s' % self.stat.enabled)

        self._limit = {}
        for axisc in self.axes():
            axis = self.stat.axis[self.ax_c2i[axisc]]
            self._limit[axisc] = (axis['min_position_limit'],
                                  axis['max_position_limit'])

        LcncHal.__init__(self, log=log, dry=dry)

    def home(self, axes=None):
        if axes is None:
            axes = self.axes()
        self.command.mode(self.linuxcnc.MODE_MANUAL)
        for axis in axes:
            self._home(axis)
        self.command.mode(self.linuxcnc.MODE_MDI)

    def _home(self, axisc=None, axisi=None, lazy=False):
        if axisi is None:
            axisi = self.ax_c2i[axisc]

        if self.verbose:
            print('Home: check axis %d' % axisi)
        self.stat.poll()
        if self.verbose:
            print('Enabled: %s' % self.stat.enabled)
        axis = self.stat.axis[axisi]
        #print axis
        if lazy and axis['homed']:
            if self.verbose:
                print('  Already homed')
            return
        # prevent "homing already in progress"
        if not axis['homing']:
            tstart = time.time()
            self.command.home(axisi)
        if self.verbose:
            print('  Waiting for home...')
        while axis['homing']:
            self.stat.poll()
            time.sleep(0.1)
        if self.verbose:
            print('  homed after %0.1f' % (time.time() - tstart, ))

    def ok_for_mdi(self):
        self.stat.poll()
        return not self.stat.estop and self.stat.enabled and self.stat.homed and self.stat.interp_state == self.linuxcnc.INTERP_IDLE

    def wait_mdi_idle(self):
        if self.dry:
            return
        while not self.ok_for_mdi():
            # TODO: notify self.progress
            #print self.stat.estop, self.stat.enabled, self.stat.homed, self.stat.interp_state, self.linuxcnc.INTERP_IDLE
            if self.verbose:
                print(
                    'Pos: commanded %d actual %s' %
                    (self.stat.axis[0]['input'], self.stat.axis[0]['output']))
            time.sleep(0.1)

    def _cmd(self, cmd):
        if self.verbose:
            print()
            print()
            print(cmd)
            print('waiting mdi idle (entry)')
        self.wait_mdi_idle()
        if self.verbose:
            print('executing command')
        # Doesn't seem to hurt perf notably and reduces a lot of errors
        self.command.mode(self.linuxcnc.MODE_MDI)
        self.command.mdi(cmd)
        if self.verbose:
            print('waiting mdi idle (exit)')
        self.wait_mdi_idle()
        if self.verbose:
            print('command done')

    def forever(self, axes, run, progress):
        if self.verbose:
            print('forever')
        while run.is_set():
            # Axes may be updated
            # Copy it so that don't crash if its updated during an iteration
            for axis, sign in dict(axes).items():
                self.mv_rel({axis: sign * 0.05})
                pos = self.pos()
                if self.verbose:
                    print('emitting progress: %s' % str(pos))
                progress(pos)
            time.sleep(0.1)

    # FIXME
    # Trim down HAL so that reported axes is correct
    def axes(self):
        return list(self.ax_c2i.keys())

    def pos(self):
        if self.dry:
            return self._dry_pos
        self.stat.poll()
        ret = {}
        for axis in self.axes():
            ret[axis] = self.stat.axis[ord(axis) - ord('x')]['output']
        return ret

    def limit(self, axes=None):
        return self._limit

    def begin(self):
        self.command.mode(self.linuxcnc.MODE_MDI)
        LcncHal.begin(self)


# LinuxCNC remote connection
class LcncRshHal(LcncHal):
    def __init__(self, rsh, log=None, dry=False):
        LcncHal.__init__(self, log=log, dry=dry)
        self.rsh = rsh

    def _cmd(self, cmd):
        # Waits for completion before returning
        self.rsh.mdi(cmd, timeout=0)
