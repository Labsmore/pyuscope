#!/usr/bin/env python3

from pynput import keyboard
import serial
import time
import sys
from threading import Thread, Lock

def on_press(key):
    mutex.acquire()
    value = getattr(key, "char", None)
    print("")
    print('pressed %s' % value)
    if value == 'd':
        s.write(b'$J=G91 X1 F100\n')
        s.flush()
    if value == 'a':
        s.write(b'$J=G91 X-1 F100\n')
        s.flush()
    mutex.release()

def on_release(key):
    mutex.acquire()
    value = getattr(key, "char", None)
    print("")
    print('released %s' % value)
    # Adding delayed cancel as well, because
    # there are times when the cancel doesn't
    # take, possibly a queueing issue.
    # 0.1 doesn't seem to work. 0.2 is fine.
    for i in range(3):
        s.write(b'\x85')
        s.flush()
        time.sleep(0.1)
    if key == keyboard.Key.esc:
        # Stop listener
        return False
    mutex.release()

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description="Control X axis with a/d")
    parser.add_argument("tty", nargs="?", default='/dev/tty.usbserial-1410', help="tty")
    args = parser.parse_args()

    s = serial.Serial(args.tty, 115200)
    mutex = Lock()

    # Collect events until released
    with keyboard.Listener(
            on_press=on_press,
            on_release=on_release) as listener:
        listener.join()
