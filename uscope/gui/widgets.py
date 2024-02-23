from uscope.app.argus.threads import StitcherThread
from uscope import config
from uscope.util import readj, writej
from collections import OrderedDict
from uscope.cloud_stitch import CSInfo
from uscope.imager.autofocus import AutoStacker
from uscope.gui.common import ArgusShutdown
from uscope.imager.imager_util import format_mm_3dec

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import time
import datetime
import os.path
import math
from enum import Enum
import copy
import traceback
import json
import json5
"""
Argus Widget
"""


class AMainWindow(QMainWindow):
    def __init__(self):
        QMainWindow.__init__(self)
        self.awidgets = OrderedDict()
        self.polli = 0
        self.ac = None
        self.shutting_down = False
        self.cachej = {}

    def __del__(self):
        self.shutdown()

    def add_awidget(self, name, awidget):
        assert name not in self.awidgets, name
        assert name
        assert awidget
        self.awidgets[name] = awidget

    def _initUI(self):
        pass

    def initUI(self):
        """
        Called to initialize GUI elements
        """
        self._initUI()
        for awidget in self.awidgets.values():
            awidget.initUI()

    def _post_ui_init(self):
        pass

    def post_ui_init(self):
        """
        Called after all GUI elements are instantiated
        """
        self._post_ui_init()
        for awidget in self.awidgets.values():
            awidget.post_ui_init()

    def _shutdown_request(self):
        pass

    def _shutdown_join(self):
        pass

    def shutdown(self):
        # Concern multiple closing events may fight
        if self.shutting_down:
            return
        self.shutting_down = True

        # FIXME: make this an AWidget
        if self.fullscreen_widget:
            self.fullscreen_widget.close()

        self.cache_save()
        self._shutdown_request()
        for awidget in self.awidgets.values():
            awidget.shutdown_request()
        if self.ac:
            self.ac.shutdown_request()
        self._shutdown_join()
        for awidget in self.awidgets.values():
            awidget.shutdown_join()
        if self.ac:
            self.ac.shutdown_join()

    def closeEvent(self, event):
        self.shutdown()

    def _cache_save(self, cachej):
        pass

    def _cache_sn_save(self, cachej):
        pass

    def cache_get_set_sn_entry(self, cachej):
        ret = cachej.setdefault("microscopes", {}).setdefault(
            self.ac.microscope.model_serial_string(), {})
        if "model" not in ret:
            ret["model"] = self.ac.microscope.model()
        if "serial" not in ret:
            ret["serial"] = self.ac.microscope.serial()
        return ret

    def cache_save(self):
        """
        Called when saving GUI state to file
        Add your state to JSON object j
        """
        if not self.ac:
            return
        # Recycle old value
        # otherwise we can loose some config
        # ex: carry over joystick config when not plugged in
        cachej = self.cachej

        # Full structure
        self.ac.microscope.cache_save(cachej)
        self._cache_save(cachej)
        for awidget in self.awidgets.values():
            awidget.cache_save(cachej)

        # S/N specific
        cachej_sn = self.cache_get_set_sn_entry(cachej)
        self.ac.microscope.cache_sn_save(cachej_sn)
        self._cache_sn_save(cachej_sn)
        for awidget in self.awidgets.values():
            awidget.cache_sn_save(cachej_sn)

        fn = self.ac.aconfig.cache_fn()
        # file getting corrupted on save
        # https://github.com/Labsmore/pyuscope/issues/366
        # Be absolutely sure we have a good file before saving
        fn_tmp = fn + ".tmp"
        with open(fn_tmp, "w") as f:
            json.dump(cachej,
                      f,
                      sort_keys=True,
                      indent=4,
                      separators=(",", ": "))
        os.rename(fn_tmp, fn)

    def _cache_load(self, cachej):
        pass

    def _cache_sn_load(self, cachej):
        pass

    def cache_load(self):
        """
        Called when loading GUI state from file
        Read your state from JSON object j
        """
        fn = self.ac.aconfig.cache_fn()
        cachej = {}
        if os.path.exists(fn):
            try:
                with open(fn, "r") as f:
                    cachej = json5.load(f)
            except Exception as e:
                print("Invalid configuration cache. Ignoring", e)

        # Full
        self.ac.microscope.cache_load(cachej)
        self._cache_load(cachej)
        for awidget in self.awidgets.values():
            awidget.cache_load(cachej)

        # S/N specific
        cachej_sn = self.cache_get_set_sn_entry(cachej)
        self.ac.microscope.cache_sn_load(cachej_sn)
        self._cache_sn_load(cachej_sn)
        for awidget in self.awidgets.values():
            awidget.cache_sn_load(cachej_sn)

        self.cachej = cachej

    def _poll_misc(self):
        pass

    def poll_misc(self):
        self.polli += 1

        motion_thread = self.ac.motion_thread
        # deleted during shutdown => can lead to crash during shutdown
        if motion_thread:
            motion_thread.update_pos_cache()

        # FIXME: maybe better to do this with events
        # Loose the log window on shutdown...should log to file?
        try:
            self.ac.poll_misc()
        except ArgusShutdown:
            print(traceback.format_exc())
            self.shutdown()
            QCoreApplication.exit(1)
            return

        self._poll_misc()
        for awidget in self.awidgets.values():
            awidget.poll_misc()

        # Save ocassionally / once 3 seconds
        if self.polli % 15 == 0:
            self.cache_save()

    def _update_pconfig(self, pconfig):
        pass

    def update_pconfig(self, pconfig):
        self._update_pconfig(pconfig)
        for awidget in self.awidgets.values():
            awidget.update_pconfig(pconfig)


