import serial
import sys
import time
import struct
#import bytearray
import binascii
import argparse
from collections import namedtuple


class Timeout(Exception):
    pass


class AckException(Exception):
    pass


class BadChecksum(Exception):
    pass


# 0xC0
SLIP_END = chr(192)
# 0xDB
SLIP_ESC = chr(219)

REG_NOP = 0x03
REG_ACK = 0x02

XYZ_STATUS = 0x00
XYZ_CONTROL = 0x01
# Set the step register to value (2's compliment)
XYZ_STEP_SET = 0x02
# Adjust the step register by argument
XYZ_STEP_ADD = 0x03
# Adjust the step register by argument
# #define XYZ_STEP_ADD    0x04
# Minimum velocity in steps/second
XYZ_VELMIN = 0x05
# Maximum velocity in steps/second
XYZ_VELMAX = 0x06
# Acceleration/decceleration in steps/second**2
XYZ_ACL = 0x07
XYZ_HSTEP_C = 0x08
XYZ_NET_STEP = 0x09
#XYZ_USTEP           0x0A
XYZ_BASE = {'X': 0x20, 'Y': 0x40, 'Z': 0x60}
'''
uint8_t checksum;
uint8_t seq;
uint8_t opcode;
uint32_t value;
'''
PACKET_FORMAT = '<BBBi'
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)
Packet = namedtuple('pr0ndexerPacket', ('checksum', 'seq', 'opcode', 'value'))


def checksum(data):
    data = str(data)
    return (~(sum([ord(c) for c in data]) % 0x100)) & 0xFF


def slip(bytes):
    ret = SLIP_END
    for b in bytes:
        if b == SLIP_END:
            # If a data byte is the same code as END character, a two byte sequence of
            # ESC and octal 334 (decimal 220) is sent instead.
            ret += SLIP_ESC + chr(220)
        elif b == SLIP_ESC:
            # If it the same as an ESC character, an two byte sequence of ESC and octal 335 (decimal
            # 221) is sent instead
            ret += SLIP_ESC + chr(221)
        else:
            ret += b
    # When the last byte in the packet has been
    # sent, an END character is then transmitted
    return ret + SLIP_END


def deslip(bytes):
    '''Returns None if slip decoding failed'''
    escape = False
    rx = ''
    i = 0

    def slip_dbg(s):
        #print s
        pass

    while i < len(bytes):
        c = chr(bytes[i])
        i += 1
        slip_dbg('')
        slip_dbg('Processing: %02X' % ord(c))

        if escape:
            slip_dbg('Escape followed')
            escape = False

            # If a data byte is the same code as END character, a two byte sequence of
            # ESC and octal 334 (decimal 220) is sent instead.
            if c == chr(220):
                rx += SLIP_END
            # If it the same as an ESC character, an two byte sequence of ESC and octal 335 (decimal
            # 221) is sent instead
            elif c == chr(221):
                rx += SLIP_ESC
            else:
                slip_dbg('Escape invalid')
                del bytes[0:i]
                rx = ''
                i = 0
                continue
        elif c == SLIP_END:
            del bytes[0:i]
            # Not the right size? drop it
            if len(rx) == PACKET_SIZE:
                slip_dbg('Good packet')
                return rx
            slip_dbg('Dropping packet: bad size')
            rx = ''
            i = 0
            continue
        elif c == SLIP_ESC:
            slip_dbg('Escape detected')
            escape = True
        # Ordinary character
        else:
            slip_dbg('Normal char')
            rx += c
    return None


#def deslip(bytes):


