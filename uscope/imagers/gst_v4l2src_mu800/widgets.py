from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

from uscope.control_scroll_base import GstControlScroll

from collections import OrderedDict
"""
acts on file descriptor directly via v4l2 API
(like on old GUI)
"""


class V4L2MU800ControlScroll(QScrollArea):
    def __init__(self, vidpip, parent=None):
        QScrollArea.__init__(self, parent=parent)