# TODO: register events in lieu of callbacks
class AWidget(QWidget):
    def __init__(self, ac, aname=None, parent=None):
        """
        Low level objects should be instantiated here
        """
        super().__init__(parent=parent)
        self.ac = ac
        self.awidgets = OrderedDict()
        if aname is not None:
            parent.add_awidget(aname, self)

    def add_awidget(self, name, awidget):
        assert name not in self.awidgets, name
        assert name
        assert awidget
        self.awidgets[name] = awidget

    def _initUI(self):
        pass

    def initUI(self):
        """
        Called to initialize GUI elements
        """
        self.ac.initUI()
        self._initUI()
        for awidget in self.awidgets.values():
            awidget.initUI()

    def _post_ui_init(self):
        pass

    def post_ui_init(self):
        """
        Called after all GUI elements are instantiated
        """
        self._post_ui_init()
        for awidget in self.awidgets.values():
            awidget.post_ui_init()

    def _shutdown_request(self):
        pass

    def shutdown_request(self):
        """
        Called when GUI is shutting down
        """
        self._shutdown_request()
        for awidget in self.awidgets.values():
            awidget.shutdown_request()

    def _shutdown_join(self):
        pass

    def shutdown_join(self):
        self._shutdown_join()
        for awidget in self.awidgets.values():
            awidget.shutdown_join()

    def _cache_save(self, cachej):
        pass

    def cache_save(self, cachej):
        """
        Called when saving GUI state to file
        Add your state to JSON object j
        """
        self._cache_save(cachej)
        for awidget in self.awidgets.values():
            awidget.cache_save(cachej)

    def _cache_load(self, cachej):
        pass

    def cache_load(self, cachej):
        """
        Called when loading GUI state from file
        Read your state from JSON object j
        """
        self._cache_load(cachej)
        for awidget in self.awidgets.values():
            awidget.cache_load(cachej)

    def _cache_sn_save(self, cachej):
        pass

    def cache_sn_save(self, cachej):
        """
        Called when saving GUI state to file
        Add your state to JSON object j
        """
        self._cache_sn_save(cachej)
        for awidget in self.awidgets.values():
            awidget.cache_sn_save(cachej)

    def _cache_sn_load(self, cachej):
        pass

    def cache_sn_load(self, cachej):
        """
        Called when loading GUI state from file
        Read your state from JSON object j
        """
        self._cache_sn_load(cachej)
        for awidget in self.awidgets.values():
            awidget.cache_sn_load(cachej)

    def _poll_misc(self):
        pass

    def poll_misc(self):
        self._poll_misc()
        for awidget in self.awidgets.values():
            awidget.poll_misc()

    def _update_pconfig(self, pconfig):
        pass

    def update_pconfig(self, pconfig):
        self._update_pconfig(pconfig)
        for awidget in self.awidgets.values():
            awidget.update_pconfig(pconfig)


class ArgusTab(AWidget):
    pass


'''
# TODO: try using this to simplify some UI elements
# https://stackoverflow.com/questions/52615115/how-to-create-collapsible-box-in-pyqt
class CollapsibleBox(QWidget):
    def __init__(self, title="", parent=None):
        super(CollapsibleBox, self).__init__(parent)

        self.toggle_button = QToolButton(
            text=title, checkable=True, checked=False
        )
        self.toggle_button.setStyleSheet("QToolButton { border: none; }")
        self.toggle_button.setToolButtonStyle(
            Qt.ToolButtonTextBesideIcon
        )
        self.toggle_button.setArrowType(Qt.RightArrow)
        self.toggle_button.pressed.connect(self.on_pressed)

        self.toggle_animation = QParallelAnimationGroup(self)

        self.content_area = QScrollArea(
            maximumHeight=0, minimumHeight=0
        )
        self.content_area.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        self.content_area.setFrameShape(QFrame.NoFrame)

        lay = QVBoxLayout(self)
        lay.setSpacing(0)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.toggle_button)
        lay.addWidget(self.content_area)

        self.toggle_animation.addAnimation(
            QPropertyAnimation(self, b"minimumHeight")
        )
        self.toggle_animation.addAnimation(
            QPropertyAnimation(self, b"maximumHeight")
        )
        self.toggle_animation.addAnimation(
            QPropertyAnimation(self.content_area, b"maximumHeight")
        )

    @pyqtSlot()
    def on_pressed(self):
        checked = self.toggle_button.isChecked()
        self.toggle_button.setArrowType(
            Qt.DownArrow if not checked else Qt.RightArrow
        )
        self.toggle_animation.setDirection(
            QAbstractAnimation.Forward
            if not checked
            else QAbstractAnimation.Backward
        )
        self.toggle_animation.start()

    def setContentLayout(self, layout):
        lay = self.content_area.layout()
        del lay
        self.content_area.setLayout(layout)
        collapsed_height = (
            self.sizeHint().height() - self.content_area.maximumHeight()
        )
        content_height = layout.sizeHint().height()
        for i in range(self.toggle_animation.animationCount()):
            animation = self.toggle_animation.animationAt(i)
            animation.setDuration(500)
            animation.setStartValue(collapsed_height)
            animation.setEndValue(collapsed_height + content_height)

        content_animation = self.toggle_animation.animationAt(
            self.toggle_animation.animationCount() - 1
        )
        content_animation.setDuration(500)
        content_animation.setStartValue(0)
        content_animation.setEndValue(content_height)
'''
"""
Select objective and show FoV
"""


