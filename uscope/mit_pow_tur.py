#!/usr/bin/env bash
"""
Mitutoyo power turret test
Connect with null modem cable
"""

import serial
import time
import platform
import glob
from uscope.util import add_bool_arg

def default_port():
    if platform.system() == "Linux":
        acms = glob.glob('/dev/ttyUSB*')
        if len(acms) == 0:
            return None
        return acms[0]
    else:
        return None

class BadCommand(Exception):
    pass

def tobytes(buff):
    if type(buff) is str:
        #return bytearray(buff, 'ascii')
        return bytearray([ord(c) for c in buff])
    elif type(buff) is bytearray or type(buff) is bytes:
        return buff
    else:
        assert 0, type(buff)


def tostr(buff):
    if type(buff) is str:
        return buff
    elif type(buff) is bytearray or type(buff) is bytes:
        return ''.join([chr(b) for b in buff])
    else:
        assert 0, type(buff)

class MitutoyoPower:
    def __init__(self, device=None, verbose=None):
        if device is None:
            device = default_port()
            if device is None:
                raise Exception("Failed to find serial port")
        # verbose = True
        self.verbose = verbose
        self.verbose and print("port: %s" % device)
        """
        Baud: 1200, 2400, 4800, or 9600
        Parity: yes/no + even/odd
        """
        self.ser = serial.Serial(device,
                baudrate=9600,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_EVEN,
                stopbits=serial.STOPBITS_ONE,
                rtscts=False,
                dsrdtr=False,
                xonxoff=False,
                timeout=0.5,
                # Blocking writes
                writeTimeout=None)

        if 0:
            self.ser.write('\r\n')
            self.ser.flush()
            self.flushInput()

    def flushInput(self):
        # Try to get rid of previous command in progress, if any
        tlast = time.time()
        while time.time() - tlast < 0.1:
            buf = self.ser.read(1024)
            if buf:
                tlast = time.time()

        self.ser.flushInput()

    def control_check_error(self, reply):
        if reply == "ROK":
            return
        if reply == "":
            raise Exception("Failed to reply :(")
        # RNGXX
        print("reply", reply)
        assert 0, 'fixme'

    def cmd(self, cmd, reply=True):
        '''Send raw command and get string result'''

        strout = cmd + "\r\n"
        self.verbose and print("cmd out: %s" % strout.strip())
        strout = tobytes(strout)
        self.ser.flushInput()
        self.ser.write(strout)
        if 0:
            for c in strout:
                self.ser.write(c)
        self.ser.flush()

        if not reply:
            return
        
        return tostr(self.ser.readline()).strip()


    def rotate_to(self, pos, wait_idle=True):
        assert pos in "ABCDE"
        self.control_check_error(self.cmd("RWRMV" + pos))
        if wait_idle:
            self.wait_idle()

    def stop(self, pos):
        self.control_check_error(self.cmd("RWRSTP"))

    def error_clear(self):
        self.control_check_error(self.cmd("RWRRST"))

    def read_info(self):
        ret = self.cmd("RRDSTU").strip()
        assert ret[0:3] == "ROK"
        ret = ret[3:]
        return {
            "status": ret[0],
            "roation_error": ret[1],
            "communication": ret[4],
            "connect_status": ret[5],
            "version": ret[6],
            "position": ret[7],
            }

    def wait_idle(self):
        """
        0:Ready status
        1:The revolver is rotating.
        """
        while self.read_info()["status"] != "0":
            time.sleep(0.05)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Mitutoyo power turret")
    parser.add_argument("--port")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    mp = MitutoyoPower()
    print("Clearning error...")
    mp.error_clear()
    if 1:
        print("To B...")
        mp.rotate_to("B")
        print("To C...")
        mp.rotate_to("C")
        print("To B...")
        mp.rotate_to("B")
        print("To C...")
        mp.rotate_to("C")

if __name__ == "__main__":
    main()
