#!/usr/bin/env -S uv run
"""
Jog button must visibly turn yellow while it has keyboard focus,
regardless of QStyle (gtk2 ignores QPalette.Button)

Run with QT_QPA_PLATFORM=offscreen
"""

import unittest

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QLineEdit, QStyleFactory, QWidget

from uscope.gui.widgets import JogListener

app = QApplication.instance() or QApplication([])


class Parent(QWidget):
    def keyPressEventCaptured(self, event):
        pass

    def keyReleaseEventCaptured(self, event):
        pass


def yellow_fraction(widget):
    img = widget.grab().toImage()
    yellow = 0
    for x in range(img.width()):
        for y in range(img.height()):
            c = QColor(img.pixel(x, y))
            if c.red() > 200 and c.green() > 150 and c.blue() < 100:
                yellow += 1
    return yellow / (img.width() * img.height())


class TestJogColor(unittest.TestCase):
    def check_style(self, style):
        app.setStyle(QStyleFactory.create(style))
        parent = Parent()
        layout = QHBoxLayout(parent)
        button = JogListener("Jog (coarse)", parent)
        layout.addWidget(button)
        other = QLineEdit(parent)
        layout.addWidget(other)
        parent.resize(300, 50)
        parent.show()
        parent.activateWindow()
        try:
            other.setFocus()
            app.processEvents()
            self.assertLess(yellow_fraction(button), 0.05)
            size = button.size()

            button.setFocus()
            app.processEvents()
            self.assertTrue(button.hasFocus())
            self.assertGreater(yellow_fraction(button), 0.5)
            self.assertEqual(size, button.size())

            button.setDown(True)
            app.processEvents()
            self.assertGreater(yellow_fraction(button), 0.5)
            button.setDown(False)

            other.setFocus()
            app.processEvents()
            self.assertLess(yellow_fraction(button), 0.05)
        finally:
            parent.close()

    def test_styles(self):
        styles = QStyleFactory.keys()
        self.assertTrue(styles)
        for style in styles:
            with self.subTest(style=style):
                self.check_style(style)


if __name__ == "__main__":
    unittest.main()