class BatchImageTab(ArgusTab):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pconfig_sources = []

    def _initUI(self):
        self.layout = QVBoxLayout()

        # TODO: we should also support script runs

        self.add_pb = QPushButton("Add current scan")
        self.layout.addWidget(self.add_pb)
        self.add_pb.clicked.connect(self.add_clicked)

        self.del_pb = QPushButton("Delete selected scan")
        self.layout.addWidget(self.del_pb)
        self.del_pb.clicked.connect(self.del_clicked)

        self.del_all_pb = QPushButton("Delete all scans")
        self.layout.addWidget(self.del_all_pb)
        self.del_all_pb.clicked.connect(self.del_all_clicked)

        self.run_all_pb = QPushButton("Run all scans")
        self.layout.addWidget(self.run_all_pb)
        self.run_all_pb.clicked.connect(self.run_all_clicked)

        label = QLabel("Abort on first failure?")
        self.layout.addWidget(label)
        self.abort_cb = QCheckBox()
        self.layout.addWidget(self.abort_cb)
        label.setVisible(False)
        self.abort_cb.setVisible(False)

        # FIXME: allow editing scan parameters
        """
        self.layout.addWidget(QLabel("Output directory"))
        self.output_dir = QLineEdit()
        self.output_dir.setReadOnly(True)
        """

        # Which tab to get config from
        # In advanced setups multiple algorithms are possible
        #label = QLabel("Planner config source")
        #self.layout.addWidget(label)
        #self.pconfig_source_cb = QComboBox()
        #self.layout.addWidget(self.pconfig_source_cb)
        #label.setVisible(False)
        #self.pconfig_source_cb.setVisible(False)

        def load_save_layout():
            layout = QHBoxLayout()

            self.load_config_pb = QPushButton("Load config")
            self.load_config_pb.clicked.connect(self.load_pb_clicked)
            layout.addWidget(self.load_config_pb)

            self.save_config_pb = QPushButton("Save config")
            self.save_config_pb.clicked.connect(self.save_pb_clicked)
            layout.addWidget(self.save_config_pb)

            return layout

        self.layout.addLayout(load_save_layout())

        self.pconfig_cb = QComboBox()
        self.layout.addWidget(self.pconfig_cb)
        self.pconfig_cb.currentIndexChanged.connect(self.update_state)

        self.display = QTextEdit()
        self.display.setReadOnly(True)
        self.layout.addWidget(self.display)
        self.display.setVisible(self.ac.bc.dev_mode())

        self.setLayout(self.layout)

        self.scan_configs = []
        self.scani = 0

        self.batch_cache_load()

    def abort_on_failure(self):
        return self.abort_cb.isChecked()

    def add_pconfig_source(self, widget, name):
        self.pconfig_sources.append(widget)
        #self.pconfig_source_cb.addItem(name)

    def update_state(self):
        if not len(self.scan_configs):
            self.display.setText("None")
        else:
            index = self.pconfig_cb.currentIndex()
            scan_config = self.scan_configs[index]
            s = json.dumps(scan_config,
                           sort_keys=True,
                           indent=4,
                           separators=(",", ": "))
            self.display.setPlainText(s)
        self.batch_cache_save()

    def get_scan_config(self):
        #mainTab = self.pconfig_sources[self.pconfig_source_cb.currentIndex()]
        assert len(self.pconfig_sources) == 1
        mainTab = self.pconfig_sources[0]
        return mainTab.active_planner_widget().get_current_scan_config()

    def add_cb(self, scan_config):
        self.scani += 1
        self.pconfig_cb.addItem(
            f"Job # {self.scani}: " +
            os.path.basename(scan_config["out_dir_config"]["user_basename"]))

    def add_clicked(self):
        scan_config = self.get_scan_config()
        self.add_cb(scan_config)
        self.scan_configs.append(scan_config)
        self.update_state()

    def del_clicked(self):
        ret = QMessageBox.question(self, "Delete scan",
                                   "Delete selected batch job?",
                                   QMessageBox.Yes | QMessageBox.Cancel,
                                   QMessageBox.Cancel)
        if ret != QMessageBox.Yes:
            return

        if len(self.scan_configs):
            index = self.pconfig_cb.currentIndex()
            del self.scan_configs[index]
            self.pconfig_cb.removeItem(index)
        self.update_state()

    def del_all(self):
        for _i in range(len(self.scan_configs)):
            del self.scan_configs[0]
            self.pconfig_cb.removeItem(0)
        self.scani = 0
        self.update_state()

    def del_all_clicked(self):
        ret = QMessageBox.question(self, "Delete all",
                                   "Delete all batch jobs?",
                                   QMessageBox.Yes | QMessageBox.Cancel,
                                   QMessageBox.Cancel)
        if ret != QMessageBox.Yes:
            return
        self.del_all()

    def run_all_clicked(self):
        ret = QMessageBox.question(self, "Start scans?", "Start scans?",
                                   QMessageBox.Yes | QMessageBox.Cancel,
                                   QMessageBox.Cancel)
        if ret != QMessageBox.Yes:
            return

        self.ac.mainTab.imaging_widget.go_planner_hconfigs(self.scan_configs)

    def load_pb_clicked(self):
        directory = self.ac.bc.batch_data_dir()
        filename = QFileDialog.getOpenFileName(None,
                                               "Select input batch config",
                                               directory,
                                               "Batch config (*.json *.j5)")
        if not filename:
            return
        filename = str(filename[0])
        if not filename:
            return
        try:
            j = readj(filename)
            self.del_all()
            self.loadj(j)
        except Exception as e:
            self.ac.log(f"Failed to load script config: {type(e)}: {e}")
            traceback.print_exc()

    def save_pb_clicked(self):
        directory = self.ac.bc.batch_data_dir()
        default_filename = datetime.datetime.utcnow().isoformat().replace(
            'T', '_').replace(':', '-').split('.')[0] + ".batch.json"
        directory = os.path.join(directory, default_filename)
        filename = QFileDialog.getSaveFileName(None,
                                               "Select output batch config",
                                               directory,
                                               "Batch config (*.json *.j5)")
        if not filename:
            return
        filename = str(filename[0])
        writej(filename, self.scan_configs)

    def batch_cache_save(self):
        s = json.dumps(self.scan_configs,
                       sort_keys=True,
                       indent=4,
                       separators=(",", ": "))
        with open(self.ac.aconfig.batch_cache_fn(), "w") as f:
            f.write(s)

    def loadj(self, j):
        self.scan_configs = list(j)
        for scan_config in self.scan_configs:
            self.add_cb(scan_config)
        self.update_state()

    def batch_cache_load(self):
        fn = self.ac.aconfig.batch_cache_fn()
        if not os.path.exists(fn):
            return
        with open(fn, "r") as f:
            j = json5.load(f)
        self.loadj(j)


