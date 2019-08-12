'''
http://wiki.linuxcnc.org/cgi-bin/wiki.pl?Emcrsh

hello EMC x 1.0
set enable EMCTOO
set verbose on
set machine on
set home 0
set home 1
set home 2
set home 3
set home 4
set home 5
set mode mdi


hello EMC x 1.0
set enable EMCTOO
set verbose on
set machine on
set mode mdi
'''

import pexpect.fdpexpect
import telnetlib
import time

class Rsh(object):
    def __init__(self, host='localhost', port=5007, password=None, enable=True, machine=True, mdi=True):
        self.telsh = telnetlib.Telnet('mk-xray', 5007, timeout=0.5)
        self.client = pexpect.fdpexpect.fdspawn(self.telsh)
        self.auth(password)
        # Required for class to operate correctly
        self.set_echo(False)
        self.set_verbose(True)
        
        if enable:
            self.enable(True)
        if machine:
            self.set_machine(True)
        if mdi:
            self.set_mode('MDI')

    def auth(self, password=None):
        if password is None:
            password = 'EMC'
        self.client.sendline('HELLO %s x 1.0' % password)
        self.client.expect('HELLO ACK i 1.1')

    def set_echo(self, yn):
        if yn != False:
            raise Exception("FIXME")
        self.client.sendline('SET ECHO OFF')
        self.client.sendline('GET ECHO')
        self.client.expect('ECHO OFF')

    def set_verbose(self, yn):
        if yn != True:
            raise Exception("FIXME")

        self.client.sendline('SET VERBOSE ON')
        # Creates these ACK messages to show up in addition to NACK
        self.client.expect('SET VERBOSE ACK')

    def enable(self, yn):
        if yn != True:
            raise Exception("FIXME")

        self.client.sendline('SET ENABLE EMCTOO')
        self.client.expect('SET ENABLE ACK')

    def set_machine(self, yn):
        if yn != True:
            raise Exception("FIXME")
            
        self.client.sendline('SET MACHINE ON')
        self.client.expect('SET MACHINE ACK')

    def set_home(self, axis):
        self.client.sendline('SET HOME %d' % axis)
        self.client.expect('SET HOME ACK')

    def set_mode(self, mode):
        if mode != 'MDI':
            raise Exception('FIXME')
        self.client.sendline('SET MODE MDI')
        self.client.expect('SET MODE ACK')

    def mdi(self, cmd, timeout=0):
        if timeout != 0:
            raise Exception('FIXME')
        # for large commands
        # USRMOT: ERROR: invalid command
        # but doesn't return error
        self.client.sendline('SET MDI %s' % cmd)
        self.client.expect('SET MDI ACK')
        
        if timeout is not None:
            self.timeout(timeout)
    
    def timeout(self, timeout):
        if timeout != 0:
            raise Exception('FIXME')
        
        while True:
            self.client.sendline('GET PROGRAM_STATUS')
            status = self.client.readline().strip()
            if status == 'PROGRAM_STATUS RUNNING':
                time.sleep(0.05)
                continue
            elif status == 'PROGRAM_STATUS IDLE':
                break
            else:
                raise Exception('bad status %s' % status)
