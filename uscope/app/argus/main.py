#!/usr/bin/env python3

from uscope.gui.gstwidget import gstwidget_main
from uscope.util import add_bool_arg
from uscope.gui.widgets import AMainWindow
from uscope import config
import json
import json5
from collections import OrderedDict
from uscope.gui.input_widget import InputWidget

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


# Can't save a dict like {(1, 2): "a"}
def tupledict_to_json(j):
    # {(1, 2): "a"} => [((1, 2), "a")]
    return [(k, v) for k, v in j.items()]


def json_to_tupledict(j):
    # [((1, 2), "a")] => {(1, 2): "a"}
    return dict([(tuple(k), v) for k, v in j])


class ArgusOptionsWindow(QWidget):
    def __init__(self, mw, parent=None):
        super().__init__(parent=parent)
        self.mw = mw
        self.ac = self.mw.ac
        # self.motion_damper = None
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout()

        def motion_gb():
            layout = QGridLayout()
            row = 0

            layout.addWidget(
                QLabel("Motion damper (default 1.0 => full speed)"), row, 0)
            self.motion_damper_le = QLineEdit("")
            self.motion_damper_le.returnPressed.connect(
                self.motion_damper_le_return)
            layout.addWidget(self.motion_damper_le, row, 1)
            row += 1

            gb = QGroupBox("Motion")
            gb.setLayout(layout)

            return gb

        def joystick_gb():
            layout = QVBoxLayout()

            self.joystick_iw = None
            if self.ac.microscope.joystick is None:
                layout.addWidget(QLabel("None"))
            else:
                self.joystick_iw = InputWidget(
                    return_pressed=self.joystick_return)
                iconfig = {}
                joystick_config = self.ac.microscope.joystick.config.get_user_config(
                )
                for function_name, function_val in joystick_config.items():
                    for k, v in function_val.items():
                        label = "%s.%s (default %s)" % (function_name, k,
                                                        v.get("default"))
                        iconfig[label] = {
                            "key": (function_name, k),
                            # better to be none by default
                            # "default": v.get("default"),
                            "type": float,
                            "empty_as_none": True,
                            "widget": "QLineEdit",
                        }
                self.joystick_iw.configure(iconfig)
                layout.addWidget(self.joystick_iw)

            gb = QGroupBox("Joystick")
            gb.setLayout(layout)

            return gb

        layout.addWidget(motion_gb())
        layout.addWidget(joystick_gb())
        self.setLayout(layout)

    def motion_damper_le_return(self, lazy=False):
        s = str(self.motion_damper_le.text()).strip()
        if s:
            try:
                motion_damper = float(s)
            except ValueError:
                self.ac.log("Failed to parse motion damper scalar")
                return
            if motion_damper <= 0 or motion_damper > 1.0:
                self.ac.log(
                    f"Require motion damper 0 < {motion_damper} <= 1.0")
                return
        else:
            if lazy:
                return
            motion_damper = 1.0
        self.ac.log(f"Setting motion damper {motion_damper}")
        self.ac.microscope.motion_ts().apply_damper(motion_damper)
        # self.motion_damper = motion_damper

    def joystick_return(self):
        try:
            value = self.joystick_iw.getValues()
        except Exception as e:
            self.ac.log(f"Failed to parse input value: {type(e)}, {e}")
            return
        # print("joystick value", value)
        config = {}
        for (function_name, k), v in value.items():
            config.setdefault(function_name, {})[k] = v
        # print("joystick config", config)
        self.ac.microscope.joystick.config.set_user_config(config)

    def cache_load(self, j):
        j = j.get("main_window", {}).get("options", {})
        self.motion_damper_le.setText(j.get("motion_damper", ""))
        self.motion_damper_le_return(lazy=True)
        if self.joystick_iw:
            try:
                saved_val = j.get("joystick_iw")
                if saved_val:
                    self.joystick_iw.setValues(json_to_tupledict(saved_val))
            except Exception as e:
                print("WARNING: failed to load joystick calibration", e)

    def cache_save(self, j):
        j = j.setdefault("main_window", {}).setdefault("options", {})
        j["motion_damper"] = str(self.motion_damper_le.text())
        if self.joystick_iw:
            values = self.joystick_iw.getValues()
            j["joystick_iw"] = tupledict_to_json(values)


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
        self.hide()
        self.verbose = verbose
        self.ac = ArgusCommon(microscope_name=microscope, mw=self)
        self.init_objects()
        self.ac.logs.append(self.mainTab.log)
        self.initUI()
        # something causes this to pop back up
        # keep it hidden until we are homed since homing is still on CLI...
        self.hide()
        self.post_ui_init()

        # Load last GUI state
        # Must be done after post_ui_init() as may depend on threads being fully initialized
        self.cache_load()

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
        self.argus_options_window.cache_load(j)

    def _cache_save(self, j):
        j = j.setdefault("main_window", {})
        j["display_limits"] = self.displayLimits.isChecked()
        j["display_advanced_movement"] = self.displayAdvancedMovement.isChecked(
        )
        j["display_advanced_objective"] = self.displayAdvancedObjective.isChecked(
        )
        self.argus_options_window.cache_save(j)

    def add_tab(self, cls, name):
        tab = cls(ac=self.ac, aname=name, parent=self)
        self.ac.tabs[name] = tab
        return tab

    def init_objects(self):
        # Tabs
        self.mainTab = self.add_tab(MainTab, "Main")
        self.imagerTab = self.add_tab(ImagerTab, "Imager")
        self.measureTab = self.add_tab(MeasureTab, "Measure")
        self.batchTab = self.add_tab(BatchImageTab, "Batch")
        self.scriptingTab = self.add_tab(ScriptingTab, "Scripting")
        self.instrumentTabs = {}
        for name, configj in self.ac.bc.instruments().items():
            configj.setdefault("name", name)
            tab_name = configj.get("tab_name", f"Instrument: {name}")
            tab = self.add_tab(ScriptingTab, tab_name)
            tab.set_instrument_config(configj)
            tab.setVisible(configj.get("visible", True))
            self.instrumentTabs[name] = tab
        self.advancedTab = self.add_tab(AdvancedTab, "Advanced")
        self.stitchingTab = self.add_tab(StitchingTab, "CloudStitch")

        # FIXME: hack, come up with something more intuitive
        self.ac.mainTab = self.mainTab
        self.ac.scriptingTab = self.scriptingTab
        self.ac.stitchingTab = self.stitchingTab
        self.ac.batchTab = self.batchTab
        self.ac.advancedTab = self.advancedTab

        self.argus_options_window = ArgusOptionsWindow(self)

    def createMenuBar(self):
        self.exitAction = QAction("Exit", self)
        self.helpContentAction = QAction("Help Content", self)
        self.aboutAction = QAction("About", self)

        menuBar = self.menuBar()

        # File menu
        fileMenu = QMenu("File", self)
        menuBar.addMenu(fileMenu)
        # Option
        self.clearLog = QAction("Clear log", fileMenu)
        fileMenu.addAction(self.clearLog)
        self.clearLog.triggered.connect(self.clearLogTriggered)
        # Extended options
        self.displayArgusOptions = QAction("Advanced options", fileMenu)
        fileMenu.addAction(self.displayArgusOptions)
        self.displayArgusOptions.triggered.connect(
            self.displayArgusOptionsTriggered)
        # Exit
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
        self.enableRtspServer = QAction("RTSP Server", self, checkable=True)
        if config.bc.dev_mode():
            videoMenu.addAction(self.enableRtspServer)
            self.enableRtspServer.triggered.connect(
                self.enableRtspServerTriggered)

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
        if widget is None:
            return
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

            self.top_widget = TopWidget(ac=self.ac, aname="top", parent=self)
            self.ac.top_widget = self.top_widget
            layout.addWidget(self.top_widget)

            self.tab_widget = QTabWidget()
            for tab_name, tab in self.ac.tabs.items():
                self.tab_widget.addTab(tab, tab_name)
            layout.addWidget(self.tab_widget)

            return layout

        layout.addLayout(left())
        layout.addWidget(self.video_widget)

        self.central_widget = QWidget()
        self.central_widget.setLayout(layout)
        self.setCentralWidget(self.central_widget)
        self.createMenuBar()
        self.showMaximized()

    def _poll_misc(self):
        pass

    def post_ui_init(self):
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

    def clearLogTriggered(self):
        self.ac.mainTab.clear_log()

    def displayAdvancedMovementTriggered(self):
        self.ac.mainTab.motion_widget.show_advanced_movement(
            bool(self.displayAdvancedMovement.isChecked()))

    def displayArgusOptionsTriggered(self):
        self.argus_options_window.show()

    def enableRtspServerTriggered(self):
        self.ac.vidpip.enable_rtsp_server(
            bool(self.enableRtspServer.isChecked()))


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