class AdvancedTab(ArgusTab):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        layout = QGridLayout()
        row = 0

        def stack_gb():
            layout = QGridLayout()
            row = 0
            """
            Some quick tests around 20x indicated +/- 0.010 w/ 2 um steps is good
            """

            layout.addWidget(QLabel("Mode"), row, 0)
            self.stack_cb = QComboBox()
            layout.addWidget(self.stack_cb, row, 1)
            self.stack_cb.addItem("A: None")
            self.stack_cb.addItem("B: Manual")
            self.stack_cb.addItem("C: die normal")
            self.stack_cb.addItem("D: die double distance")
            self.stack_cb.addItem("E: die double steps")
            row += 1

            layout.addWidget(QLabel("Stack drift correction?"), row, 0)
            self.stack_drift_cb = QCheckBox()
            layout.addWidget(self.stack_drift_cb, row, 1)
            row += 1

            layout.addWidget(QLabel("+/- each side distance"), row, 0)
            self.stacker_distance_le = QLineEdit("")
            layout.addWidget(self.stacker_distance_le, row, 1)
            row += 1

            # Set to non-0 to activate
            layout.addWidget(QLabel("+/- each side snapshots (+1 center)"),
                             row, 0)
            self.stacker_number_le = QLineEdit("")
            layout.addWidget(self.stacker_number_le, row, 1)
            row += 1

            gb = QGroupBox("Stacking")
            gb.setLayout(layout)
            return gb

        layout.addWidget(QLabel("Image stabilization"), row, 0)
        self.image_stabilization_cb = QComboBox()
        self.image_stabilization_cb_map = {
            0: 1,
            1: 3,
            2: 9,
            3: 27,
            4: 100,
            5: 1000,
        }
        self.image_stabilization_cb.addItem("1 (off)")
        self.image_stabilization_cb.addItem("3")
        self.image_stabilization_cb.addItem("9")
        self.image_stabilization_cb.addItem("27")
        self.image_stabilization_cb.addItem("100")
        self.image_stabilization_cb.addItem("1000")
        layout.addWidget(self.image_stabilization_cb, row, 1)
        row += 1

        if self.ac.microscope.has_z():
            layout.addWidget(stack_gb(), row, 0)
            row += 1

        # FIXME: display for now, but should make editable
        # Or maybe have it log a report instead of making widgets?

        self.diagnostic_info_pb = QPushButton("Diagnostic info (brief)")
        self.diagnostic_info_pb.clicked.connect(self.diagnostic_info)
        layout.addWidget(self.diagnostic_info_pb, row, 0)
        row += 1

        self.diagnostic_info_pb = QPushButton("Diagnostic info (verbose)")
        self.diagnostic_info_pb.clicked.connect(self.diagnostic_info_verbose)
        layout.addWidget(self.diagnostic_info_pb, row, 0)
        row += 1

        self.setLayout(layout)

    def diagnostic_info_verbose(self):
        self.diagnostic_info(verbose=True)

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
            scan_config = self.ac.mainTab.active_planner_widget(
            ).get_current_scan_config()
        except:
            scan_config = {}
            self.ac.log("Exception getting planner config")

        imager_state = {}
        imager_state["sn"] = self.ac.microscope.imager.get_sn()
        imager_state["prop_cache"] = self.ac.control_scroll.get_prop_cache()

        j = {
            "argus_cachej": copy.deepcopy(self.ac.mw.cachej),
            "scan_config": scan_config,
            "imager_state": imager_state,
            "verbose": verbose,
        }
        self.ac.task_thread.diagnostic_info(j)

    def update_pconfig_stack(self, pconfig):
        images_pm = int(str(self.stacker_number_le.text()))
        distance_pm = float(self.stacker_distance_le.text())
        if not images_pm or distance_pm == 0.0:
            return
        # +/- but always add the center plane
        images_per_stack = 1 + 2 * images_pm
        pconfig["points-stacker"] = {
            "number": images_per_stack,
            "distance": 2 * distance_pm,
        }
        if self.stack_drift_cb.isChecked():
            pconfig["stacker-drift"] = {}

    def get_image_stablization(self):
        return self.image_stabilization_cb_map[
            self.image_stabilization_cb.currentIndex()]

    def _update_pconfig(self, pconfig):
        image_stabilization = self.get_image_stablization()
        if image_stabilization > 1:
            pconfig["image-stabilization"] = {
                "n": image_stabilization,
            }

        if self.ac.microscope.has_z():
            self.update_pconfig_stack(pconfig)

    def image_stacking_enabled(self):
        images_pm = int(str(self.stacker_number_le.text()))
        distance_pm = float(self.stacker_distance_le.text())
        if not images_pm or distance_pm == 0.0:
            return False
        else:
            return True

    def image_stacking_pm_n(self):
        return int(str(self.stacker_number_le.text()))

    def image_stablization_enabled(self):
        return self.get_image_stablization() > 1

    def _post_ui_init(self):
        if self.ac.microscope.has_z():
            self.ac.objectiveChanged.connect(self.update_stack_mode)
            self.stack_cb.currentIndexChanged.connect(self.update_stack_mode)
            self.update_stack_mode()

    def _cache_save(self, cachej):
        j = {
            "image_stabilization": self.image_stabilization_cb.currentIndex(),
        }
        if self.ac.microscope.has_z():
            j["stacking"] = {
                "images_pm": self.stacker_number_le.text(),
                "distance_pm": self.stacker_distance_le.text(),
                "mode_index": self.stack_cb.currentIndex(),
                "drift_correction": self.stack_drift_cb.isChecked(),
            }
        cachej["advanced"] = j

    def _cache_load(self, cachej):
        j = cachej.get("advanced", {})
        self.image_stabilization_cb.setCurrentIndex(
            j.get("image_stabilization", 0))
        if self.ac.microscope.has_z():
            stacking = j.get("stacking", {})
            self.stacker_number_le.setText(stacking.get("images_pm", "0"))
            self.stacker_distance_le.setText(stacking.get(
                "distance_pm", "0.0"))
            self.stack_cb.setCurrentIndex(stacking.get("mode_index", 0))
            self.stack_drift_cb.setChecked(stacking.get("drift_correction", 0))

    #
    def update_stack_mode(self, *args):
        mode = self.stack_cb.currentIndex()

        # Manual
        if mode == 1:
            self.stacker_distance_le.setEnabled(True)
            self.stacker_number_le.setEnabled(True)
        # Either disable or auto set
        else:
            self.stacker_distance_le.setEnabled(False)
            self.stacker_number_le.setEnabled(False)

        def setup_die_step(distance_mult, step_mult):
            stacker = AutoStacker(microscope=self.ac.microscope)
            objective_config = self.ac.objective_config()
            assert objective_config
            params = stacker.calc_die_parameters(objective_config,
                                                 distance_mult, step_mult)
            self.stacker_distance_le.setText("%0.6f" % params["pm_distance"])
            self.stacker_number_le.setText("%u" % params["pm_steps"])

        """
        self.stack_cb.addItem("A: None")
        self.stack_cb.addItem("B: Manual")
        self.stack_cb.addItem("C: die normal")
        self.stack_cb.addItem("D: die double distance")
        self.stack_cb.addItem("E: die double steps")
        """
        # Disabled
        if mode == 0:
            self.stacker_distance_le.setText("0.0")
            self.stacker_number_le.setText("0")
        # Manual
        elif mode == 1:
            pass
        # Normal
        elif mode == 2:
            setup_die_step(1, 1)
        # Double distance
        elif mode == 3:
            # Keep step size constant => add more steps
            setup_die_step(2, 2)
        # Double step
        elif mode == 4:
            setup_die_step(1, 2)
        else:
            assert 0, "unknown mode"


