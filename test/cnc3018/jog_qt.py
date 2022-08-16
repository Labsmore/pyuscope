#!/usr/bin/env python3

"""
Jog 3018 CNC using arrow keys
"""

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
import sys
from uscope.util import add_bool_arg
from uscope.motion.grbl import GRBL
import time

axis_map = {
            # Upper left origin
            Qt.Key_Left: ("X", -1),
            Qt.Key_Right: ("X", 1),
            Qt.Key_Up: ("Y", -1),
            Qt.Key_Down: ("Y", 1),
            Qt.Key_PageUp: ("Z", 1),
            Qt.Key_PageDown: ("Z", -1),
        }

class TestGUI(QMainWindow):

    def __init__(self, grbl):
        self.grbl = grbl
        QMainWindow.__init__(self)
        self.initUI()

    def initUI(self):
        self.setWindowTitle('Demo')

        widget = QWidget()
        self.setCentralWidget(widget)

        # self.showMaximized()
        self.show()


    def keyPressEvent(self, event):
        k = event.key()
        # Ignore duplicates, want only real presses
        if event.isAutoRepeat():
            return

        # Focus is sensitive...should step slower?
        # worry sonce focus gets re-integrated

        axis = axis_map.get(k, None)
        if axis:
            axis, sign = axis
            print("Key jogging %s%c" % (axis, {1: '+', -1: '-'}[sign]))

            cmd = "G91 %s%0.3f" % (axis, sign * 1.0)
            grbl.gs.j(cmd)
            if 1:
                mpos = grbl.qstatus()["MPos"]
                print("X%0.3f Y%0.3f Z%0.3F" % (mpos["x"], mpos["y"], mpos["z"]))


    def keyReleaseEvent(self, event):
        # Don't move around with moving around text boxes, etc
        # if not self.video_container.hasFocus():
        #    return
        k = event.key()
        # Ignore duplicates, want only real presses
        if event.isAutoRepeat():
            return

        axis = axis_map.get(k, None)
        if axis:
            """
            s.write(b'\x85')
            # Adding delayed cancel as well, because
            # there are times when the cancel doesn't
            # take, possibly a queueing issue.
            # 0.1 doesn't seem to work. 0.2 is fine.
            time.sleep(0.2)
            s.write(b'\x85')
            """
            grbl.gs.cancel()
            time.sleep(0.2)
            grbl.gs.cancel()

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description="Execute a G1 movement command")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    grbl = GRBL(verbose=args.verbose)

    app = QApplication(sys.argv)
    _mainwin = TestGUI(grbl=grbl)
    # XXX: what about the gstreamer message bus?
    # Is it simply not running?
    # must be what pygst is doing
    sys.exit(app.exec_())
