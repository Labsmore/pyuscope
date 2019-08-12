'''
WARNING: this file is deployed standalone to remote systems
Do not add uvscada dependencies
'''

from SimpleXMLRPCServer import SimpleXMLRPCServer
import linuxcnc

class Server(object):
    def __init__(self, bind='localhost', port=22617, verbose=False):
        self.server = None
        self.bind = bind
        self.port = port
        self.verbose = verbose
        
        self.s = linuxcnc.stat()
        self.c = linuxcnc.command()

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
        print 'mdi'
        print args, kwargs
        ret = self.c.mdi(*args, **kwargs)
        print ret
    
    def run(self):
        print 'Starting server'
        self.server = SimpleXMLRPCServer((self.bind, self.port), logRequests=self.verbose, allow_none=True)
        self.server.register_introspection_functions()
        self.server.register_multicall_functions()
        self.server.register_instance(self)
        self.server.register_function(self.c.mode,          "c_mode")
        self.server.register_function(self.c.wait_complete, "c_wait_complete")
        #self.server.register_function(self.c.mdi,           "c_mdi")
        self.server.register_function(self.c_mdi,           "c_mdi")
        self.server.register_function(self.s.state,         "s_state")
        self.server.register_function(self.c.state,         "c_state")
        self.server.register_function(self.c.home,          "c_home")
        print 'Running'
        self.server.serve_forever()

if __name__ == '__main__':
    s = Server()
    s.run()