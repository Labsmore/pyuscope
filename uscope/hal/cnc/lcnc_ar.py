'''
Linux CNC auto RPC
'''

from .lcnc import LcncPyHal
from uscope.lcnc.client import LCNCRPC, PORT
from uscope import paramiko_util

import os
try:
    import paramiko
except:
    # quick workaround
    print("FIXME WARNING: paramiko import failed")
    paramiko = None
import select
import sys
import threading
import time
import subprocess

DEVNULL = open(os.devnull, 'wb')
RSH_PORT = 5007

# http://stackoverflow.com/questions/4409502/directory-transfers-on-paramiko


class LcncPyHalAr(LcncPyHal):
    def __init__(self, *args, **kwargs):
        try:
            self._init(*args, **kwargs)
        except:
            print('Shutting down on init failure')
            self.ar_stop()
            raise

    def update_server(self):
        print('Remote agent: updating')
        self.sftp = self.ssh.open_sftp()
        #sftp.rmdir(remote_tmp)
        # TODO: will probably throw exception if it exists
        try:
            self.sftp.mkdir(self.remote_tmp)
        except IOError:
            print("Temp directory already exists")
        # Copy over remote agent
        src = os.path.join(os.path.dirname(__file__), '..', 'lcnc',
                           'server.py')
        self.sftp.put(src, self.remote_server_py)

    def lcnc_launch(self, local_ini=None):
        print('linuxcnc: launching')

        if local_ini:
            remote_ini = os.path.join(self.remote_tmp, 'config',
                                      os.path.basename(self.local_ini))
            if self.sftp is None:
                self.sftp = self.ssh.open_sftp()
            paramiko_util.sftp_putdir(self.sftp,
                                      os.path.dirname(self.local_ini),
                                      os.path.join(self.remote_tmp, 'config'))
        elif remote_ini is None:
            remote_ini = '/home/machinekit/machinekit/configs/default/default.ini'

        transport = self.ssh.get_transport()
        self.linuxcnc_channel = transport.open_session()
        # http://stackoverflow.com/questions/7734679/paramiko-and-exec-command-killing-remote-process
        # Makes sure process dies when we close connection
        self.linuxcnc_channel.get_pty()
        #self.linuxcnc_stdin, self.linuxcnc_stdout, self.linuxcnc_stderr = self.ssh.exec_command('linuxcnc %s' % ini)
        if 0:
            self.linuxcnc_channel.exec_command(
                'screen -t linuxcnc linuxcnc %s' % remote_ini)
        else:
            self.linuxcnc_channel.exec_command('linuxcnc %s' % remote_ini)
        self.thread_linuxcnc = threading.Thread(target=self.run_linuxcnc)
        self.thread_linuxcnc.start()
        #time.sleep(5)
        # Although unused is a good indicator
        # note: if you are using axis gui this will freeze
        # need to poke around and see how we can do better
        self.wait_remote_port(RSH_PORT)

    def server_launch(self):
        print('Remote agent: launching ')
        transport = self.ssh.get_transport()
        self.server_channel = transport.open_session()
        # http://stackoverflow.com/questions/7734679/paramiko-and-exec-command-killing-remote-process
        # Makes sure process dies when we close connection
        self.server_channel.get_pty()
        #self.server_stdin, self.server_stdout, self.server_stderr = self.ssh.exec_command('python %s' % self.remote_server_py)
        if 0:
            self.server_channel.exec_command('screen -t lcnc_ar python %s' %
                                             self.remote_server_py)
        else:
            self.server_channel.exec_command('python %s' %
                                             self.remote_server_py)
        self.thread_server = threading.Thread(target=self.run_server)
        self.thread_server.start()
        self.wait_remote_port(PORT)

    # Machine configuration directory is '/home/machinekit/machinekit/configs/ARM.BeagleBone.CRAMPS'
    # Machine configuration file is 'simplified-rsh.ini'
    def _init(self,
              host,
              local_ini=None,
              remote_ini=None,
              log=None,
              dry=False,
              username='machinekit',
              update_server=False,
              launch_lcnc=False,
              launch_server=False):
        self.running = True
        self.tunnel = None
        self.verbose = 0

        print('Creating SSH connection')
        self.ssh = paramiko.SSHClient()
        self.ssh.load_system_host_keys()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(hostname=host,
                         port=22,
                         username=username,
                         key_filename=None,
                         look_for_keys=True,
                         password=None)

        self.remote_tmp = '/tmp/lcnc_ar'
        self.remote_server_py = os.path.join(self.remote_tmp, 'server.py')

        self.sftp = None
        if update_server:
            self.update_server()

        port_up = self.remote_port_up(PORT)

        if port_up:
            print('linuxcnc: assuming running since RPC agent running ')
        elif self.remote_port_up(RSH_PORT):
            print('linuxcnc: already running')
        else:
            # In practice I never want this anymore
            # Complain loudly if I trigger this code path
            if not launch_lcnc:
                raise Exception("LCNC not running")
            self.lcnc_launch(local_ini)

        if port_up:
            print('Remote agent: appears to be already running')
        else:
            # In practice I never want this anymore
            # Complain loudly if I trigger this code path
            if not launch_server:
                raise Exception("Server not running")
            self.server_launch()

        if self.local_port_up(PORT):
            print('SSH tunnel: alrady running')
        else:
            print('SSH tunnel: creating')
            self.thread_tunnel = threading.Thread(target=self.run_tunnel)
            self.thread_tunnel.start()
        self.wait_local_port(PORT)

        linuxcnc = LCNCRPC('localhost')
        LcncPyHal.__init__(self, linuxcnc=linuxcnc, log=log, dry=dry)

    def local_port_up(self, port):
        rc = subprocess.call('exec 6<>/dev/tcp/127.0.0.1/%s' % port,
                             shell=True,
                             stdout=DEVNULL,
                             stderr=DEVNULL,
                             executable='/bin/bash')
        return rc == 0

    def wait_local_port(self, port):
        print('Checking local port %d' % port)
        while not self.local_port_up(port):
            time.sleep(0.1)
        print('port open')

    def remote_port_up(self, port):
        channel = self.ssh.get_transport().open_session()
        # exec 6<>/dev/tcp/127.0.0.1/22617
        channel.exec_command('exec 6<>/dev/tcp/127.0.0.1/%s' % port)
        rc = channel.recv_exit_status()
        return rc == 0

    def wait_remote_port(self, port):
        print('Checking remote port %d' % port)
        while not self.remote_port_up(port):
            time.sleep(0.1)
        print('port is open')

    def __del__(self):
        self.ar_stop()

    def ar_stop(self):
        print('shutting down')
        self.running = False
        if self.tunnel:
            # ForwardServer
            self.tunnel.shutdown()
        self.ssh.close()

    def run_linuxcnc(self):
        '''
        while self.running:
            for f in [self.linuxcnc_stdout, self.linuxcnc_stderr]:
                sys.stdout.write(f.read())
                sys.stdout.flush()
                time.sleep(0.1)
        '''
        while self.running:
            [rdy_r, _rdy_w, _rdy_x] = select.select([self.linuxcnc_channel],
                                                    [], [])
            if len(rdy_r):
                # TODO: consider log file instead
                sys.stdout.write(self.linuxcnc_channel.recv(1024))
                sys.stdout.flush()

        print('linuxcnc thread exiting')

    def run_server(self):
        '''
        # AttributeError: 'ChannelFile' object has no attribute 'fileno'
        fds = {
            self.stdout.fileno(): self.stdout,
            self.stderr.fileno(): self.stderr,
        }
        
        while self.running:
            [rdy_r, _rdy_w, _rdy_x] = select.select([fds.keys(), [], []])
            for fd in rdy_r:
                # TODO: consider log file instead
                sys.stdout.write(fds[fd].read())
                sys.stdout.flush()
        '''

        while self.running:
            [rdy_r, _rdy_w, _rdy_x] = select.select([self.server_channel], [],
                                                    [])
            if len(rdy_r):
                # TODO: consider log file instead
                sys.stdout.write(self.server_channel.recv(1024))
                sys.stdout.flush()

        # http://sebastiandahlgren.se/2012/10/11/using-paramiko-to-send-ssh-commands/
        # weird they didn't conform to the file api at all with fds
        '''
        while self.running:
            for f in [self.server_stdout, self.server_stderr]:
                sys.stdout.write(f.read())
                sys.stdout.flush()
                time.sleep(0.1)
        '''

        print('Server thread exiting')

    def run_tunnel(self):
        '''
        Because we're using xmlrpc it generates a lot of socket connections
        if you leave verbose on you'll get spammed with messages like this
        
        Connected!  Tunnel open ('127.0.0.1', 55469) -> ('192.168.2.55', 22) -> ('127.0.0.1', 22617)
        Tunnel closed from ('127.0.0.1', 55469)
        
        Is this socket deluge okay or should I look into something more connection oriented?
        '''

        print('Preparing tunnel')
        self.tunnel = paramiko_util.forward_tunnel(
            local_port=PORT,
            remote_host='127.0.0.1',
            remote_port=PORT,
            transport=self.ssh.get_transport())
        print('Serving tunnel')
        self.tunnel.serve_forever()
        print('Tunnel thread exiting')
