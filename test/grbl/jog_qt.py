#!/usr/bin/env python3
"""
Jog 3018 CNC using arrow keys

# Apps that interfere with serial ports
sudo apt-get remove modemmanager brltty
# Software we need
sudo apt-get install -y python3-serial python3-pyqt5
usermod -a -G dialout $USER
"""

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
import sys
from uscope.util import add_bool_arg
from uscope.motion.grbl import get_grbl
import time
import math

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
        # log scaled to slider
        self.jog_min = 1
        self.jog_max = 1000
        self.jog_cur = None
        # careful hard coded below as 2.0
        self.slider_min = 1
        self.slider_max = 100

        self.grbl = grbl
        QMainWindow.__init__(self)
        self.initUI()
        self.last_send = time.time()

    def initUI(self):
        self.setWindowTitle('Demo')

        def labels():
            layout = QHBoxLayout()
            layout.addWidget(QLabel("1"))
            layout.addWidget(QLabel("10"))
            layout.addWidget(QLabel("100"))
            layout.addWidget(QLabel("1000"))
            return layout

        layout = QVBoxLayout()
        # layout.addWidget(QPushButton())
        layout.addLayout(labels())
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(self.slider_min)
        self.slider.setMaximum(self.slider_max)
        self.slider.setValue(self.slider_max // 2)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTickInterval(33)
        # Send keyboard events to CNC navigation instead
        self.slider.setFocusPolicy(Qt.NoFocus)
        layout.addWidget(self.slider)
        self.slider.valueChanged.connect(self.sliderChanged)
        self.sliderChanged()

        widget = QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)

        # self.showMaximized()
        self.show()

    def sliderChanged(self):
        slider_val = float(self.slider.value())
        v = math.log(slider_val, 10)
        # Scale in log space
        log_scalar = (math.log(self.jog_max, 10) -
                      math.log(self.jog_min, 10)) / 2.0
        v = math.log(self.jog_min, 10) + v * log_scalar
        # Convert back to linear space
        v = 10**v
        self.jog_cur = max(min(v, self.jog_max), self.jog_min)
        print("jog: slider %u => jog %u (was %u)" %
              (slider_val, self.jog_cur, v))

    def keyPressEvent(self, event):
        k = event.key()
        # Ignore duplicates, want only real presses
        if 0 and event.isAutoRepeat():
            return

        # spamming too many commands and queing up
        if time.time() - self.last_send < 0.1:
            return
        self.last_send = time.time()

        # Focus is sensitive...should step slower?
        # worry sonce focus gets re-integrated

        axis = axis_map.get(k, None)
        print("press %s" % (axis, ))
        # return
        if axis:
            axis, sign = axis
            print("Key jogging %s%c" % (axis, {1: '+', -1: '-'}[sign]))

            cmd = "G91 %s%0.3f F%u" % (axis, sign * 1.0, self.jog_cur)
            print("JOG:", cmd)
            grbl.gs.j(cmd)
            if 1:
                mpos = grbl.qstatus()["MPos"]
                print("X%0.3f Y%0.3f Z%0.3F" %
                      (mpos["x"], mpos["y"], mpos["z"]))

    def keyReleaseEvent(self, event):
        # Don't move around with moving around text boxes, etc
        # if not self.video_container.hasFocus():
        #    return
        k = event.key()
        # Ignore duplicates, want only real presses
        if event.isAutoRepeat():
            return

        axis = axis_map.get(k, None)
        print("release %s" % (axis, ))
        # return
        if axis:
            grbl.gs.cancel_jog()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description="Execute a G1 movement command")

    parser.add_argument("--port", default=None, help="serial port")
    add_bool_arg(parser, "--verbose", default=False, help="Verbose output")
    args = parser.parse_args()

    grbl = get_grbl(port=args.port, verbose=args.verbose)

    app = QApplication(sys.argv)
    _mainwin = TestGUI(grbl=grbl)
    # XXX: what about the gstreamer message bus?
    # Is it simply not running?
    # must be what pygst is doing
    sys.exit(app.exec_())
