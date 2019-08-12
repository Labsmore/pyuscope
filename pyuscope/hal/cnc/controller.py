'''
This file is part of uvscada
Licensed under 2 clause BSD license, see COPYING for details
'''

# no real interfaces really defined yet...
class Controller:
    def __init__(self, debug=False, log=None):
        if log is None:
            def log(s):
                print s
        self.log = log
        
        self.debug = debug
        self.axes = {}

    def off(self):
        pass
    
    def on(self):
        pass

    def ret0(self):
        '''Return all axes to 0'''
        for axis in self.axes.values():
            axis.set_pos(0)
        
    def stop(self):
        '''Gracefully stop the system at next interrupt point'''
        for axis in self.axes.values():
            axis.stop()

    def estop(self):
        '''Halt the system ASAP, possibly losing precision/position'''
        for axis in self.axes.values():
            axis.estop()
