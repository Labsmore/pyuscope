#!/usr/bin/env python3

from pynput import keyboard
import serial
import time
import sys

if len(sys.argv) == 2:
    p = sys.argv[1]
else:
    p = '/dev/tty.usbserial-1410'

s = serial.Serial(p, 115200)
#s.open()

def on_press(key):
    print("")
    value = key.char
    # value = key.value
    print('alphanumeric key {0} pressed'.format(
        value))
    if value == 'd':
        s.write(b'$J=G91 X1 F100\n')
    if value == 'a':
        s.write(b'$J=G91 X-1 F100\n')

def on_release(key):
    print("")
    s.write(b'\x85')
    # Adding delayed cancel as well, because
    # there are times when the cancel doesn't
    # take, possibly a queueing issue.
    # 0.1 doesn't seem to work. 0.2 is fine.
    time.sleep(0.2)
    s.write(b'\x85')
    print('{0} released'.format(
        key))
    if key == keyboard.Key.esc:
        # Stop listener
        return False

# Collect events until released
with keyboard.Listener(
        on_press=on_press,
        on_release=on_release) as listener:
    listener.join()
