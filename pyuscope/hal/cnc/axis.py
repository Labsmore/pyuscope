'''
This file is part of uvscada
Licensed under 2 clause BSD license, see COPYING for details
'''

'''
A simple axis that is controlled by a step and direction input
Usually a stepper motor but not necessarily (ex: Gecko G320X servo controller)

Works in steps with a convenience interface in nameless units
It is up to higher level logic if it needs to be metric vs SAE aware, um vs mm, etc
'''
class Axis(object):
    def __init__(self, name, log=None, spu=None):
        if log is None:
            def log(s):
                print s
        self._log = log
        self._name = name
        # Steps per unit
        self._spu = spu
        # Total number of steps
        # Fractional: the actual number of steps is the truncated value
        self._net = 0.0
    
    def __str__(self):
        return self.name
    
    def to_steps(self, units):
        '''Convert from steps to units'''
        return int(units * self._spu)

    def to_units(self, steps):
        '''Convert from units to steps'''
        return steps / self._spu
    
    def units(self):
        '''Convert current step count to units'''
        return self.net / self._spu
        
    def mv_abs(self, units):
        '''Go to absolute position as fast as possible'''
        raise Exception('Required')

    def mv_rel(self, units):
        '''Move axis relative to current position as fast as possible'''
        self.step(self.steps(units))
    
    def step(self, steps):
        raise Exception("Required")

    def stop(self):
        '''Gracefully stop the system at next interrupt point'''
        raise Exception('Required')

    def estop(self):
        '''Halt the system ASAP, possibly losing precision/position'''
        self.stop()
        
    def unestop(self):
        '''Clear emergency stop, if any'''
        raise Exception('Required')
      
    def forever_neg(self, done):
        '''Decrease until stopped'''
        raise Exception('Required')
          
    def forever_pos(self, done, callback=None):
        '''Increase until stopped'''
        raise Exception('Required')

