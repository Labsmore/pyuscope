#!/usr/bin/env python
'''
WARNING: this file is deployed standalone to remote systems
Do not add uvscada dependencies

WARNING: system only supports python2
'''

from SimpleXMLRPCServer import SimpleXMLRPCServer
import linuxcnc
import os
import socket
import signal
import sys

PID_FILE = "/tmp/pyuscope_server.pid"


def port_in_use(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ret = False
    ret = s.connect_ex(('localhost', port)) == 0
    s.close()
    return ret


def kill_existing(verbose=False):
    if not os.path.exists(PID_FILE):
        raise Exception("Port open  but no pid file")

    pid = int(open(PID_FILE, "r").read())
    if verbose:
        print("server: killing %u" % pid)
    os.kill(pid, 9)


sys_excepthook = sys.excepthook


def excepthook(excType, excValue, tracebackobj):
    print("removing")
    os.unlink(PID_FILE)
    sys_excepthook(excType, excValue, tracebackobj)


class Server(object):

    def __init__(self, bind='localhost', port=22617, verbose=False):
        self.server = None
        self.bind = bind
        self.port = port
        self.verbose = verbose

        self.s = linuxcnc.stat()
        self.c = linuxcnc.command()

        # might get collision w/ other pid? check port first
        if port_in_use(port):
            kill_existing(verbose=verbose)

        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))

        # doesn't seem to work
        # pid solution seems to be working well...ignore
        if 0:
            sys.excepthook = excepthook
            # Remove lock file on ^C
            signal.signal(signal.SIGINT, signal.SIG_DFL)

    def __del__(self):
        if self.verbose:
            print("Deleting PID file")
        os.unlink(PID_FILE)

    def s_poll(self):
        self.s.poll()
        ret = {}
        #for attr in ['axis', 'axes', 'estop', 'enabled', 'homed', 'interp_state']:
        #    ret[attr] = getattr(self.s, attr)
        # AttributeError: 'linuxcnc.stat' object has no attribute '__dict__'
        '''
        for k, v in self.s.__dict__.iteritems():
            if k.startswith('_'):
                continue
            if not type(v) in [int, str]:
                continue
        '''
        for k in ['axis', 'axes', 'estop', 'enabled', 'homed', 'interp_state']:
            ret[k] = getattr(self.s, k)
        # dict
        ret['axis'] = self.s.axis
        return ret

    def constants(self):
        ret = {}
        for k, v in linuxcnc.__dict__.iteritems():
            if k.startswith('_'):
                continue
            if not type(v) in [int, str]:
                continue
            ret[k] = v
        return ret

    def c_mdi(self, *args, **kwargs):
        print('mdi', args, kwargs)
        ret = self.c.mdi(*args, **kwargs)
        print('mdi ret', ret)

    def run(self):
        print('Starting server')
        self.server = SimpleXMLRPCServer((self.bind, self.port),
                                         logRequests=self.verbose,
                                         allow_none=True)
        self.server.register_introspection_functions()
        self.server.register_multicall_functions()
        self.server.register_instance(self)
        self.server.register_function(self.c.mode, "c_mode")
        self.server.register_function(self.c.wait_complete, "c_wait_complete")
        #self.server.register_function(self.c.mdi,           "c_mdi")
        self.server.register_function(self.c_mdi, "c_mdi")
        self.server.register_function(self.s.state, "s_state")
        self.server.register_function(self.c.state, "c_state")
        self.server.register_function(self.c.home, "c_home")
        print('Running')
        self.server.serve_forever()


if __name__ == '__main__':
    s = Server()
    s.run()
