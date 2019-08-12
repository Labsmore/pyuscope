'''
This file is part of uvscada
Licensed under 2 clause BSD license, see COPYING for details
'''

'''
UCN5804

x motor
stuck direection
looking into motor shaft
    CCW

IO Pin           Function
IO_08           IO8/Relay2  
IO7                IO_07
IO_09              IO9/Relay1    
IO6/Counter0    IO_06               
IO_0A           IOA/ADC4          
IO_05              IO5/ADC3                
IO_0B           IOB/ADC5          
IO_04              IO4/ADC2
IO_0C           IOC/ADC6
IO_03             IO3/ADC1
IO_0D           IO_02
IOD/ADC7        IO2/ADC0
IO_0E           IO_01
IOE/PWM0        IO1/RX0
IO_0F           IO_00
IOF/PWM1        IO0/TX0

alpha is using UCN5804 but waiting for new drivers in mail
'''

import time
from uvscada.usbio import USBIO
import threading

VERSION = 0.0

from axis import Axis
from controller import Controller

# Klinger / Micro-controle driver
class MC(Controller):
    def __init__(self, debug=False, log=None):
        Controller.__init__(self, debug=False, log=log)
        
        self.usbio = USBIO(debug=debug)
        self.log('opened some USBIO okay')
        if self.usbio.serial is None:
            raise Exception("USBIO missing serial")
        
        self.axes = [MCAxis('X', self, 0, 1, invert_dir=True, log=log),
                    MCAxis('Y', self, 2, 3, invert_dir=False, log=log),
                    MCAxis('Z', self, 4, 5, invert_dir=False, log=log),
                    ]
        
        for axis in self.axes:
            axis.forward(True)
        #self.inches()
        self.um()
        # enforce some initial state?
        #self.off()
        self.log('Controller ready')
    
    def __del__(self):
        '''
        When the pins go to high impedance things seem to go crazy
        Maybe all I need are some pullups / pulldowns
        '''
        self.off()
    
    def off(self):
        self.usbio.set_relay(2, False)
        self.is_on = False
    
    def on(self):
        '''
        After USBIO was plugged in but before it was initialized steppers were freaking out
        I'm not sure the exact cause but I solved it by routing the buffer power
        (USBIO is 3.3V and steppers require 3.5V min 5V safe)
        through relay 2
        '''
        self.usbio.set_relay(2, True)
        self.is_on = True
        
class MCAxis(Axis):
    def __init__(self, name, mc, step_pin, dir_pin, invert_dir = False, log=None):
        Axis.__init__(self, name, log=log)
        #self.mc = mc
        self.usbio = mc.usbio
        if self.usbio.serial is None:
            raise Exception("USBIO missing serial")
        self.step_pin = step_pin
        self.dir_pin = dir_pin
        self.invert_dir = invert_dir
        
        # Set a known output state
        # Unknown state to force set
        self.is_forward = None
        self.forward()
        
        self.usbio.set_gpio(self.step_pin, True)
        
        self.step_delay_s = None
        #self.step_delay_s = 0.001
        #self.step_delay_s = 5
        
        # Total number of steps
        self.net = 0

        self._stop = threading.Event()
        self._estop = threading.Event()
        
    def forever_pos(self):
        '''Go forever in the positive direction until stopped'''
        while not self._stop.is_set():
            if self._estop.is_set():
                return
            self.step(10)
        self._stop.clear()
        
    def forever_neg(self):
        '''Go forever in the negative direction until stopped'''
        while not self._stop.is_set():
            if self._estop.is_set():
                return
            self.step(-10)
        self._stop.clear()
    
    def stop(self):
        self._stop.set()

    def estop(self):
        self._estop.set()
    
    def unestop(self):
        self._estop.clear()
        
    def unstop(self):
        self._stop.clear()
        
    def step(self, steps):
        '''
        Yes this does have terrible real time performance bit banging over USB
        Was quick to setup and worked good enough for a while
        '''
        
        self.forward(steps > 0)

        # If steps would generate an extra step due to round carry over include it
        # instead of taking raw steps val
        # Ex: cur 10.3 + 4.8 steps => 5 steps now, not 4
        final_pos = self.net + steps
        for i in range(abs(int(final_pos - self.net))):
            # Loop runs quick enough that should detect reasonably quickly
            if self._estop.is_set():
                print 'MC axis %s: emergency stop detected!' % (self.name,)
                # Record what we finished since its little work
                self.net += i
                return
            #print 'Step %d / %d' % (i + 1, steps)
            # No idea if one is better before the other
            if self.step_delay_s:
                time.sleep(self.step_delay_s)
            self.usbio.set_gpio(self.step_pin, True)
            if self.step_delay_s:
                time.sleep(self.step_delay_s)
            self.usbio.set_gpio(self.step_pin, False)

        self.net = final_pos

    def forward(self, is_forward):
        if self.is_forward == is_forward:
            return
        self.usbio.set_gpio(self.dir_pin,
                    not is_forward if self.invert_dir else is_forward)
        self.is_forward = is_forward
    
    def home(self):
        # Assume current location is 0
        self.net = 0
    
    def mv_abs(self, units):
        self.step(units * self._spu - self.steps)

    def mv_rel(self, units):
        self.step(units * self._spu)

if __name__ == "__main__":
    mc = MC()
    
    while True:
        def s():
            time.sleep(0.5)
        d = 100
        print 'Jogging X'
        mc.axes['x'].jog(d)
        s()
        print 'Jogging Y'
        mc.axes['y'].jog(d)
        s()
        print 'Jogging -X'
        mc.axes['x'].jog(-d)
        s()
        print 'Jogging -Y'
        mc.axes['y'].jog(-d)
        s()
        
    while True:
        for axis in mc.axes.values():
            print 'Jogging %s' % axis.name
            axis.jog(100)