class Indexer:
    # wtf is acm
    def __init__(self, device=None, debug=False, log=None):
        if log is None:

            def log(s):
                print(s)

        self._log = log
        self.serial = None
        self.debug = debug
        self.wait_ack = True

        # Turns out both go in opposite direction I need
        self.invert_step = True

        self.seq = 0
        if device is None:
            for s in ("/dev/ttyUSB0", ):
                try:
                    self.try_open(s)
                    print('Opened %s okay' % s)
                    break
                except IOError:
                    print('Failed to open %s' % s)
                    continue
            if self.serial is None:
                raise IOError("Failed to find a suitable device")
        else:
            self.try_open(device)

        # Clear old data
        if self.debug:
            self._log('Flushing %d chars' % self.serial.inWaiting())
        self.serial.flushInput()

        # Send and make sure we get an ack
        self.reg_write(REG_NOP, 0)

    def configure(self, acl=None, velmin=None, velmax=None, hstep_c=None):
        '''
        WARNING: do not update parameters while motor is moving, it may create an acceleration discontinuity
        
        These appear as per axis but are actually currently global
        Defaults
        "acl': 325,
            acceleration, basically how many clock cycles to adjust each subsequent pulse
            more detailed: how much to adjust axis->velocity each step
        "velmin': 10,
            minimum pulse width loop value
        "velmax': 9250,
            maximum pulse width loop value
        "hstep_c': 740000
            scales velocity
            smaller values linearly creates smaller pulses => move motor faster
            from k2 code: hstep_c = 7.4e5
            
        
        actual pulse width: g_hstep_c / axis->velocity NOP high/low loops
        '''
        if acl:
            self.reg_write(0x20 + XYZ_ACL, acl)
        if velmin:
            self.reg_write(0x20 + XYZ_VELMIN, velmin)
        if velmax:
            self.reg_write(0x20 + XYZ_VELMAX, velmax)
        if hstep_c:
            self.reg_write(0x20 + XYZ_HSTEP_C, hstep_c)

    def try_open(self, device):
        self.device = device
        self.serial = serial.Serial(port=self.device,
                                    baudrate=38400,
                                    timeout=1,
                                    writeTimeout=1)
        if self.serial is None:
            raise IOError('Can not connect to serial')

    '''
    def recv(self):
        # Sync until first ~
        if self.debug:
            print 'pr0ndexer DEBUG: recv: waiting for opening ~'
        for _i in xrange(3):
            c = self.serial.read(1)
            if self.debug:
                print 'pr0ndexer DEBUG: recv open wait: got "%s", wait: %d' % (c, self.serial.inWaiting())
            if c == '~':
                break
        else:
            raise Timeout('Timed out waiting for opening ~')
        
        if self.debug:
            print 'pr0ndexer DEBUG: recv: waiting for closing ~'
        # Read until ~
        ret = ''
        for _i in xrange(60):
            c = self.serial.read(1)
            if c == '~':
                break
            ret += c
        else:
            raise Timeout('Timed out waiting for closing ~')
        
        if self.debug:
            print 'pr0ndexer DEBUG: recv: returning: "%s"' % (ret,)
        return ret
    '''

    def reg_write(self, reg, value):
        self.packet_write(0x80 | reg, value)

    # loose wire in serial adapter, should probably replace it
    # causing lots of checksum errors on rx side
    def reg_read(self, reg, retries=10):
        '''Return 32 bit register value'''
        for i in range(retries):
            try:
                self.serial.flushInput()
                self.packet_write(reg, 0)

                reply_packet = self.packet_read()
                if reply_packet.opcode != reg:
                    self._log(
                        "WARNING: Replied wrong reg.  Expected 0x%02X but got 0x%02X"
                        % (reg, reply_packet.opcode))
                    # try to flush out
                    time.sleep(0.1)
                    continue
                return reply_packet.value
            except BadChecksum as e:
                if i == retries - 1:
                    raise e
                self._log('WARNING: bad checksum on read: %s' % (e, ))
            except Timeout as e:
                if i == retries - 1:
                    raise e
                self._log('WARNING: timed out read')

    def packet_write(self, reg, value, retries=3):
        #print 'Packet write reg=0x%02X, value=0x%08X' % (reg, value)
        packet = struct.pack('<BBi', self.seq, reg, value)
        packet = chr(checksum(packet)) + packet
        out = slip(packet)
        self.seq = (self.seq + 1) % 0x100

        if self.debug:
            self._log('pr0ndexer DEBUG: packet: %s, sending: %s' %
                      (binascii.hexlify(packet), binascii.hexlify(out)))
            #if self.serial.inWaiting():
            #    raise Exception('At send %d chars waiting' % self.serial.inWaiting())
        for retry in range(retries):
            try:
                self.serial.flushInput()
                self.serial.write(out)
                self.serial.flush()

                if self.wait_ack:
                    try:
                        _ack_packet = self.packet_read()
                    except BadChecksum as e:
                        self._log('WARNING: bad rx checksum')
                        continue

                        # poorly assume if we get any response back that the write succeeded
                        # since we are throwing the reply away anyway
                        '''
                        if i == retries-1:
                            raise e
                        print 'WARNING: bad checksum on read: %s' % (e,)
                        continue
                        '''
                return
                '''
                    ack_packet = self.packet_read()
                    if ack_packet.seq != self.seq:
                        
                    if ack_packet.opcode != reg:
                        # Maybe due to previously sent packets?
                        raise Exception("Replied wrong reg.  Expected 0x%02X but got 0x%02X", reg, reply_packet.opcode)
                else:
                    raise AckException("Didn't get ack in %d packets" % retries)
                '''
            except Timeout:
                self._log('WARNING: retry %s timed out' % retry)
                continue
        raise Timeout('Failed to write packet after %d retries' % retries)

    def packet_read(self):
        # Now read response
        # Go until we either get a packet or serial port times out
        rx = bytearray()
        while True:
            while True:
                c = self.serial.read(1)
                if not c:
                    raise Timeout('Failed to read serial port')
                rx += c
                #print 'Read %s' % binascii.hexlify(rx)
                packet_raw = deslip(rx)
                if packet_raw:
                    break
            #print 'Got packet of length %d' % len(packet_raw)
            packet = Packet(*struct.unpack(PACKET_FORMAT, packet_raw))
            checksum_computed = checksum(packet_raw[1:])
            if packet.checksum != checksum_computed:
                self.serial.flushOutput()
                self.serial.flushInput()
                raise BadChecksum("Expected 0x%02X but got 0x%02X" %
                                  (packet.checksum, checksum_computed))
            return packet

    def step(self, axis, n, wait=True):
        if self.invert_step:
            n = -n

        if self.debug:
            self._log('pr0ndexer DEBUG: %s stepping %d' % (axis, n))

        self.reg_write(XYZ_BASE[axis] + XYZ_STEP_SET, n)
        if wait:
            wait_timeout = 11. * abs(n) / 371.0 + 1.
            self.wait_idle(axis, wait_timeout)

    def steps_a_second(self):
        # for delay register of 2000
        #return 370
        return 3700

    def step_rel(self, axis, n, wait=True, wait_timeout_rel=False):
        if self.invert_step:
            n = -n

        if self.debug:
            self._log('pr0ndexer DEBUG: %s stepping rel %d' % (axis, n))

        self.reg_write(XYZ_BASE[axis] + XYZ_STEP_ADD, n)
        if wait:
            # Because we don't know how many steps have already been done (without querying)
            # we can't make a reasonable wait timeout
            wait_timeout = None
            if wait_timeout_rel:
                wait_timeout = 11. * abs(n) / 371.0 + 1.
            self.wait_idle(axis, wait_timeout)

    def wait_idle(self, axis, wait_timeout):
        # some margin to make sure its done
        #time.sleep(abs(n) / 371.0 + 0.2)
        start = time.time()
        while True:
            waited = time.time() - start
            # Relative step but absolute steps remaining
            cur = self.reg_read(XYZ_BASE[axis] + XYZ_STEP_SET)
            # Done?
            if cur == 0:
                return
            if wait_timeout and waited > wait_timeout:
                raise Timeout(
                    "Couldn't reach desired step within desired time, last value %d",
                    cur)

    def net_tostep(self, axis):
        '''Returns number of steps executed + to execute'''
        ret = self.reg_read(XYZ_BASE[axis] + XYZ_NET_STEP)
        if self.invert_step:
            return -ret
        else:
            return ret


def str2bool(arg_value):
    arg_value = arg_value.lower()
    if arg_value == "false" or arg_value == "0" or arg_value == "no" or arg_value == "off":
        return False
    else:
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test utility to drive serial port")
    parser.add_argument('--debug',
                        action="store_true",
                        default=False,
                        help='Debug')
    parser.add_argument('port',
                        nargs='?',
                        default=None,
                        help='Serial port to open. Default: snoop around')
    args = parser.parse_args()

    indexer = Indexer(device=args.port, debug=args.debug)

    # default: 7.4e5
    hstep_c = 7.4e7
    print('Delay %d' % hstep_c)
    indexer.reg_write(0x20 + XYZ_HSTEP_C, hstep_c)
    indexer.step('X', 200)
