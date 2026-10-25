#!/usr/bin/env -S uv run

# Load Matplotlib before Qt and GStreamer. Mixing uv-managed Matplotlib with
# distribution-provided native GUI bindings is sensitive to initialization
# order and can otherwise abort while importing pyplot.
import matplotlib.pyplot as plt  # noqa: F401

from uscope.gui.gstwidget import gstwidget_main
from uscope.util import add_bool_arg
from uscope.gui.widgets import AMainWindow
from uscope import config
from uscope import app_log
from uscope.gui.input_widget import InputWidget

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import copy
import os
import traceback
import threading

from uscope.gui.common import ArgusCommon, error

from uscope.gui.widgets import TopWidget, BatchImageTab, StitchingTab, MeasureTab
from uscope.gui.imaging import MainTab, PanoramaTab, ImagerTab
from uscope.gui.machine import MachineTab
from uscope.gui.scripting import ScriptingTab
from uscope.gui.instruments import InstrumentsTab
from uscope.instrument_db import InstrumentDB
from uscope.gui.calibration import CalibrationTab, OpticsTab
from uscope.gui.objectives import ObjectivesTab
from uscope.gui.minimap import MinimapTab
from uscope.gui.snapshot_viewer import SnapshotViewerTab


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
        layout.addStretch()
        general = QWidget()
        general.setLayout(layout)

        self.tabs = QTabWidget()
        self.tabs.addTab(general, "General")
        self.tabs.addTab(self.mw.objectivesTab, "Objectives")
        self.tabs.addTab(self.mw.opticsTab, "Optics")
        self.tabs.addTab(self.mw.calibrationTab, "Calibration")
        self.tabs.addTab(self.mw.instrumentsTab, "Instruments")
        top = QVBoxLayout()
        top.addWidget(self.tabs)
        self.setLayout(top)
        self.setWindowTitle("Advanced options")
        self.resize(1100, 700)

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

        # Some window managers drop a maximize request made before the
        # window is mapped. Request it up front and again once events run
        self.setWindowState(self.windowState() | Qt.WindowMaximized)
        self.show()
        QTimer.singleShot(250, self.ensure_maximized)

    def ensure_maximized(self):
        if self.isMaximized() or self.isFullScreen():
            return
        screen = QApplication.desktop().availableGeometry(self)
        hint = self.minimumSizeHint()
        if hint.width() > screen.width() or hint.height() > screen.height():
            self.ac.log(
                "WARNING: window minimum size %ux%u exceeds screen %ux%u, can't maximize"
                % (hint.width(), hint.height(), screen.width(),
                   screen.height()))
        self.showMaximized()

    def expertViewTriggered(self):
        """
        Show / hide tabs and options most users don't need
        (QTabWidget.setTabVisible() is Qt 5.15+ => remove / re-insert)
        """
        expert = self.expertView.isChecked()
        self.enableRtspServer.setVisible(expert)
        self.displayAdvancedMovement.setVisible(expert)
        self.displayAdvancedMovementTriggered()
        all_tabs = list(self.ac.tabs.items())
        for name, tab in all_tabs:
            if tab not in (self.machineTab, self.batchTab, self.scriptingTab,
                           self.stitchingTab):
                continue
            index = self.tab_widget.indexOf(tab)
            if expert and index < 0:
                # Restore original position relative to tabs still shown
                insert_at = 0
                for _name, other in all_tabs:
                    if other is tab:
                        break
                    if self.tab_widget.indexOf(other) >= 0:
                        insert_at += 1
                self.tab_widget.insertTab(insert_at, tab, name)
            elif not expert and index >= 0:
                self.tab_widget.removeTab(index)

    def _cache_load(self, j):
        j = j.get("main_window", {})
        self.expertView.setChecked(j.get("expert_view", config.bc.dev_mode()))
        self.expertViewTriggered()
        self.displayLimits.setChecked(
            j.get("display_limits", config.bc.dev_mode()))
        self.displayLimitsTriggered()
        self.displayAdvancedMovement.setChecked(
            j.get("display_advanced_movement", False))
        self.displayAdvancedMovementTriggered()
        self.displayAdvancedObjective.setChecked(
            j.get("display_advanced_objective", False))
        self.displayAdvancedObjectiveTriggered()
        self.argus_options_window.cache_load(j)

    def _cache_save(self, j):
        j = j.setdefault("main_window", {})
        j["expert_view"] = self.expertView.isChecked()
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
        # PanoramaTab uses Main's imaging widget
        self.ac.mainTab = self.mainTab
        self.panoramaTab = self.add_tab(PanoramaTab, "Panorama")
        self.ac.panoramaTab = self.panoramaTab
        self.imagerTab = self.add_tab(ImagerTab, "Imager")
        self.ac.imagerTab = self.imagerTab
        self.machineTab = self.add_tab(MachineTab, "Machine")
        self.measureTab = self.add_tab(MeasureTab, "Measure")
        self.minimapTab = self.add_tab(MinimapTab, "Minimap")
        self.snapshotsTab = self.add_tab(SnapshotViewerTab, "Snapshots")
        self.batchTab = self.add_tab(BatchImageTab, "Batch")
        self.scriptingTab = self.add_tab(ScriptingTab, "Scripting")
        self.stitchingTab = self.add_tab(StitchingTab, "CloudStitch")

        # Display order (independent of construction order above)
        display_order = [
            self.mainTab,
            self.snapshotsTab,
            self.measureTab,
            self.minimapTab,
            self.panoramaTab,
            self.imagerTab,
            self.machineTab,
            self.batchTab,
            self.scriptingTab,
            self.stitchingTab,
        ]
        name_by_tab = {tab: name for name, tab in self.ac.tabs.items()}
        self.ac.tabs = {name_by_tab[tab]: tab for tab in display_order}

        # FIXME: hack, come up with something more intuitive
        self.ac.scriptingTab = self.scriptingTab
        self.ac.stitchingTab = self.stitchingTab
        self.ac.batchTab = self.batchTab

        # Instrument name => ScriptingTab
        self.instrumentTabs = {}
        self.instrument_db = InstrumentDB.for_microscope(self.ac.microscope)
        for entry in self.instrument_db.instances.values():
            if entry["enabled"]:
                self.instrument_tab_add(entry)

        # Shown in File => Advanced options, not as main tabs
        self.objectivesTab = ObjectivesTab(ac=self.ac,
                                           aname="objectives",
                                           parent=self)
        self.opticsTab = OpticsTab(ac=self.ac, aname="optics", parent=self)
        self.calibrationTab = CalibrationTab(ac=self.ac,
                                             aname="calibration",
                                             parent=self)
        self.instrumentsTab = InstrumentsTab(ac=self.ac,
                                             aname="instruments",
                                             parent=self)
        self.argus_options_window = ArgusOptionsWindow(self)

    def builtin_tab_names(self):
        instrument_tabs = list(self.instrumentTabs.values())
        return [
            name for name, tab in self.ac.tabs.items()
            if tab not in instrument_tabs
        ]

    def instrument_tab_add(self, entry, live=False):
        """
        Create an instrument tab from an InstrumentDB entry
        live: the GUI is already running (otherwise startup does the init)
        """
        config = dict(entry)
        config["plugin_path"] = self.instrument_db.resolve_path(entry) or ""
        tab = ScriptingTab(ac=self.ac,
                           aname="instrument:" + entry["name"],
                           parent=self,
                           instrument_config=config)
        self.instrumentTabs[entry["name"]] = tab
        if not entry["visible"]:
            # Not in a layout => must hide or it draws over the main window
            tab.hide()
        else:
            # Insert after any other instrument tabs, before Batch
            tabs = list(self.ac.tabs.items())
            index = [t for _name, t in tabs].index(self.batchTab)
            tabs.insert(index, (entry["tab_name"], tab))
            self.ac.tabs = dict(tabs)
        if live:
            # Not initUI(): that also re-runs ArgusCommon.initUI()
            tab._initUI()
            if entry["visible"]:
                insert_at = 0
                for _name, other in self.ac.tabs.items():
                    if other is tab:
                        break
                    if self.tab_widget.indexOf(other) >= 0:
                        insert_at += 1
                self.tab_widget.insertTab(insert_at, tab, entry["tab_name"])
            tab._post_ui_init()
        return tab

    def instrument_tab_remove(self, name):
        """
        Plugin should already be stopped
        """
        tab = self.instrumentTabs.pop(name)
        tab.teardown()
        index = self.tab_widget.indexOf(tab)
        if index >= 0:
            self.tab_widget.removeTab(index)
        self.ac.tabs = {k: v for k, v in self.ac.tabs.items() if v is not tab}
        del self.awidgets["instrument:" + name]
        tab.deleteLater()

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
        self.expertView = QAction("Expert view", fileMenu, checkable=True)
        self.expertView.setToolTip(
            "Show Machine, Batch, Scripting and CloudStitch tabs, "
            "RTSP server and advanced movement")
        fileMenu.addAction(self.expertView)
        self.expertView.setChecked(config.bc.dev_mode())
        self.expertView.triggered.connect(self.expertViewTriggered)
        # Applied by _cache_load() once all widgets exist
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
        self.manageObjectives = QAction("Manage objectives…", self)
        videoMenu.addAction(self.manageObjectives)
        self.manageObjectives.triggered.connect(self.manageObjectivesTriggered)
        # Expert view only
        self.enableRtspServer = QAction("RTSP Server", self, checkable=True)
        videoMenu.addAction(self.enableRtspServer)
        self.enableRtspServer.triggered.connect(self.enableRtspServerTriggered)
        self.createCaptureFormatMenu(videoMenu)

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
        # Advanced movement (expert view only)
        self.displayAdvancedMovement = QAction("Advanced movement",
                                               motionMenu,
                                               checkable=True)
        motionMenu.addAction(self.displayAdvancedMovement)
        self.displayAdvancedMovement.setChecked(False)
        self.displayAdvancedMovement.triggered.connect(
            self.displayAdvancedMovementTriggered)

        # Help menu
        helpMenu = menuBar.addMenu("Help")
        helpMenu.addAction(self.helpContentAction)
        self.diagnosticInfoAction = QAction("Diagnostic info (brief)", self)
        self.diagnosticInfoAction.triggered.connect(
            lambda: self.diagnostic_info(verbose=False))
        helpMenu.addAction(self.diagnosticInfoAction)
        self.diagnosticInfoVerboseAction = QAction(
            "Diagnostic info (verbose)", self)
        self.diagnosticInfoVerboseAction.triggered.connect(
            lambda: self.diagnostic_info(verbose=True))
        helpMenu.addAction(self.diagnosticInfoVerboseAction)
        helpMenu.addAction(self.aboutAction)

        self.exitAction.triggered.connect(self.close)
        self.helpContentAction.triggered.connect(self.helpContent)
        self.aboutAction.triggered.connect(self.about)

    def createCaptureFormatMenu(self, videoMenu):
        """
        Video => Capture format
        Select the pixel format negotiated with the camera (8 bit RGB, 16 bit, raw)
        Overrides microscope.j5 imager.format and persists per camera S/N
        Only shown if the source supports more than one format
        """
        from uscope.imager import pixel_format

        self.captureFormatMenu = None
        self.captureFormatActions = {}
        formats = self.ac.vidpip.supported_formats()
        if not formats or len(formats) < 2:
            return

        self.captureFormatMenu = videoMenu.addMenu("Capture format")
        self.captureFormatGroup = QActionGroup(self)
        self.captureFormatGroup.setExclusive(True)
        for fmt in formats:
            action = QAction(pixel_format.label(fmt), self, checkable=True)
            action.setData(fmt)
            self.captureFormatGroup.addAction(action)
            self.captureFormatMenu.addAction(action)
            self.captureFormatActions[fmt] = action
        self.captureFormatGroup.triggered.connect(
            self.captureFormatTriggered)
        self.captureFormatMenu.addSeparator()
        self.captureFormatDefault = QAction("Use microscope default", self)
        self.captureFormatMenu.addAction(self.captureFormatDefault)
        self.captureFormatDefault.triggered.connect(
            self.captureFormatDefaultTriggered)

        self.ac.captureFormatChanged.connect(self.captureFormatChanged)

    def captureFormatPostUiInit(self):
        """
        Camera is now running: reflect what it actually supports
        """
        if not self.captureFormatMenu:
            return
        supported = self.ac.imager.supported_capture_formats()
        for fmt, action in self.captureFormatActions.items():
            action.setEnabled(fmt in supported)
        self.captureFormatChanged(self.ac.imager.get_capture_format())

    def captureFormatChanged(self, fmt):
        if not self.captureFormatMenu:
            return
        for afmt, action in self.captureFormatActions.items():
            action.setChecked(afmt == fmt)

    def captureFormatTriggered(self, action):
        fmt = action.data()
        try:
            self.ac.apply_capture_format_override(fmt)
        except (ValueError, RuntimeError) as e:
            self.ac.log(f"Failed to set capture format {fmt}: {e}")
            # Snap menu back to reality
            self.captureFormatChanged(self.ac.imager.get_capture_format())

    def captureFormatDefaultTriggered(self):
        try:
            self.ac.apply_capture_format_override(None)
        except (ValueError, RuntimeError) as e:
            self.ac.log(f"Failed to restore default capture format: {e}")
        self.captureFormatChanged(self.ac.imager.get_capture_format())

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

    def diagnostic_info(self, verbose=False):
        """
        Some things take a while and/or need to be sequenced
        Gather up all GUI state we can and pass off to another thread to actually print
        """

        self.ac.log("")
        self.ac.log("")
        self.ac.log("")
        self.ac.log("Gathering diagnostic info")
        # This has a lot of misc info
        try:
            scan_config = self.panoramaTab.active_planner_widget(
            ).get_current_planner_hconfig()
        except Exception:
            scan_config = {}
            self.ac.log("Exception getting planner config")

        imager_state = {}
        imager_state["sn"] = self.ac.microscope.imager.get_sn()
        imager_state["prop_cache"] = self.ac.imager_properties.get_prop_cache()

        j = {
            "argus_cachej": copy.deepcopy(self.cachej),
            "scan_config": scan_config,
            "imager_state": imager_state,
            "verbose": verbose,
        }
        self.ac.task_thread.diagnostic_info(j)

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

    def _poll_misc(self):
        pass

    def post_ui_init(self):
        # Start services
        # This will microscope.configure() which is needed by later tabs
        self.ac.post_ui_init()

        # Tabs
        for tab in self.awidgets.values():
            tab.post_ui_init()

        self.captureFormatPostUiInit()

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
        self.ac.panoramaTab.show_minmax(bool(self.displayLimits.isChecked()))

    def clearLogTriggered(self):
        self.ac.mainTab.clear_log()

    def displayAdvancedMovementTriggered(self):
        self.ac.mainTab.motion_widget.show_advanced_movement(
            self.expertView.isChecked()
            and bool(self.displayAdvancedMovement.isChecked()))

    def displayArgusOptionsTriggered(self):
        self.argus_options_window.show()

    def manageObjectivesTriggered(self):
        window = self.argus_options_window
        window.tabs.setCurrentWidget(self.objectivesTab)
        window.show()
        window.raise_()

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
        app_log.install(os.path.join(config.get_bc().get_data_dir(),
                                     "log.txt"))
        app_log.install_qt_message_handler()
        gstwidget_main(MainWindow, parse_args=parse_args)
    except Exception as e:
        traceback.print_exc()
        error(str(e))


if __name__ == '__main__':
    main()
