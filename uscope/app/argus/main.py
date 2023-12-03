#!/usr/bin/env python3

from uscope.gui.gstwidget import gstwidget_main
from uscope.util import add_bool_arg
from uscope.gui.widgets import AMainWindow
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

from uscope.gui.common import ArgusCommon, ArgusShutdown, error

from uscope.gui.widgets import TopWidget, BatchImageTab, AdvancedTab, StitchingTab, MeasureTab
from uscope.gui.imaging import MainTab, ImagerTab
from uscope.gui.scripting import ScriptingTab


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


class MainWindow(AMainWindow):
    def __init__(self, microscope=None, verbose=False):
        AMainWindow.__init__(self)
        # Homing may need attention in CLI
        # Make sure user sees that before UI
        self.tabs = {}
        self.hide()
        self.verbose = verbose
        self.polli = 0
        self.ac = ArgusCommon(microscope_name=microscope, mw=self)
        self.init_objects()
        self.ac.logs.append(self.mainTab.log)
        self.initUI()
        # Load last GUI state
        self.cache_load()
        # something causes this to pop back up
        # keep it hidden until we are homed since homing is still on CLI...
        self.hide()
        self.post_ui_init()
        self.show()

        # sometimes GUI maximization doesn't stick
        self.showMaximized()

    def _cache_load(self, j):
        j = j.get("main_window", {})
        self.displayLimits.setChecked(
            j.get("display_limits", config.bc.dev_mode()))
        self.displayLimitsTriggered()
        self.displayAdvancedMovement.setChecked(
            j.get("display_advanced_movement", config.bc.dev_mode()))
        self.displayAdvancedMovementTriggered()
        self.displayAdvancedObjective.setChecked(
            j.get("display_advanced_objective", config.bc.dev_mode()))
        self.displayAdvancedObjectiveTriggered()

    def _cache_save(self, j):
        j = j.setdefault("main_window", {})
        j["display_limits"] = self.displayLimits.isChecked()
        j["display_advanced_movement"] = self.displayAdvancedMovement.isChecked(
        )
        j["display_advanced_objective"] = self.displayAdvancedObjective.isChecked(
        )

    def add_tab(self, cls, name):
        tab = cls(ac=self.ac, aname=name, parent=self)
        self.tabs[name] = tab
        return tab

    def init_objects(self):
        # Tabs
        self.mainTab = self.add_tab(MainTab, "Main")
        self.imagerTab = self.add_tab(ImagerTab, "Imager")
        self.measureTab = self.add_tab(MeasureTab, "Measure")
        self.batchTab = self.add_tab(BatchImageTab, "Batch")
        self.scriptingTab = self.add_tab(ScriptingTab, "Scripting")
        self.advancedTab = self.add_tab(AdvancedTab, "Advanced")
        self.stitchingTab = self.add_tab(StitchingTab, "CloudStitch")

        # FIXME: hack, come up with something more intuitive
        self.ac.mainTab = self.mainTab
        self.ac.scriptingTab = self.scriptingTab
        self.ac.stitchingTab = self.stitchingTab
        self.ac.batchTab = self.batchTab

    def createMenuBar(self):
        self.exitAction = QAction("Exit", self)
        self.helpContentAction = QAction("Help Content", self)
        self.aboutAction = QAction("About", self)

        menuBar = self.menuBar()

        # File menu
        fileMenu = QMenu("File", self)
        menuBar.addMenu(fileMenu)
        fileMenu.addAction(self.exitAction)

        # Video menu
        videoMenu = menuBar.addMenu("Video")
        # action
        self.zoomPlus = QAction("ROI zoom +", self)
        videoMenu.addAction(self.zoomPlus)
        self.zoomPlus.triggered.connect(self.zoom_plus)
        # action
        self.zoomMinus = QAction("ROI zoom -", self)
        videoMenu.addAction(self.zoomMinus)
        self.zoomMinus.triggered.connect(self.zoom_minus)
        # action
        self.fullShow = QAction("Full show", self)
        videoMenu.addAction(self.fullShow)
        self.fullShow.triggered.connect(self.full_show)
        # action
        self.displayAdvancedObjective = QAction("Advanced objective",
                                                self,
                                                checkable=True)
        videoMenu.addAction(self.displayAdvancedObjective)
        self.displayAdvancedObjective.triggered.connect(
            self.displayAdvancedObjectiveTriggered)

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
        # Advanced movement
        self.displayAdvancedMovement = QAction("Advanced movement",
                                               motionMenu,
                                               checkable=True)
        motionMenu.addAction(self.displayAdvancedMovement)
        self.displayAdvancedMovement.setChecked(config.bc.dev_mode())
        self.displayAdvancedMovement.triggered.connect(
            self.displayAdvancedMovementTriggered)

        # Help menu
        helpMenu = menuBar.addMenu("Help")
        helpMenu.addAction(self.helpContentAction)
        helpMenu.addAction(self.aboutAction)

        self.exitAction.triggered.connect(self.close)
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

    def displayAdvancedObjectiveTriggered(self):
        self.ac.mainTab.objective_widget.show_advanced(
            bool(self.displayAdvancedObjective.isChecked()))

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

    def _initUI(self):
        self.setWindowTitle("pyuscope main")
        self.setWindowIcon(QIcon(config.GUI.icon_files["logo"]))
        self.fullscreen_widget = None

        layout = QHBoxLayout()

        self.video_widget = self.ac.vidpip.get_widget("zoomable")
        self.video_widget.setParent(self)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.setSizePolicy(policy)

        def left():
            layout = QVBoxLayout()

            self.top_widget = TopWidget(ac=self.ac, parent=self)
            self.ac.top_widget = self.top_widget
            self.top_widget.initUI()
            layout.addWidget(self.top_widget)

            self.tab_widget = QTabWidget()
            for tab_name, tab in self.tabs.items():
                # tab.initUI()
                self.tab_widget.addTab(tab, tab_name)
            self.batchTab.add_pconfig_source(self.mainTab, "Main tab")
            layout.addWidget(self.tab_widget)

            return layout

        layout.addLayout(left())
        layout.addWidget(self.video_widget)

        self.central_widget = QWidget()
        self.central_widget.setLayout(layout)
        self.setCentralWidget(self.central_widget)
        self.createMenuBar()
        self.showMaximized()

    def poll_misc(self):
        self.polli += 1
        ac = self.ac

        motion_thread = ac.motion_thread
        # deleted during shutdown => can lead to crash during shutdown
        if motion_thread:
            motion_thread.update_pos_cache()

        # FIXME: maybe better to do this with events
        # Loose the log window on shutdown...should log to file?
        try:
            ac.poll_misc()
        except ArgusShutdown:
            print(traceback.format_exc())
            ac.shutdown()
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

        self.ac.update_pconfigs.append(self.mainTab.update_pconfig)
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
        # KiCAD zoom in / out => F1 / F2
        elif k == Qt.Key_F1:
            self.ac.vidpip.zoomable_plus()
        elif k == Qt.Key_F2:
            self.ac.vidpip.zoomable_minus()
        elif k == Qt.Key_F3:
            self.ac.vidpip.zoomable_high_toggle()
        elif event.key() == Qt.Key_F11:
            if self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()
                self.showFullScreen()
        else:
            event.ignore()
            return
        event.accept()


    def invertKJXYTriggered(self):
        mw = self.mainTab.motion_widget
        if self.invertKJXY.isChecked():
            mw.set_kj_xy_scalar(-1.0)
        else:
            mw.set_kj_xy_scalar(+1.0)

    def displayLimitsTriggered(self):
        self.ac.mainTab.show_minmax(bool(self.displayLimits.isChecked()))

    def displayAdvancedMovementTriggered(self):
        self.ac.mainTab.motion_widget.show_advanced_movement(
            bool(self.displayAdvancedMovement.isChecked()))


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