class StitchingTab(ArgusTab):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stitcher_thread = None
        self.last_cs_upload = None

    def _initUI(self):
        layout = QGridLayout()
        row = 0

        def stitch_gb():
            layout = QGridLayout()
            row = 0

            self.key_widgets = []

            def key_widget(widget):
                self.key_widgets.append(widget)
                return widget

            layout.addWidget(key_widget(QLabel("AccessKey")), row, 0)
            # Is there a reasonable default here?
            self.stitch_accesskey = key_widget(
                QLineEdit(self.ac.bc.labsmore_stitch_aws_access_key()))
            layout.addWidget(self.stitch_accesskey, row, 1)
            row += 1

            layout.addWidget(key_widget(QLabel("SecretKey")), row, 0)
            self.stitch_secretkey = key_widget(
                QLineEdit(self.ac.bc.labsmore_stitch_aws_secret_key()))
            self.stitch_secretkey.setEchoMode(QLineEdit.Password)
            layout.addWidget(self.stitch_secretkey, row, 1)
            row += 1

            layout.addWidget(key_widget(QLabel("IDKey")), row, 0)
            # Is there a reasonable default here?
            self.stitch_idkey = key_widget(
                QLineEdit(self.ac.bc.labsmore_stitch_aws_id_key()))
            self.stitch_idkey.setEchoMode(QLineEdit.Password)
            layout.addWidget(self.stitch_idkey, row, 1)
            row += 1

            for widget in self.key_widgets:
                widget.setVisible(config.get_bc().dev_mode())

            layout.addWidget(QLabel("Notification Email Address"), row, 0)
            self.stitch_email = QLineEdit(
                self.ac.bc.labsmore_stitch_notification_email())
            reg_ex = QRegExp("\\b[A-z0-9._%+-]+@[A-z0-9.-]+\\.[A-z]{2,4}\\b")
            input_validator = QRegExpValidator(reg_ex, self.stitch_email)
            self.stitch_email.setValidator(input_validator)
            layout.addWidget(self.stitch_email, row, 1)
            row += 1

            def manual_gb():
                layout = QVBoxLayout()

                self.stitch_browse_pb = QPushButton("Browse for directory")
                self.stitch_browse_pb.clicked.connect(
                    self.browse_for_stitch_dir)
                layout.addWidget(self.stitch_browse_pb)

                self.manual_stitch_dir = QLineEdit("")
                layout.addWidget(self.manual_stitch_dir)

                self.cs_pb = QPushButton("Run manual stitch")
                self.cs_pb.clicked.connect(self.stitch_begin_manual_cs)
                layout.addWidget(self.cs_pb)

                gb = QGroupBox("Manual stitch")
                gb.setLayout(layout)
                return gb

            layout.addWidget(manual_gb(), row, 0, 1, 2)
            row += 1

            gb = QGroupBox("Cloud Stitching")
            gb.setLayout(layout)
            return gb

        layout.addWidget(stitch_gb(), row, 0)
        row += 1

        self.setLayout(layout)

    def _post_ui_init(self):
        self.stitcher_thread = StitcherThread(ac=self.ac, parent=self)
        self.stitcher_thread.log_msg.connect(self.ac.log)
        self.stitcher_thread.start()

    def _shutdown_request(self):
        if self.stitcher_thread:
            self.stitcher_thread.shutdown_request()

    def _shutdown_join(self):
        if self.stitcher_thread:
            self.stitcher_thread.shutdown_join()

    def stitch_begin_manual_cs(self):
        this_upload = str(self.manual_stitch_dir.text())
        if this_upload == self.last_cs_upload:
            self.ac.log(f"Ignoring duplicate upload: {this_upload}")
            return
        self.stitch_add(this_upload)
        self.last_cs_upload = this_upload

    def scan_completed(self, scan_config, result):
        if scan_config["dry"]:
            return

        if self.ac.mainTab.imaging_widget.iow.stitch_gb.isChecked():
            # CLI box is special => take priority
            # CLI may launch CloudStitch under the hood
            self.stitch_add(scan_config["out_dir"], scan_config=scan_config)

    def stitch_add(self, directory, scan_config=None):
        self.ac.log(f"Stitch: requested {directory}")
        if not os.path.exists(directory):
            self.ac.log(
                f"Aborting stitch: directory does not exist: {directory}")
            return
        if scan_config is not None:
            ippj = scan_config["pconfig"].get("ipp", {})
        else:
            ippj = {}
            self.ac.mainTab.imaging_widget.update_ippj(ippj)

        # Offload uploads etc to thread since they might take a while
        cs_info = None
        if ippj["cloud_stitch"]:
            cs_info = self.get_cs_info()
            if not cs_info.is_plausible():
                self.log(
                    "ERROR: requested CloudStitch but don't have credentials")
                return
        self.stitcher_thread.imagep_add(
            directory=directory,
            cs_info=cs_info,
            ippj=ippj,
        )

    def get_cs_info(self):
        return CSInfo(access_key=str(self.stitch_accesskey.text()),
                      secret_key=str(self.stitch_secretkey.text()),
                      id_key=str(self.stitch_idkey.text()),
                      notification_email=str(self.stitch_email.text()))

    def has_cs_info(self):
        # called too early in initialization
        # return self.get_cs_info().is_plausible()
        return self.ac.bc.labsmore_stitch_plausible()

    def browse_for_stitch_dir(self):
        filename = QFileDialog.getExistingDirectory(
            None, "Select directory", self.ac.microscope.bc.get_scan_dir(),
            QFileDialog.ShowDirsOnly)
        if not filename:
            return
        self.manual_stitch_dir.setText(filename)


class JoystickListener(QPushButton):
    """
    Widget that maintains state of joystick enabled/disabled.
    """
    def __init__(self, label, parent=None):
        super().__init__(label, parent=parent)
        self.parent = parent
        self.setCheckable(True)
        self.setIcon(QIcon(config.GUI.icon_files["gamepad"]))
        # should be enabled by default?
        # if in bad position could crash system
        # probably better to make enabling explicit
        self.setChecked(False)
        # pressed captures our toggle => creates loop
        self.clicked.connect(self.was_pressed)

    def was_pressed(self):
        # It's already toggled when we get here
        if self.isChecked():
            self.parent.ac.joystick_thread.enable()
        else:
            self.parent.ac.joystick_thread.disable()


class JogListener(QPushButton):
    """
    Widget that listens for WSAD keys for linear stage movement
    """
    def __init__(self, label, parent=None):
        super().__init__(label, parent=parent)
        self.parent = parent
        self.setIcon(QIcon(config.GUI.icon_files["jog"]))

    def keyPressEvent(self, event):
        self.parent.keyPressEventCaptured(event)

    def keyReleaseEvent(self, event):
        self.parent.keyReleaseEventCaptured(event)

    def focusInEvent(self, event):
        """
        Clearly indicate movement starting
        """
        p = self.palette()
        p.setColor(self.backgroundRole(), Qt.yellow)
        self.setPalette(p)

    def focusOutEvent(self, event):
        """
        Clearly indicate movement stopping
        """
        p = self.palette()
        p.setColor(self.backgroundRole(), Qt.white)
        self.setPalette(p)


class HLinearSlider100(QSlider):
    def __init__(self, default, parent=None):
        super().__init__(Qt.Horizontal, parent=parent)
        self.setMinimum(1)
        self.setMaximum(100)
        self.setValue(default)
        self.setTickPosition(QSlider.TicksBelow)
        self.setTickInterval(10)
        self.setFocusPolicy(Qt.NoFocus)


