# Not very enthusiastic about this bit of code but it does seem to work
# Why isn't this functionality part of the paramiko library?
# maybe should look at paramiko harder

try:
    import SocketServer
except ImportError:
    import socketserver as SocketServer
import select
import os

class ForwardServer (SocketServer.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

class Handler (SocketServer.BaseRequestHandler):
    def __init__(self, *args, **kwargs):
        self.verbose = False
        SocketServer.BaseRequestHandler.__init__(self, *args, **kwargs)
    
    def _verbose(self, s):
        if self.verbose:
            print(s)

    def handle(self):
        try:
            chan = self.ssh_transport.open_channel('direct-tcpip',
                                                   (self.chain_host, self.chain_port),
                                                   self.request.getpeername())
        except Exception as e:
            self._verbose('Incoming request to %s:%d failed: %s' % (self.chain_host,
                                                              self.chain_port,
                                                              repr(e)))
            return
        if chan is None:
            self._verbose('Incoming request to %s:%d was rejected by the SSH server.' %
                    (self.chain_host, self.chain_port))
            return

        self._verbose('Connected!  Tunnel open %r -> %r -> %r' % (self.request.getpeername(),
                                                            chan.getpeername(), (self.chain_host, self.chain_port)))
        while True:
            r, w, x = select.select([self.request, chan], [], [])
            if self.request in r:
                data = self.request.recv(1024)
                if len(data) == 0:
                    break
                chan.send(data)
            if chan in r:
                data = chan.recv(1024)
                if len(data) == 0:
                    break
                self.request.send(data)
                
        peername = self.request.getpeername()
        chan.close()
        self.request.close()
        self._verbose('Tunnel closed from %r' % (peername,))

def forward_tunnel(local_port, remote_host, remote_port, transport):
    # this is a little convoluted, but lets me configure things for the Handler
    # object.  (SocketServer doesn't give Handlers any way to access the outer
    # server normally.)
    class SubHander (Handler):
        chain_host = remote_host
        chain_port = remote_port
        ssh_transport = transport
    #ForwardServer(('', local_port), SubHander).serve_forever()
    return ForwardServer(('', local_port), SubHander)

def normalize_dirpath(dirpath):
    while dirpath.endswith("/"):
        dirpath = dirpath[:-1]
    return dirpath

def sftp_mkdir(sftp, remotepath, mode=0777, intermediate=False):
    remotepath = normalize_dirpath(remotepath)
    if intermediate:
        try:
            sftp.mkdir(remotepath, mode=mode)
        except IOError, e:
            sftp_mkdir(sftp, remotepath.rsplit("/", 1)[0], mode=mode,
                       intermediate=True)
            return sftp.mkdir(remotepath, mode=mode)
    else:
        sftp.mkdir(remotepath, mode=mode)

def sftp_putdir(sftp, localpath, remotepath, preserve_perm=True):
    if not remotepath.startswith("/"):
        raise ValueError("%s must be absolute path" % remotepath)
            
    # normalize
    localpath = normalize_dirpath(localpath)
    remotepath = normalize_dirpath(remotepath)

    try:
        sftp.chdir(remotepath)
        localsuffix = localpath.rsplit("/", 1)[1]
        remotesuffix = remotepath.rsplit("/", 1)[1]
        if localsuffix != remotesuffix:
            remotepath = os.path.join(remotepath, localsuffix)
    except IOError, e:
        pass

    for root, dirs, fls in os.walk(localpath):
        prefix = os.path.commonprefix([localpath, root])
        suffix = root.split(prefix, 1)[1]
        if suffix.startswith("/"):
            suffix = suffix[1:]

        remroot = os.path.join(remotepath, suffix)

        try:
            sftp.chdir(remroot)
        except IOError, e:
            if preserve_perm:
                mode = os.stat(root).st_mode & 0777
            else:
                mode = 0777
            sftp_mkdir(sftp, remroot, mode=mode, intermediate=True)
            sftp.chdir(remroot)
        for f in fls:
            remfile = os.path.join(remroot, f)
            localfile = os.path.join(root, f)
            sftp.put(localfile, remfile)
            if preserve_perm:
                sftp.chmod(remfile, os.stat(localfile).st_mode & 0777)
