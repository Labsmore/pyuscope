#!/usr/bin/env python3

from uscope.gui.gstwidget import gstwidget_main
from uscope.util import add_bool_arg
from uscope import config
import json
import json5
from collections import OrderedDict

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import os.path
import sys
import traceback
import threading

from uscope.app.argus.common import ArgusCommon, ArgusShutdown, error

from uscope.app.argus.widgets import MainTab, ImagerTab, BatchImageTab, AdvancedTab, StitchingTab
from uscope.app.argus.scripting import ScriptingTab


class FullscreenVideo(QWidget):
    closing = pyqtSignal()

    def __init__(self, widget):
        super().__init__()
        self.setWindowTitle("pyuscope fullscreen")
        layout = QVBoxLayout()
        layout.addWidget(widget)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        widget.setSizePolicy(policy)
        layout.setAlignment(Qt.AlignCenter)
        self.setLayout(layout)
        self.showMaximized()

    def closeEvent(self, event):
        self.closing.emit()


class MainWindow(QMainWindow):
    def __init__(self, microscope=None, verbose=False):
        QMainWindow.__init__(self)
        # Homing may need attention in CLI
        # Make sure user sees that before UI
        self.hide()
        self.verbose = verbose
        self.shutting_down = False
        self.ac = None
        self.awidgets = OrderedDict()
        self.polli = 0
        self.ac = ArgusCommon(microscope_name=microscope, mw=self)
        self.init_objects()
        self.ac.logs.append(self.mainTab.log)
        self.initUI()
        # something caues this to pop back up
        # keep it hidden until we are homed since homing is still on CLI...
        self.hide()
        self.post_ui_init()
        self.cachej = None
        self.show()

    def __del__(self):
        self.shutdown()

    def cache_load(self):
        fn = self.ac.aconfig.cache_fn()
        self.cachej = {}
        if os.path.exists(fn):
            with open(fn, "r") as f:
                self.cachej = json5.load(f)
        for tab in self.awidgets.values():
            tab.cache_load(self.cachej)

    def cache_save(self):
        if not self.ac:
            return
        cachej = {}
        for tab in self.awidgets.values():
            tab.cache_save(cachej)
        fn = self.ac.aconfig.cache_fn()
        with open(fn, "w") as f:
            json.dump(cachej,
                      f,
                      sort_keys=True,
                      indent=4,
                      separators=(",", ": "))

    def closeEvent(self, event):
        self.shutdown()

    def shutdown(self):
        # Concern multiple closing events may fight
        if self.shutting_down:
            return
        self.shutting_down = True

        if self.fullscreen_widget:
            self.fullscreen_widget.close()

        self.cache_save()
        for tab in self.awidgets.values():
            tab.shutdown()
        try:
            if self.ac:
                self.ac.shutdown()
        except AttributeError:
            pass

    def init_objects(self):
        # Tabs
        self.mainTab = MainTab(ac=self.ac, parent=self)
        self.awidgets["Main"] = self.mainTab
        self.imagerTab = ImagerTab(ac=self.ac, parent=self)
        self.awidgets["Imager"] = self.imagerTab
        self.batchTab = BatchImageTab(ac=self.ac, parent=self)
        self.awidgets["Batch"] = self.batchTab
        self.scriptingTab = ScriptingTab(ac=self.ac, parent=self)
        self.awidgets["Scripting"] = self.scriptingTab
        self.advancedTab = AdvancedTab(ac=self.ac, parent=self)
        self.awidgets["Advanced"] = self.advancedTab
        self.stitchingTab = StitchingTab(ac=self.ac, parent=self)
        self.awidgets["CloudStitch"] = self.stitchingTab
        self.ac.mainTab = self.mainTab
        self.ac.scriptingTab = self.scriptingTab
        self.ac.stitchingTab = self.stitchingTab
        self.ac.batchTab = self.batchTab

    def createMenuBar(self):
        self.exitAction = QAction("Exit", self)
        self.zoomPlus = QAction("ROI zoom +", self)
        self.zoomMinus = QAction("ROI zoom -", self)
        self.fullShow = QAction("Full show", self)
        self.helpContentAction = QAction("Help Content", self)
        self.aboutAction = QAction("About", self)

        menuBar = self.menuBar()

        # File menu
        fileMenu = QMenu("File", self)
        menuBar.addMenu(fileMenu)
        fileMenu.addAction(self.exitAction)

        # Video menu
        videoMenu = menuBar.addMenu("Video")
        videoMenu.addAction(self.zoomPlus)
        videoMenu.addAction(self.zoomMinus)
        videoMenu.addAction(self.fullShow)

        motionMenu = menuBar.addMenu("Motion")
        # Some people prefer perspective of moving camera, some prefer moving stage
        self.invertKJXY = QAction("Invert keyboard/joystick XY",
                                  motionMenu,
                                  checkable=True)
        motionMenu.addAction(self.invertKJXY)
        self.invertKJXY.triggered.connect(self.invertKJXYTriggered)
        # Adds a lot of clutter
        # off by default
        self.displayLimits = QAction("Limit display",
                                     motionMenu,
                                     checkable=True)
        motionMenu.addAction(self.displayLimits)
        self.displayLimits.setChecked(config.bc.dev_mode())
        self.displayLimits.triggered.connect(self.displayLimitsTriggered)

        # Help menu
        helpMenu = menuBar.addMenu("Help")
        helpMenu.addAction(self.helpContentAction)
        helpMenu.addAction(self.aboutAction)

        self.exitAction.triggered.connect(self.close)
        self.zoomPlus.triggered.connect(self.zoom_plus)
        self.zoomMinus.triggered.connect(self.zoom_minus)
        self.fullShow.triggered.connect(self.full_show)
        self.helpContentAction.triggered.connect(self.helpContent)
        self.aboutAction.triggered.connect(self.about)

    def close(self):
        pass

    def zoom_plus(self):
        self.ac.vidpip.zoomable_plus()

    def zoom_minus(self):
        self.ac.vidpip.zoomable_minus()

    def full_show(self):
        if self.fullscreen_widget:
            return
        # need to coordinate moving this to the right window
        # for now just let it float
        # self.ac.vidpip.player.set_state(Gst.State.PAUSED)
        widget = self.ac.vidpip.add_full_widget()
        self.fullscreen_widget = FullscreenVideo(widget)
        self.fullscreen_widget.show()
        self.ac.vidpip.full_restart_pipeline()
        self.fullscreen_widget.closing.connect(self.shutdown)

    """
    def full_hide(self):
        if not self.fullscreen_widget:
            return
        self.ac.vidpip.remove_full_widget()
        self.fullscreen_widget.close()
    """

    def helpContent(self):
        pass

    def about(self):
        pass

    def initUI(self):
        self.ac.initUI()
        self.setWindowTitle("pyuscope main")
        self.setWindowIcon(QIcon(config.GUI.icon_files["logo"]))
        self.fullscreen_widget = None

        layout = QHBoxLayout()

        self.video_widget = self.ac.vidpip.get_widget("zoomable")
        self.video_widget.setParent(self)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.setSizePolicy(policy)

        self.tab_widget = QTabWidget()
        for tab_name, tab in self.awidgets.items():
            tab.initUI()
            self.tab_widget.addTab(tab, tab_name)
        self.cache_load()
        self.batchTab.add_pconfig_source(self.mainTab, "Main tab")

        layout.addWidget(self.tab_widget)
        layout.addWidget(self.video_widget)

        self.central_widget = QWidget()
        self.central_widget.setLayout(layout)
        self.setCentralWidget(self.central_widget)
        self.createMenuBar()
        self.showMaximized()

    def poll_misc(self):
        self.polli += 1
        self.ac.motion_thread.update_pos_cache()

        # FIXME: maybe better to do this with events
        # Loose the log window on shutdown...should log to file?
        try:
            self.ac.poll_misc()
        except ArgusShutdown:
            print(traceback.format_exc())
            self.ac.shutdown()
            QCoreApplication.exit(1)
            return

        # FIXME: convert to plugin iteration
        self.mainTab.planner_widget_xy2p.poll_misc()
        self.mainTab.planner_widget_xy3p.poll_misc()
        self.mainTab.motion_widget.poll_misc()
        self.imagerTab.poll_misc()
        self.scriptingTab.poll_misc()

        # Save ocassionally / once 3 seconds
        if self.polli % 15 == 0:
            self.cache_save()

    def post_ui_init(self):
        self.ac.log("pyuscope starting")
        self.ac.log("https://github.com/Labsmore/pyuscope/")
        self.ac.log("For enquiries contact support@labsmore.com")
        self.ac.log("")

        self.ac.update_pconfigs.append(self.advancedTab.update_pconfig)
        self.ac.update_pconfigs.append(self.imagerTab.update_pconfig)
        # Start services
        # This will microscope.configure() which is needed by later tabs
        self.ac.post_ui_init()

        # Tabs
        for tab in self.awidgets.values():
            tab.post_ui_init()

        self.poll_timer = QTimer()
        self.poll_timer.setSingleShot(False)
        self.poll_timer.timeout.connect(self.poll_misc)
        self.poll_timer.start(200)

        #if self.ac.microscope.joystick:
        #    self.addTab(JoystickTab(ac=self.ac, parent=self))

    def keyPressEvent(self, event):

        k = event.key()
        if k == Qt.Key_Escape:
            self.ac.motion_thread.stop()

        # Ignore duplicates, want only real presses
        if 0 and event.isAutoRepeat():
            return

        # KiCAD zoom in / out => F1 / F2
        if k == Qt.Key_F1:
            self.ac.vidpip.zoomable_plus()
        elif k == Qt.Key_F2:
            self.ac.vidpip.zoomable_minus()
        elif k == Qt.Key_F3:
            self.ac.vidpip.zoomable_high_toggle()

    def invertKJXYTriggered(self):
        mw = self.mainTab.motion_widget
        if self.invertKJXY.isChecked():
            mw.set_kj_xy_scalar(-1.0)
        else:
            mw.set_kj_xy_scalar(+1.0)

    def displayLimitsTriggered(self):
        self.ac.mainTab.show_minmax(bool(self.displayLimits.isChecked()))


def parse_args():
    import argparse

    parser = argparse.ArgumentParser(description='')
    parser.add_argument('--microscope', help="Which microscope config to use")
    add_bool_arg(parser, '--verbose', default=None)
    args = parser.parse_args()

    args.verbose and print("Parsing args in thread %s" %
                           (threading.get_ident(), ))

    return vars(args)


def main():
    try:
        gstwidget_main(MainWindow, parse_args=parse_args)
    except Exception as e:
        print(traceback.format_exc())
        error(str(e))


if __name__ == '__main__':
    main()