"""
Slider is displayed as log scale ticks 1, 10, 100
    Represents percent of max velocity to use
Internal state is 1 to 100 linear (fraction moved across)
However, actual moves need to get scaled by the
"""


class JogSlider(QWidget):
    def __init__(self, ac, parent=None):
        super().__init__(parent=parent)

        self.ac = ac

        # log scaled to slider
        self.jog_cur = None

        self.jog_min = 0.1
        self.jog_max = 100
        self.slider_min = 1
        self.slider_max = 100
        # As fraction of slider max value
        self.slider_adjust_factor = 0.1

        self.layout = QVBoxLayout()

        def labels():
            self.label_layout = QHBoxLayout()
            self.update_label_layout(False)
            return self.label_layout

        self.layout.addLayout(labels())

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(self.slider_min)
        self.slider.setMaximum(self.slider_max)
        self.slider.setValue(self.slider_max // 2)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTickInterval(33)
        # Send keyboard events to CNC navigation instead
        self.slider.setFocusPolicy(Qt.NoFocus)
        self.layout.addWidget(self.slider)

        self.setLayout(self.layout)

    def update_label_layout(self, fine):
        # Clear layout
        for i in reversed(range(self.label_layout.count())):
            self.label_layout.itemAt(i).widget().setParent(None)

        if fine:
            labels = ("0.1", "1", "10")
        else:
            labels = ("0.1", "1", "10", "100")
        for _labeli, s in enumerate(labels):
            label = QLabel(s)
            """
            2023-10-25: centered on all looks best
            if labeli == 0:
                label.setAlignment(Qt.AlignLeft)
            elif labeli == len(labels) - 1:
                label.setAlignment(Qt.AlignRight)
            else:
                label.setAlignment(Qt.AlignCenter)
            """
            label.setAlignment(Qt.AlignCenter)
            self.label_layout.addWidget(label)

    def get_jog_percentage(self):
        verbose = 0
        slider_val = float(self.slider.value())
        v = math.log(slider_val, 10)
        log_delta = math.log(self.slider_max, 10) - math.log(
            self.slider_min, 10)
        verbose and print('delta', log_delta, math.log(self.jog_max, 10),
                          math.log(self.jog_min, 10))
        # Scale in log space
        log_scalar = (math.log(self.jog_max, 10) -
                      math.log(self.jog_min, 10)) / log_delta
        v = math.log(self.jog_min, 10) + v * log_scalar
        # Convert back to linear space
        v = 10**v
        ret = max(min(v, self.jog_max), self.jog_min)
        verbose and print("jog: slider %u => jog %u (was %u)" %
                          (slider_val, ret, v))
        return ret

    def get_jog_fraction(self):
        """
        Return a proportion of how to scale the jog (0 to 1.0)
        No fine scaling applied
        """
        return self.get_jog_percentage() / self.jog_max

    def increase_key(self):
        slider_val = int(
            min(
                self.slider_max,
                float(self.slider.value()) +
                self.slider_max * self.slider_adjust_factor))
        self.slider.setValue(slider_val)

    def decrease_key(self):
        slider_val = int(
            max(
                self.slider_min,
                float(self.slider.value()) -
                self.slider_max * self.slider_adjust_factor))
        self.slider.setValue(slider_val)

    def jog_slider_raw(self):
        return int(self.slider.value())

    def set_jog_slider_raw(self, v):
        self.slider.setValue(int(v))

    def set_jog_slider(self, val):
        # val is expected to be between 0.0 to 1.0
        val_min = 0
        val_max = 1.0
        if val == 0:
            self.slider.setValue(self.slider_min)
            return
        old_range = val_max - val_min
        new_range = self.slider_max - self.slider_min
        new_value = ((
            (val - val_min) * new_range) / old_range) + self.slider_min
        self.slider.setValue(new_value)


class TopMotionWidget(AWidget):
    def __init__(self, ac, aname=None, parent=None):
        super().__init__(ac, aname=aname, parent=parent)

        self.fine_move = False
        # Used to switch back and forth + save
        self.slider_last_coarse = None
        self.slider_last_fine = None

        self.axis_map = {
            # Upper left origin
            Qt.Key_A: ("x", -1),
            Qt.Key_D: ("x", 1),
            Qt.Key_S: ("y", -1),
            Qt.Key_W: ("y", 1),
        }
        if self.ac.microscope.has_z():
            self.axis_map.update({
                Qt.Key_Q: ("z", -1),
                Qt.Key_E: ("z", 1),
            })
        # Poll time misses quick presses
        # https://github.com/Labsmore/pyuscope/issues/300
        self.jog_last_presses = {}

        self.last_send = time.time()
        # Can be used to invert keyboard, joystick XY inputs
        self.kj_xy_scalar = 1.0
        # self.max_velocities = None

        self.keys_up = {}

    def statistics_getj(self, statj):
        j = statj.setdefault("argus", {})
        j["keyboard_slow_jogs"] = self.jog_controller.slow_jogs

    # Used to invert XY for user preference
    def set_kj_xy_scalar(self, val):
        self.kj_xy_scalar = val

    def _initUI(self):
        self.joystick_listener = None
        if self.ac.joystick_thread:
            self.joystick_listener = JoystickListener("  Joystick Control",
                                                      self)

        layout = QHBoxLayout()

        self.listener = JogListener("XXX", self)
        self.update_jog_text()
        layout.addWidget(self.listener)
        if self.joystick_listener:
            layout.addWidget(self.joystick_listener)
        self.slider = JogSlider(ac=self.ac)
        layout.addWidget(self.slider)

        self.setLayout(layout)

    def _post_ui_init(self):
        # self.max_velocities = self.ac.motion_thread.motion.get_max_velocities()
        self.jog_controller = self.ac.motion_thread.get_jog_controller(0.2)
        self.ac.microscope.statistics.add_getj(self.statistics_getj)

    def update_slider_cache(self):
        if self.fine_move:
            self.slider_last_fine = self.slider.jog_slider_raw()
        else:
            self.slider_last_coarse = self.slider.jog_slider_raw()

    def update_slider_from_last(self):
        if not self.fine_move and self.slider_last_coarse is not None:
            self.slider.set_jog_slider_raw(self.slider_last_coarse)
        if self.fine_move and self.slider_last_fine is not None:
            self.slider.set_jog_slider_raw(self.slider_last_fine)

    def toggle_fine(self):
        self.update_slider_cache()
        self.fine_move = not self.fine_move
        self.slider.update_label_layout(self.fine_move)
        self.update_jog_text()
        self.update_slider_from_last()

    def update_jog_text(self):
        if self.fine_move:
            label = "Jog (fine)"
        else:
            label = "Jog (coarse)"
        self.listener.setText(label)

    def _cache_save(self, cachej):
        # not listening to slide events...
        self.update_slider_cache()
        j = {}
        j["fine_move"] = self.fine_move
        j["slider_last_fine"] = self.slider_last_fine
        j["slider_last_coarse"] = self.slider_last_coarse
        cachej["top-motion"] = j

    def _cache_load(self, cachej):
        j = cachej.get("top-motion", {})

        self.fine_move = j.get("fine_move", False)
        self.slider_last_fine = j.get("slider_last_fine")
        self.slider_last_coarse = j.get("slider_last_coarse")
        self.update_jog_text()
        self.update_slider_from_last()

    def keyPressEventCaptured(self, event):
        k = event.key()
        # Ignore duplicates, want only real presses
        # if 0 and event.isAutoRepeat():
        #     return

        self.keys_up[k] = True
        self.jog_last_presses[k] = True
        if k == Qt.Key_F:
            self.toggle_fine()
        elif k == Qt.Key_Z:
            self.slider.decrease_key()
        elif k == Qt.Key_C:
            self.slider.increase_key()
        else:
            event.ignore()
            return
            # print("unknown key %s" % (k, ))
        event.accept()

    def keyReleaseEventCaptured(self, event):
        # Don't move around with moving around text boxes, etc
        # if not self.video_container.hasFocus():
        #    return
        k = event.key()
        self.keys_up[k] = False

        # Hmm larger GUI doesn't get these if this handler is active
        if k == Qt.Key_Escape:
            self.ac.motion_thread.stop()

        # Ignore duplicates, want only real presses
        if event.isAutoRepeat():
            return

    def update_jogging(self):
        joystick = self.ac.microscope.joystick
        if joystick:
            slider_val = self.slider.get_jog_fraction()
            joystick.config.set_volatile_scalars({
                "x":
                self.kj_xy_scalar * slider_val,
                "y":
                self.kj_xy_scalar * slider_val,
                "z":
                self.kj_xy_scalar * slider_val,
            })

        # Check keyboard jogging state
        jogs = dict([(axis, 0.0)
                     for axis in self.ac.motion_thread.motion.axes()])
        for k, (axis, keyboard_sign) in self.axis_map.items():
            # not all systems have z
            if axis not in jogs:
                continue

            if not (self.keys_up.get(k, False)
                    or self.jog_last_presses.get(k, False)):
                continue

            fine_scalar = 1.0
            # FIXME: now that using real machine units need to revisit this
            if self.fine_move:
                fine_scalar = 0.1
            jog_val = keyboard_sign * self.kj_xy_scalar * fine_scalar * self.slider.get_jog_fraction(
            )
            jogs[axis] = jog_val

        self.jog_controller.update(jogs)
        self.jog_last_presses = {}

    def _poll_misc(self):
        self.update_jogging()


class TopWidget(AWidget):
    def _initUI(self):
        layout = QHBoxLayout()

        self.stop_pb = QPushButton("STOP")
        self.stop_pb.clicked.connect(self.stop_pushed)
        layout.addWidget(self.stop_pb)

        self.motion_widget = TopMotionWidget(ac=self.ac,
                                             aname="motion",
                                             parent=self)
        layout.addWidget(self.motion_widget)

        def right_layout():
            layout = QVBoxLayout()

            self.pos_label = QLabel("")
            layout.addWidget(self.pos_label)

            self.autofocus_pb = QPushButton("Autofocus")
            self.autofocus_pb.clicked.connect(self.autofocus_pushed)
            layout.addWidget(self.autofocus_pb)

            return layout

        layout.addLayout(right_layout())

        self.setLayout(layout)

    def stop_pushed(self):
        self.ac.log("System stop requested")
        self.ac.microscope.stop()

    def autofocus_pushed(self):
        self.ac.image_processing_thread.auto_focus(self.ac.objective_config())

    def _poll_misc(self):
        last_pos = self.ac.motion_thread.get_pos_cache()
        if last_pos is not None:
            self.update_pos(last_pos)

    def update_pos(self, pos):
        got = self.ac.usc.motion.format_positions(pos)
        self.pos_label.setText(got)

    '''
    def _cache_save(self, cachej):
        j = {}
        cachej["top"] = j

    def _cache_load(self, cachej):
        j = cachej.get("top", {})
    '''


class AnnotateImage(QLabel):
    """
    A custom class which overrides the painting method to allow annotations
    on the base image
    """

    Modes = Enum('Modes', ['SELECT', 'RULER'])

    def __init__(self, filename=None):
        super().__init__()
        self.filename = filename
        self.mode = self.Modes.SELECT
        self.measurements = []
        self.point_a = None
        self.point_b = False
        self.selected_index = -1
        self._pixel_conversion = 1.0

    @property
    def pixel_conversion(self):
        return self._pixel_conversion

    @pixel_conversion.setter
    def pixel_conversion(self, value):
        self._pixel_conversion = value
        self.update()  # Repaint when conversion updated

    def add_measurement(self, value):
        self.measurements.append(value)

    # Check if the current pos selects a target
    def select(self, pos):
        pos = (pos.x(), pos.y())
        self.selected_index = -1
        for n, m in enumerate(self.measurements):
            # TODO: maybe cache points for faster and more accurate selection?
            if m[0] == "Line":
                start = (m[1].x(), m[1].y())
                end = (m[2].x(), m[2].y())
                start_x = min(start[0], end[0])
                end_x = max(start[0], end[0])
                start_y = min(start[1], end[1])
                end_y = max(start[1], end[1])
                if pos[0] in range(start_x, end_x) and pos[1] in range(
                        start_y, end_y):
                    self.selected_index = n
                    break
            elif m[0] == "Circle":  # e.g. logic for detecting circle/other shape selection...
                pass
            elif m[0] == "Square":
                pass

        self.update()

    def delete_selected(self):
        if self.selected_index == -1:
            return
        if self.mode != self.Modes.SELECT:
            return
        try:
            del self.measurements[self.selected_index]
            self.selected_index = -1
            self.update()
        except Exception as _e:
            pass

    def set_mode(self, value: int):
        self.mode = value
        # Reset vars when mode change
        self.point_a = None
        self.point_b = None

    def paintEvent(self, e):
        # Make sure to paint the image first
        super().paintEvent(e)

        # Now draw the measurements
        qp = QPainter()
        qp.begin(self)
        pen = QPen(Qt.blue)

        def draw_point(point):
            circ_radius = 4
            pen.setWidth(4)
            qp.setPen(pen)
            qp.drawEllipse(point.x() - int(circ_radius / 2),
                           point.y() - int(circ_radius / 2), circ_radius,
                           circ_radius)

        def draw_labelled_line(start, end):
            pen.setWidth(8)
            qp.setPen(pen)
            draw_point(start)
            draw_point(end)
            qp.drawLine(start.x(), start.y(), end.x(), end.y())
            font = QFont()
            font.setFamily('Times')
            font.setBold(True)
            font.setPointSize(12)
            qp.setFont(font)
            distance = ((start.x() - end.x())**2 +
                        (start.y() - end.y())**2)**0.5
            distance = round(distance, 2)
            # Center on line but offset so we aren't on it
            dx = 0
            dy = 0
            # More left/right than up/down?
            if abs(start.x() - end.x()) > abs(start.y() - end.y()):
                # Move up
                dy -= 10
            else:
                # Move right
                dx += 10
            distance_um = self.pixel_conversion * distance
            text = format_mm_3dec(distance_um / 1000)
            qp.drawText((start.x() + end.x()) // 2 + dx,
                        (start.y() + end.y()) // 2 + dy, text)

        selected_color = QColor(43, 250, 43, 200)
        default_color = QColor(43, 43, 43, 200)
        for n, m in enumerate(self.measurements):
            pen.setColor(default_color)
            if n == self.selected_index:
                pen.setColor(selected_color)
            if m[0] == "Line":
                draw_labelled_line(m[1], m[2])

        if self.point_a:
            point_color = QColor(43, 43, 250, 200)
            pen.setColor(point_color)
            draw_point(self.point_a)

        qp.end()

    def mouseReleaseEvent(self, event):
        # Try to find a selectable
        if self.mode == self.Modes.SELECT:
            self.select(event.pos())
            return

        if self.mode == self.Modes.RULER:
            if not self.point_a:
                self.point_a = event.pos()
                self.update()
                return
            if self.point_a:
                self.add_measurement(["Line", self.point_a, event.pos()])
                self.point_a = None
                self.point_b = None

        self.update()

    def undo(self):
        try:
            self.measurements.pop()
            self.update()
        except:
            pass
            # print("No more actions to undo")

    def clear_all(self):
        self.measurements = []
        self.update()


class MeasureTab(ArgusTab):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        layout = QGridLayout()
        row = 0

        def stack_gb():
            layout = QGridLayout()
            row = 0

            # Opening images is not supported at this time
            # Need metadata (EXIF, etc) support
            if 0:
                hbox = QHBoxLayout()
                self.open_image_pb = QPushButton("Open")
                self.open_image_pb.clicked.connect(self.open)
                hbox.addWidget(self.open_image_pb)
                hbox.addStretch()
                layout.addLayout(hbox, row, 0)
            row += 1
            self.annotate_image = AnnotateImage()
            self.annotate_image.setBackgroundRole(QPalette.Base)
            self.annotate_image.setSizePolicy(QSizePolicy.Ignored,
                                              QSizePolicy.Ignored)
            self.annotate_image.setScaledContents(True)
            self.sa_image = QScrollArea()
            self.sa_image.setBackgroundRole(QPalette.Dark)
            self.sa_image.setWidget(self.annotate_image)
            self.sa_image.setVisible(False)
            layout.addWidget(self.sa_image, row, 0)
            self.pb_grid = QVBoxLayout()
            self.ruler_pb = QPushButton("Ruler")
            self.ruler_pb.setCheckable(True)
            self.ruler_pb.clicked.connect(self.on_ruler)
            self.pb_grid.addWidget(self.ruler_pb)
            self.pb_grid.addStretch()
            self.clear_all_pb = QPushButton("Clear All")
            self.clear_all_pb.clicked.connect(self.annotate_image.clear_all)
            self.pb_grid.addWidget(self.clear_all_pb)
            layout.addLayout(self.pb_grid, row, 1)

            gb = QGroupBox("")
            gb.setLayout(layout)
            return gb

        layout.addWidget(stack_gb(), row, 0)
        self.setLayout(layout)

        # Add hotkeys
        self.shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self.shortcut.activated.connect(self.on_undo)
        self.shortcut = QShortcut(QKeySequence("Del"), self)
        self.shortcut.activated.connect(self.annotate_image.delete_selected)

        self.ac.snapshotCaptured.connect(self.snapshot_processed)

    @pyqtSlot()
    def on_undo(self):
        self.annotate_image.undo()

    def on_ruler(self, event):
        """
        Toggle ruler mode
        """
        if self.ruler_pb.isChecked():
            self.annotate_image.set_mode(self.annotate_image.Modes.RULER)
            self.ruler_pb.setStyleSheet(
                f"color: white; background-color: green;")
        else:
            self.annotate_image.set_mode(self.annotate_image.Modes.SELECT)
            self.ruler_pb.setStyleSheet(
                f"color: black; background-color: lightgrey;")

    def fitToWindow(self):
        self.scrollArea.setWidgetResizable(True)

    '''
    def open(self):
        """
        Open image file
        """
        options = QFileDialog.Options()
        fileName, _ = QFileDialog.getOpenFileName(
            self,
            'QFileDialog.getOpenFileName()',
            '',
            'Images (*.png *.jpeg *.jpg *.bmp)',
            options=options)
        if fileName:
            qim = QImage(fileName)
            if qim.isNull():
                QMessageBox.information(self, "Load Image",
                                        "Cannot load %s." % fileName)
                return
            self.annotate_image.clear_all()
            self.annotate_image.setPixmap(QPixmap.fromImage(qim))
            self.sa_image.setVisible(True)
            self.annotate_image.adjustSize()
            # Open the accompanying .json file if it exists
            try:
                dir_name = os.path.dirname(fileName)
                base_name = os.path.splitext(os.path.basename(fileName))[0]
                f = open(os.path.join(dir_name, base_name + ".json"))
                data = json.load(f)
                self.annotate_image.pixel_conversion = data.get(
                    "pixelConversion", 1.0)
            except Exception as e:
                print("Failed to load .json")
    '''

    def snapshot_processed(self, data):
        """
        Receive a new snapshot image
        """
        image = data.get('image', None)
        if image is None:
            return
        self.annotate_image.pixel_conversion = data["objective_config"][
            "um_per_pixel"]
        # Convert PIL image to QT image
        image = image.convert("RGBA")
        data = image.tobytes("raw", "RGBA")
        qim = QImage(data, image.size[0], image.size[1],
                     QImage.Format_RGBA8888)
        self.annotate_image.clear_all()
        self.annotate_image.setPixmap(QPixmap.fromImage(qim))
        self.sa_image.setVisible(True)
        self.annotate_image.adjustSize()
        # Need ruler before select mode is useful
        self.ruler_pb.setChecked(True)
        self.on_ruler(None)
