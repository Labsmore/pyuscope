#!/usr/bin/env python3

from uscope.gui.gstwidget import GstVideoPipeline, gstwidget_main
from uscope.gui.control_scrolls import get_control_scroll
from uscope.util import add_bool_arg
from uscope.config import get_usj, USC, PC
from uscope.imager.imager_util import get_scaled
from uscope.benchmark import Benchmark
from uscope.gui import plugin
from uscope.gst_util import Gst, CaptureSink
from uscope.motion.plugins import get_motion_hal
from uscope.app.argus.threads import MotionThread, PlannerThread
from uscope.planner.planner_util import microscope_to_planner_config
from uscope import config
from uscope.motion import motion_util
import json
from collections import OrderedDict

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import time
import datetime
import os.path
from PIL import Image
import sys
import traceback
import threading
from io import StringIO
import math

debug = os.getenv("ARGUS_VERBOSE") == "Y"


def dbg(*args):
    if not debug:
        return
    if len(args) == 0:
        print()
    elif len(args) == 1:
        print('main: %s' % (args[0], ))
    else:
        print('main: ' + (args[0] % args[1:]))


def error(msg, code=1):
    prefix = 'ERROR'
    if sys.stdout.isatty():
        prefix = '\33[91m' + prefix + '\33[0m'
    print('{} {}'.format(prefix, msg))
    exit(code)


"""
Argus Widget
"""


class AWidget(QWidget):
    def __init__(self, ac, parent=None):
        super().__init__(parent=None)
        self.ac = ac

    def initUI(self):
        pass

    def post_ui_init(self):
        pass


class ArgusTab(AWidget):
    pass


"""
Select objective and show FoV
"""


class ObjectiveWidget(AWidget):
    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)
        self.objective_name_le = None

    def initUI(self):
        cl = QGridLayout()

        row = 0
        l = QLabel("Objective")
        cl.addWidget(l, row, 0)

        self.obj_cb = QComboBox()
        cl.addWidget(self.obj_cb, row, 1)
        self.obj_cb.currentIndexChanged.connect(self.update_obj_config)
        self.obj_view = QLabel("")
        cl.addWidget(self.obj_view, row, 2)
        # seed it
        self.reload_obj_cb()
        row += 1

        self.setLayout(cl)

    def post_ui_init(self):
        self.update_obj_config()

    def reload_obj_cb(self):
        '''Re-populate the objective combo box'''
        self.obj_cb.clear()
        self.obj_config = None
        self.obj_configi = None
        for objective in self.ac.usj["objectives"]:
            self.obj_cb.addItem(objective['name'])

    def update_obj_config(self):
        '''Make resolution display reflect current objective'''
        self.obj_configi = self.obj_cb.currentIndex()
        self.obj_config = self.ac.usj['objectives'][self.obj_configi]
        self.ac.log('Selected objective %s' % self.obj_config['name'])

        im_w_pix, im_h_pix = self.ac.usc.imager.cropped_wh()
        im_w_um = self.obj_config["x_view"]
        im_h_um = im_w_um * im_h_pix / im_w_pix
        self.obj_view.setText('View : %0.3fx %0.3fy' % (im_w_um, im_h_um))
        if self.objective_name_le:
            suffix = self.obj_config.get("suffix")
            if not suffix:
                suffix = self.obj_config.get("name")
            self.objective_name_le.setText(suffix)


"""
Save a snapshot to a file
"""


class SnapshotWidget(AWidget):
    snapshotCaptured = pyqtSignal(int)

    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)

        # self.pos.connect(self.update_pos)
        self.snapshotCaptured.connect(self.captureSnapshot)

    def initUI(self):
        gb = QGroupBox('Snapshot')
        layout = QGridLayout()

        snapshot_dir = self.ac.usc.app("argus").snapshot_dir()
        if not os.path.isdir(snapshot_dir):
            self.ac.log('Snapshot dir %s does not exist' % snapshot_dir)
            if os.path.exists(snapshot_dir):
                raise Exception("Snapshot directory is not accessible")
            os.mkdir(snapshot_dir)
            self.ac.log('Snapshot dir %s created' % snapshot_dir)

        # nah...just have it in the config
        # d = QFileDialog.getExistingDirectory(self, 'Select snapshot directory', snapshot_dir)

        self.snapshot_serial = -1

        self.snapshot_pb = QPushButton("Snap")
        self.snapshot_pb.setIcon(QIcon(config.GUI.icon_files["camera"]))

        self.snapshot_pb.clicked.connect(self.take_snapshot)
        layout.addWidget(self.snapshot_pb, 0, 0)

        self.snapshot_fn_le = QLineEdit('snapshot')
        self.snapshot_suffix_le = QLineEdit(
            self.ac.usc.imager.save_extension())
        # XXX: since we already have jpegenc this is questionable
        self.snapshot_suffix_le.setEnabled(False)
        self.snapshot_suffix_le.setSizePolicy(
            QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
        hl = QHBoxLayout()
        hl.addWidget(self.snapshot_fn_le)
        hl.addWidget(self.snapshot_suffix_le)
        layout.addLayout(hl, 0, 1)

        gb.setLayout(layout)

        layout = QVBoxLayout()
        layout.addWidget(gb)
        self.setLayout(layout)

    def take_snapshot(self):
        self.ac.log('Requesting snapshot')
        # Disable until snapshot is completed
        self.snapshot_pb.setEnabled(False)

        def emitSnapshotCaptured(image_id):
            self.ac.log('Image captured: %s' % image_id)
            self.snapshotCaptured.emit(image_id)

        self.ac.capture_sink.request_image(emitSnapshotCaptured)

    def snapshot_fn(self):
        return snapshot_fn(user=str(self.snapshot_fn_le.text()),
                           extension=str(self.snapshot_suffix_le.text()),
                           parent=self.ac.usc.app("argus").snapshot_dir())

    def captureSnapshot(self, image_id):
        self.ac.log('RX image for saving')

        def try_save():
            image = self.ac.capture_sink.pop_image(image_id)
            fn_full = self.snapshot_fn()
            self.ac.log('Capturing %s...' % fn_full)
            factor = self.ac.usc.imager.scalar()
            # Use a reasonably high quality filter
            try:
                scaled = get_scaled(image, factor, Image.ANTIALIAS)
                extension = str(self.snapshot_suffix_le.text())
                if extension == ".jpg":
                    scaled.save(fn_full,
                                quality=self.ac.usc.imager.save_quality())
                else:
                    scaled.save(fn_full)
            # FIXME: refine
            except Exception:
                self.ac.log('WARNING: failed to save %s' % fn_full)

        try_save()

        self.snapshot_pb.setEnabled(True)

    def post_ui_init(self):
        pass


"""
Provides camera overview and ROI side by side
"""


class FullROIWidget(AWidget):
    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)

    def initUI(self):
        # Overview
        def low_res_layout():
            layout = QVBoxLayout()
            layout.addWidget(QLabel("Overview"))
            layout.addWidget(self.ac.vidpip.get_widget("overview"))

            return layout

        # Higher res in the center for focusing
        def high_res_layout():
            layout = QVBoxLayout()
            layout.addWidget(QLabel("Focus"))
            layout.addWidget(self.ac.vidpip.get_widget("roi"))

            return layout

        layout = QHBoxLayout()
        layout.addLayout(low_res_layout())
        layout.addLayout(high_res_layout())
        self.setLayout(layout)

    def post_ui_init(self):
        pass


"""
TODO:
-XY w/ third point to correct for angle skew
-XYZ w/ third point to correct for height
    Maybe evolution / same as above
-Many point for distorted dies like packaged chip
"""


class PlannerWidget(AWidget):
    def __init__(self, ac, scan_widget, objective_widget, parent=None):
        super().__init__(ac=ac, parent=parent)
        self.scan_widget = scan_widget
        self.objective_widget = objective_widget

    # FIXME: abstract these better

    def get_out_dir(self):
        base_out_dir = self.ac.usc.app("argus").scan_dir()
        return self.scan_widget.jobName.getName(base_out_dir)

    def get_objective(self):
        return self.objective_widget.obj_config


"""
Integrates both 2D planner controls and current display
2.5D: XY planner controls + XYZ display
"""


class XYPlanner2PWidget(PlannerWidget):
    def __init__(self, ac, scan_widget, objective_widget, parent=None):
        super().__init__(ac=ac,
                         scan_widget=scan_widget,
                         objective_widget=objective_widget,
                         parent=parent)

    def initUI(self):
        gl = QGridLayout()
        row = 0

        gl.addWidget(QLabel("X (mm)"), row, 1)
        gl.addWidget(QLabel("Y (mm)"), row, 2)
        gl.addWidget(QLabel("Z (mm)"), row, 3)
        row += 1

        self.axis_pos_label = {}
        gl.addWidget(QLabel("Current"), row, 0)
        label = QLabel("?")
        gl.addWidget(label, row, 1)
        self.axis_pos_label['x'] = label
        label = QLabel("?")
        gl.addWidget(label, row, 2)
        self.axis_pos_label['y'] = label
        label = QLabel("?")
        gl.addWidget(label, row, 3)
        self.axis_pos_label['z'] = label
        row += 1

        start_label, end_label, start_icon, end_icon = {
            "ll": ("Lower left", "Upper right", config.GUI.icon_files["SW"],
                   config.GUI.icon_files["NE"]),
            "ul": ("Upper left", "Lower right", config.GUI.icon_files["NW"],
                   config.GUI.icon_files["SE"]),
        }[self.ac.usc.motion.origin()]

        self.plan_start_pb = QPushButton(start_label)
        self.plan_start_pb.clicked.connect(self.set_start_pos)
        self.plan_start_pb.setIcon(QIcon(start_icon))
        gl.addWidget(self.plan_start_pb, row, 0)
        self.plan_x0_le = QLineEdit('0.000')
        gl.addWidget(self.plan_x0_le, row, 1)
        self.plan_y0_le = QLineEdit('0.000')
        gl.addWidget(self.plan_y0_le, row, 2)
        row += 1

        self.plan_end_pb = QPushButton(end_label)
        self.plan_end_pb.clicked.connect(self.set_end_pos)
        self.plan_end_pb.setIcon(QIcon(end_icon))
        gl.addWidget(self.plan_end_pb, row, 0)
        self.plan_x1_le = QLineEdit('0.000')
        gl.addWidget(self.plan_x1_le, row, 1)
        self.plan_y1_le = QLineEdit('0.000')
        gl.addWidget(self.plan_y1_le, row, 2)
        row += 1

        self.setLayout(gl)

    def post_ui_init(self):
        self.position_poll_timer = QTimer()
        self.position_poll_timer.timeout.connect(self.poll_update_pos)
        self.position_poll_timer.start(200)

    def poll_update_pos(self):
        last_pos = self.ac.motion_thread.pos_cache
        if last_pos:
            self.update_pos(last_pos)
        # FIXME: hack to avoid concurrency issues with planner and motion thread fighting
        # merge them together?
        if not self.ac.pt:
            self.ac.motion_thread.update_pos_cache()
        # 400 => 100: allow more frequent updates now that flicker issue is solved
        self.position_poll_timer.start(100)

    def update_pos(self, pos):
        # FIXME: this is causing screen flickering
        # https://github.com/Labsmore/pyuscope/issues/34
        for axis, axis_pos in pos.items():
            # hack...not all systems use z but is included by default
            if axis == 'z' and axis not in self.axis_pos_label:
                continue
            self.axis_pos_label[axis].setText('%0.3f' % axis_pos)

    def mk_contour_json(self):
        try:
            x0 = float(self.plan_x0_le.text())
            y0 = float(self.plan_y0_le.text())
            x1 = float(self.plan_x1_le.text())
            y1 = float(self.plan_y1_le.text())
        except ValueError:
            self.ac.log("Bad scan x/y")
            return None

        # Planner will sort order as needed
        ret = {
            "start": {
                "x": x0,
                "y": y0,
            },
            "end": {
                "x": x1,
                "y": y1,
            }
        }

        return ret

    def get_current_scan_config(self):
        contour_json = self.mk_contour_json()
        if not contour_json:
            return

        objective = self.get_objective()
        pconfig = microscope_to_planner_config(self.ac.usj,
                                               objective=objective,
                                               contour=contour_json)

        self.ac.update_pconfig(pconfig)

        # Ignored app specific metadata
        pconfig["app"] = {
            "app": "argus",
            "objective": objective,
            "microscope": self.ac.microscope,
        }

        out_dir = self.get_out_dir()
        if os.path.exists(out_dir):
            self.ac.log("Refusing to create config: dir already exists: %s" %
                        out_dir)
            return None

        return {
            "pconfig": pconfig,
            "out_dir": out_dir,
        }

    def set_start_pos(self):
        '''
        try:
            lex = float(self.plan_x0_le.text())
        except ValueError:
            self.ac.log('WARNING: bad X value')

        try:
            ley = float(self.plan_y0_le.text())
        except ValueError:
            self.ac.log('WARNING: bad Y value')
        '''
        # take as upper left corner of view area
        # this is the current XY position
        pos = self.ac.motion_thread.pos_cache
        #self.ac.log("Updating start pos w/ %s" % (str(pos)))
        self.plan_x0_le.setText('%0.3f' % pos['x'])
        self.plan_y0_le.setText('%0.3f' % pos['y'])

    def x_view(self):
        # XXX: maybe put better abstraction on this
        return self.objective_widget.obj_config["x_view"]

    def set_end_pos(self):
        # take as lower right corner of view area
        # this is the current XY position + view size
        pos = self.ac.motion_thread.pos_cache
        #self.ac.log("Updating end pos from %s" % (str(pos)))
        x_view = self.x_view()
        im_w_pix, im_h_pix = self.ac.usc.imager.cropped_wh()
        y_view = 1.0 * x_view * im_h_pix / im_w_pix
        x1 = pos['x'] + x_view
        y1 = pos['y'] + y_view
        self.plan_x1_le.setText('%0.3f' % x1)
        self.plan_y1_le.setText('%0.3f' % y1)


class XYPlanner3PWidget(PlannerWidget):
    def __init__(self, ac, scan_widget, objective_widget, parent=None):
        super().__init__(ac=ac,
                         scan_widget=scan_widget,
                         objective_widget=objective_widget,
                         parent=parent)

    def initUI(self):
        gl = QGridLayout()
        row = 0

        gl.addWidget(QLabel("X (mm)"), row, 1)
        gl.addWidget(QLabel("Y (mm)"), row, 2)
        gl.addWidget(QLabel("Z (mm)"), row, 3)
        row += 1

        self.axis_pos_label = {}
        gl.addWidget(QLabel("Current"), row, 0)
        label = QLabel("?")
        gl.addWidget(label, row, 1)
        self.axis_pos_label['x'] = label
        label = QLabel("?")
        gl.addWidget(label, row, 2)
        self.axis_pos_label['y'] = label
        label = QLabel("?")
        gl.addWidget(label, row, 3)
        self.axis_pos_label['z'] = label
        row += 1

        self.corner_widgets = OrderedDict()

        def make_corner_widget(key, button_text):
            pb = QPushButton(button_text)

            def clicked():
                self.corner_clicked(key)

            pb.clicked.connect(clicked)
            gl.addWidget(pb, row, 0)
            x_le = QLineEdit("")
            gl.addWidget(x_le, row, 1)
            y_le = QLineEdit("")
            gl.addWidget(y_le, row, 2)
            z_le = QLineEdit("")
            gl.addWidget(z_le, row, 3)
            self.corner_widgets[key] = {
                "pb": pb,
                "x_le": x_le,
                "y_le": y_le,
                "z_le": z_le,
            }

        make_corner_widget("ll", "Lower left")
        row += 1
        make_corner_widget("ul", "Upper left")
        row += 1
        make_corner_widget("lr", "Lower right")
        row += 1

        self.setLayout(gl)

    def corner_clicked(self, key):
        pos_cur = self.ac.motion_thread.pos_cache
        widgets = self.corner_widgets[key]

        x_view = self.x_view()
        im_w_pix, im_h_pix = self.ac.usc.imager.cropped_wh()
        y_view = 1.0 * x_view * im_h_pix / im_w_pix

        # End position has to include the sensor size
        pos = dict(pos_cur)
        if key == "ll":
            pass
        elif key == "ul":
            pos["y"] += y_view
        elif key == "lr":
            pos["x"] += x_view
        else:
            assert 0

        widgets["x_le"].setText('%0.3f' % pos['x'])
        widgets["y_le"].setText('%0.3f' % pos['y'])
        widgets["z_le"].setText('%0.3f' % pos['z'])

    def post_ui_init(self):
        self.position_poll_timer = QTimer()
        self.position_poll_timer.timeout.connect(self.poll_update_pos)
        self.position_poll_timer.start(200)

    def poll_update_pos(self):
        last_pos = self.ac.motion_thread.pos_cache
        if last_pos:
            self.update_pos(last_pos)
        # FIXME: hack to avoid concurrency issues with planner and motion thread fighting
        # merge them together?
        if not self.ac.pt:
            self.ac.motion_thread.update_pos_cache()
        # 400 => 100: allow more frequent updates now that flicker issue is solved
        self.position_poll_timer.start(100)

    def update_pos(self, pos):
        # FIXME: this is causing screen flickering
        # https://github.com/Labsmore/pyuscope/issues/34
        # self.ac.log("update_pos(), %s" % (pos,))
        for axis, axis_pos in pos.items():
            # hack...not all systems use z but is included by default
            if axis == 'z' and axis not in self.axis_pos_label:
                continue
            self.axis_pos_label[axis].setText('%0.3f' % axis_pos)

    def mk_corner_json(self):
        corners = OrderedDict()
        for name, widgets in self.corner_widgets.items():
            try:
                x = float(widgets["x_le"].text())
                y = float(widgets["y_le"].text())
                z = float(widgets["z_le"].text())
            except ValueError:
                self.ac.log("Bad scan x/y")
                return None
            corners[name] = {"x": x, "y": y, "z": z}

        return corners

    def get_current_scan_config(self):
        corner_json = self.mk_corner_json()
        if not corner_json:
            return

        objective = self.get_objective()
        pconfig = microscope_to_planner_config(self.ac.usj,
                                               objective=objective,
                                               corners=corner_json)

        self.ac.update_pconfig(pconfig)

        # Ignored app specific metadata
        pconfig["app"] = {
            "app": "argus",
            "objective": objective,
            "microscope": self.ac.microscope,
        }

        out_dir = self.get_out_dir()
        if os.path.exists(out_dir):
            self.ac.log("Refusing to create config: dir already exists: %s" %
                        out_dir)
            return None

        return {
            "pconfig": pconfig,
            "out_dir": out_dir,
        }

    def set_start_pos(self):
        '''
        try:
            lex = float(self.plan_x0_le.text())
        except ValueError:
            self.ac.log('WARNING: bad X value')

        try:
            ley = float(self.plan_y0_le.text())
        except ValueError:
            self.ac.log('WARNING: bad Y value')
        '''
        # take as upper left corner of view area
        # this is the current XY position
        pos = self.ac.motion_thread.pos_cache
        #self.ac.log("Updating start pos w/ %s" % (str(pos)))
        self.plan_x0_le.setText('%0.3f' % pos['x'])
        self.plan_y0_le.setText('%0.3f' % pos['y'])

    def x_view(self):
        # XXX: maybe put better abstraction on this
        return self.objective_widget.obj_config["x_view"]

    def set_end_pos(self):
        # take as lower right corner of view area
        # this is the current XY position + view size
        pos = self.ac.motion_thread.pos_cache
        #self.ac.log("Updating end pos from %s" % (str(pos)))
        x_view = self.x_view()
        im_w_pix, im_h_pix = self.ac.usc.imager.cropped_wh()
        y_view = 1.0 * x_view * im_h_pix / im_w_pix
        x1 = pos['x'] + x_view
        y1 = pos['y'] + y_view
        self.plan_x1_le.setText('%0.3f' % x1)
        self.plan_y1_le.setText('%0.3f' % y1)


"""
Monitors the current scan
Set output job name
"""


class ScanWidget(AWidget):
    def __init__(self,
                 ac,
                 go_currnet_pconfig,
                 setControlsEnabled,
                 parent=None):
        super().__init__(ac=ac, parent=parent)
        self.position_poll_timer = None
        self.go_currnet_pconfig = go_currnet_pconfig
        self.setControlsEnabled = setControlsEnabled

    def initUI(self):
        """
        Line up Go/Stop w/ "Job name" to make visually appealing
        """
        def getProgressLayout():
            layout = QHBoxLayout()

            self.go_pause_pb = QPushButton("Go")
            self.go_pause_pb.clicked.connect(self.go_pause_clicked)
            self.go_pause_pb.setIcon(QIcon(config.GUI.icon_files['go']))
            layout.addWidget(self.go_pause_pb)

            self.stop_pb = QPushButton("Stop")
            self.stop_pb.clicked.connect(self.stop_clicked)
            self.stop_pb.setIcon(QIcon(config.GUI.icon_files['stop']))
            layout.addWidget(self.stop_pb)

            layout.addWidget(QLabel('Dry?'))
            self.dry_cb = QCheckBox()
            self.dry_cb.setChecked(self.ac.usc.app("argus").dry_default())
            layout.addWidget(self.dry_cb)

            self.progress_bar = QProgressBar()
            layout.addWidget(self.progress_bar)

            return layout

        def getScanNameWidget():
            name = self.ac.usc.app("argus").scan_name_widget()
            if name == "simple":
                return SimpleScanNameWidget()
            elif name == "sipr0n":
                return SiPr0nScanNameWidget()
            else:
                raise ValueError(name)

        layout = QVBoxLayout()
        gb = QGroupBox("Scan")
        self.jobName = getScanNameWidget()
        layout.addWidget(self.jobName)
        layout.addLayout(getProgressLayout())
        gb.setLayout(layout)

        layout = QVBoxLayout()
        layout.addWidget(gb)
        self.setLayout(layout)

    def post_ui_init(self):
        pass

    def dry(self):
        return self.dry_cb.isChecked()

    def processCncProgress(self, state):
        """
        pictures_to_take, pictures_taken, image, first
        """
        if state["type"] == "begin":
            self.progress_bar.setMinimum(0)
            self.progress_bar.setMaximum(state["images_to_capture"])
            self.progress_bar.setValue(0)
            self.bench = Benchmark(state["images_to_capture"])
        elif state["type"] == "image":
            #self.ac.log('took %s at %d / %d' % (image, pictures_taken, pictures_to_take))
            self.bench.set_cur_items(state["images_captured"])
            self.ac.log('Captured: %s' % (state["image_filename_rel"], ))
            self.ac.log('%s' % (str(self.bench)))
            self.progress_bar.setValue(state["images_captured"])
        else:
            pass

    def plannerDone(self, run_next=True):
        self.ac.log('RX planner done')
        self.go_pause_pb.setText("Go")
        # Cleanup camera objects
        self.log_fd = None
        self.ac.pt = None

        # More scans?
        if run_next and self.scan_configs:
            self.run_next_scan_config()
        else:
            self.scan_configs = None
            self.setControlsEnabled(True)
            # Prevent accidental start after done
            self.dry_cb.setChecked(True)

            # Return to normal state if HDR was enabled
            self.ac.control_scroll.set_push_gui(True)
            self.ac.control_scroll.set_push_prop(True)

    def run_next_scan_config(self):
        try:
            scan_config = self.scan_configs[0]
            del self.scan_configs[0]

            dry = self.dry()

            out_dir = scan_config["out_dir"]
            pconfig = scan_config["pconfig"]
            if not dry:
                os.mkdir(out_dir)

            if "hdr" in pconfig["imager"] and self.ac.auto_exposure_enabled():
                self.ac.log(
                    "Run aborted: requested HDR but auto-exposure enabled")
                self.plannerDone(run_next=False)
                return

            def emitCncProgress(state):
                self.ac.cncProgress.emit(state)

            # not sure if this is the right place to add this
            # plannerj['copyright'] = "&copy; %s John McMaster, CC-BY" % datetime.datetime.today().year

            # Directly goes into planner constructor
            # Make sure everything here is thread safe
            # log param is handled by other thread
            planner_args = {
                # Simple settings written to disk, no objects
                "pconfig": pconfig,
                "motion": self.ac.motion_thread.get_planner_motion(),

                # Typically GstGUIImager
                # Will be offloaded to its own thread
                # Operations must be blocking
                # We enforce that nothing is running and disable all CNC GUI controls
                "imager": self.ac.imager,
                "out_dir": out_dir,

                # Includes microscope.json in the output
                "meta_base": {
                    "microscope": self.ac.usj
                },

                # Set to true if should try to mimimize hardware actions
                "dry": dry,
                # "overwrite": False,
                #"verbosity": 2,
            }

            self.ac.pt = PlannerThread(self,
                                       planner_args,
                                       progress_cb=emitCncProgress)
            self.ac.pt.log_msg.connect(self.ac.log)
            self.ac.pt.plannerDone.connect(self.plannerDone)
            self.setControlsEnabled(False)
            if dry:
                self.log_fd = StringIO()
            else:
                self.log_fd = open(os.path.join(out_dir, "log.txt"), "w")

            self.go_pause_pb.setText("Pause")

            hdr = pconfig["imager"].get("hdr")
            if hdr:
                # Actively driving properties during operation may cause signal thrashing
                # Only take explicit external updates
                # GUI will continue to update to reflect state though
                self.ac.control_scroll.set_push_gui(False)
                self.ac.control_scroll.set_push_prop(False)

            self.ac.pt.start()
        except:
            self.plannerDone(run_next=False)
            raise

    def go_scan_configs(self, scan_configs):
        if not scan_configs:
            return

        self.scan_configs = list(scan_configs)
        if not self.ac.is_idle():
            return

        if self.ac.auto_exposure_enabled():
            self.ac.log(
                "WARNING: auto-exposure is enabled. This may result in an unevently exposed panorama"
            )

        # If user had started some movement before hitting run wait until its done
        dbg("Waiting for previous movement (if any) to cease")
        # TODO: make this not block GUI
        self.ac.motion_thread.wait_idle()

        dry = self.dry()
        if dry:
            dbg('Dry run checked')

        base_out_dir = self.ac.usc.app("argus").scan_dir()
        if not dry and not os.path.exists(base_out_dir):
            os.mkdir(base_out_dir)

        # Kick off first job
        self.run_next_scan_config()

    def go_pause_clicked(self):
        # CNC already running? pause/continue
        if self.ac.pt:
            # Pause
            if self.ac.pt.is_paused():
                self.go_pause_pb.setText("Pause")
                self.ac.pt.unpause()
            else:
                self.go_pause_pb.setText("Continue")
                self.ac.pt.pause()
        # Go go go!
        else:
            self.go_currnet_pconfig()

    def stop_clicked(self):
        if self.ac.pt:
            self.ac.log('Stop requested')
            self.ac.pt.stop()


def awidgets_initUI(awidgets):
    for awidget in awidgets:
        awidget.initUI()


def awidgets_post_ui_init(awidgets):
    for awidget in awidgets:
        awidget.post_ui_init()


class MainTab(ArgusTab):
    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)

        self.log_fd = None
        # must be created early to accept early logging
        # not displayed until later though
        self.log_widget = QTextEdit()
        # Is this used for something?
        self.log_widget.setObjectName("log_widget")
        # Special case for logging that might occur out of thread
        self.ac.log_msg.connect(self.log)

        self.snapshot_widget = SnapshotWidget(ac=ac)
        self.objective_widget = ObjectiveWidget(ac=ac)
        self.video_widget = FullROIWidget(ac=ac)

        self.planner_widget_tabs = QTabWidget()
        # First sets up algorithm specific parameters
        # Second is a more generic monitoring / control widget
        self.scan_widget = ScanWidget(
            ac=ac,
            go_currnet_pconfig=self.go_currnet_pconfig,
            setControlsEnabled=self.setControlsEnabled)
        # FIXME: integrate better
        if os.getenv("XY3P") == "Y":
            self.planner_widget = XYPlanner3PWidget(
                ac=ac,
                scan_widget=self.scan_widget,
                objective_widget=self.objective_widget)
        else:
            self.planner_widget = XYPlanner2PWidget(
                ac=ac,
                scan_widget=self.scan_widget,
                objective_widget=self.objective_widget)

        self.motion_widget = MotionWidget(ac=self.ac,
                                          motion_thread=self.ac.motion_thread,
                                          usc=self.ac.usc,
                                          log=self.ac.log)

        self.awidgets = [
            self.snapshot_widget,
            self.objective_widget,
            self.video_widget,
            self.planner_widget,
            self.scan_widget,
            self.motion_widget,
        ]

    def initUI(self):
        def get_axes_gb():
            layout = QVBoxLayout()
            # TODO: support other planner sources (ex: 3 point)
            if os.getenv("XY3P") == "Y":
                self.planner_widget_tabs.addTab(self.planner_widget, "XY3P")
            else:
                self.planner_widget_tabs.addTab(self.planner_widget, "XY2P")
            layout.addWidget(self.planner_widget_tabs)
            layout.addWidget(self.motion_widget)
            gb = QGroupBox("Motion")
            gb.setLayout(layout)
            return gb

        def get_bottom_layout():
            layout = QHBoxLayout()
            layout.addWidget(get_axes_gb())

            def get_lr_layout():
                layout = QVBoxLayout()
                layout.addWidget(self.snapshot_widget)
                layout.addWidget(self.scan_widget)
                return layout

            layout.addLayout(get_lr_layout())
            return layout

        awidgets_initUI(self.awidgets)

        layout = QVBoxLayout()
        dbg("get_config_layout()")
        layout.addWidget(self.objective_widget)
        dbg("get_video_layout()")
        layout.addWidget(self.video_widget)
        dbg("get_bottom_layout()")
        layout.addLayout(get_bottom_layout())
        self.log_widget.setReadOnly(True)
        layout.addWidget(self.log_widget)

        self.setLayout(layout)

        # Offload callback to GUI thread so it can do GUI ops
        self.ac.cncProgress.connect(self.scan_widget.processCncProgress)

    def post_ui_init(self):
        awidgets_post_ui_init(self.awidgets)

    def log(self, s='', newline=True):
        s = str(s)
        # print("LOG: %s" % s)
        if newline:
            s += '\n'

        c = self.log_widget.textCursor()
        c.clearSelection()
        c.movePosition(QTextCursor.End)
        c.insertText(s)
        self.log_widget.setTextCursor(c)

        if self.log_fd is not None:
            self.log_fd.write(s)
            self.log_fd.flush()

    def go_currnet_pconfig(self):
        self.scan_widget.go_scan_configs(
            [self.planner_widget.get_current_scan_config()])

    def setControlsEnabled(self, yes):
        self.snapshot_widget.snapshot_pb.setEnabled(yes)


class ImagerTab(ArgusTab):
    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)

    def initUI(self):
        # Most of the layout is filled in from the ControlScroll
        self.layout = QVBoxLayout()

        # screws up the original
        self.layout.addWidget(self.ac.vidpip.get_widget("overview2"))
        self.layout.addWidget(self.ac.control_scroll)

        self.setLayout(self.layout)


class BatchImageTab(ArgusTab):
    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)

    def initUI(self):
        self.layout = QVBoxLayout()

        self.add_pb = QPushButton("Add current scan")
        self.layout.addWidget(self.add_pb)
        self.add_pb.clicked.connect(self.add_clicked)

        self.del_pb = QPushButton("Del selected scan")
        self.layout.addWidget(self.del_pb)
        self.del_pb.clicked.connect(self.del_clicked)

        self.del_all_pb = QPushButton("Del all scans")
        self.layout.addWidget(self.del_all_pb)
        self.del_all_pb.clicked.connect(self.del_all_clicked)

        self.run_all_pb = QPushButton("Run all scans")
        self.layout.addWidget(self.run_all_pb)
        self.run_all_pb.clicked.connect(self.run_all_clicked)

        self.layout.addWidget(QLabel("Abort on first failure?"))
        self.abort_cb = QCheckBox()
        self.layout.addWidget(self.abort_cb)

        # FIXME: instead just set to the MainTab's name
        self.scan_name = QLineEdit()

        # Which tab to get config from
        # In advanced setups multiple algorithms are possible
        self.layout.addWidget(QLabel("Planner config source"))
        self.pconfig_sources = []
        self.pconfig_source_cb = QComboBox()
        self.layout.addWidget(self.pconfig_source_cb)

        self.pconfig_cb = QComboBox()
        self.layout.addWidget(self.pconfig_cb)
        self.pconfig_cb.currentIndexChanged.connect(self.update_cb)

        self.display = QTextEdit()
        self.display.setReadOnly(True)
        self.layout.addWidget(self.display)

        self.setLayout(self.layout)

        self.scan_configs = []
        self.scani = 0

    def abort_on_failure(self):
        return self.abort_cb.isChecked()

    def add_pconfig_source(self, widget, name):
        self.pconfig_sources.append(widget)
        self.pconfig_source_cb.addItem(name)

    def update_cb(self):
        if not len(self.scan_configs):
            self.display.setText("None")
            return
        index = self.pconfig_cb.currentIndex()
        scan_config = self.scan_configs[index]
        s = json.dumps(scan_config,
                       sort_keys=True,
                       indent=4,
                       separators=(",", ": "))
        self.display.setPlainText(json.dumps(s))

    def get_scan_config(self):
        mainTab = self.pconfig_sources[self.pconfig_source_cb.currentIndex()]
        return mainTab.planner_widget.get_current_scan_config()

    def add_clicked(self):
        self.scani += 1
        name = str(self.scan_name.text())
        if not name:
            name = "Batch %u" % self.scani
        else:
            name = "Batch %u: %s" % (self.scani, name)
        self.pconfig_cb.addItem(name)
        self.scan_configs.append(self.get_scan_config())
        self.update_cb()

    def del_clicked(self):
        if len(self.scan_configs):
            index = self.pconfig_cb.currentIndex()
            del self.scan_configs[index]
            self.pconfig_cb.removeItem(index)
        self.update_cb()

    def del_all_clicked(self):
        for _i in range(len(self.scan_configs)):
            del self.scan_configs[0]
            self.pconfig_cb.removeItem(0)
        self.update_cb()

    def run_all_clicked(self):
        self.ac.mainTab.scan_widget.go_scan_configs(self.scan_configs)


class AdvancedTab(ArgusTab):
    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)

        layout = QGridLayout()
        row = 0

        def stack_gb():
            layout = QGridLayout()
            row = 0

            if 0:
                # FIXME: dropbox
                # althouh center is probably ideal anyway
                layout.addWidget(QLabel("Start mode: relative or center"), row,
                                 0)
                self.stacker_start_mode_le = QLineEdit("center")
                layout.addWidget(self.stacker_start_mode_le, row, 1)
                row += 1

            layout.addWidget(QLabel("Distance"), row, 0)
            # Is there a reasonable default here?
            self.stacker_distance_le = QLineEdit("0.000")
            layout.addWidget(self.stacker_distance_le, row, 1)
            row += 1

            layout.addWidget(QLabel("Number"), row, 0)
            self.stacker_number_le = QLineEdit("1")
            layout.addWidget(self.stacker_number_le, row, 1)
            row += 1

            gb = QGroupBox("Stacking")
            gb.setLayout(layout)
            return gb

        layout.addWidget(stack_gb(), row, 0)
        row += 1

        layout.addWidget(QLabel("HDR exposure sequence (csv in us)"), row, 0)
        self.hdr_le = QLineEdit("")
        layout.addWidget(self.hdr_le, row, 1)
        row += 1

        # FIXME: display for now, but should make editable
        def planner_gb():
            layout = QGridLayout()
            row = 0
            pconfig = microscope_to_planner_config(self.ac.usj,
                                                   objective={"x_view": None},
                                                   contour={})
            pc = PC(j=pconfig)

            layout.addWidget(QLabel("Border"), row, 0)
            self.gutter_le = QLineEdit("%f" % pc.border())
            self.gutter_le.setReadOnly(True)
            layout.addWidget(self.gutter_le, row, 1)
            row += 1

            layout.addWidget(QLabel("tsettle"), row, 0)
            self.tsettle_le = QLineEdit("%f" % pc.tsettle())
            self.tsettle_le.setReadOnly(True)
            layout.addWidget(self.tsettle_le, row, 1)
            row += 1

            layout.addWidget(QLabel("Ideal overlap X"), row, 0)
            self.overlap_x_le = QLineEdit("%f" % pc.ideal_overlap("x"))
            self.overlap_x_le.setReadOnly(True)
            layout.addWidget(self.overlap_x_le, row, 1)
            row += 1

            layout.addWidget(QLabel("Ideal overlap Y"), row, 0)
            self.overlap_y_le = QLineEdit("%f" % pc.ideal_overlap("y"))
            self.overlap_y_le.setReadOnly(True)
            layout.addWidget(self.overlap_y_le, row, 1)
            row += 1

            layout.addWidget(QLabel("Image scalar"), row, 0)
            self.image_scalar_le = QLineEdit("%f" % pc.image_scalar())
            self.image_scalar_le.setReadOnly(True)
            layout.addWidget(self.image_scalar_le, row, 1)
            row += 1

            layout.addWidget(QLabel("Motion origin"), row, 0)
            self.motion_scalar_le = QLineEdit(pc.motion_origin())
            self.motion_scalar_le.setReadOnly(True)
            layout.addWidget(self.motion_scalar_le, row, 1)
            row += 1

            layout.addWidget(QLabel("Backlash compensation"), row, 0)
            self.backlash_comp_le = QLineEdit(
                str(self.ac.usc.motion.backlash_compensation()))
            self.backlash_comp_le.setReadOnly(True)
            layout.addWidget(self.backlash_comp_le, row, 1)
            row += 1

            backlashes = self.ac.usc.motion.backlash()
            layout.addWidget(QLabel("Backlash X"), row, 0)
            self.backlash_x_le = QLineEdit("%f" % backlashes["x"])
            self.backlash_x_le.setReadOnly(True)
            layout.addWidget(self.backlash_x_le, row, 1)
            row += 1

            layout.addWidget(QLabel("Backlash Y"), row, 0)
            self.backlash_y_le = QLineEdit("%f" % backlashes["y"])
            self.backlash_y_le.setReadOnly(True)
            layout.addWidget(self.backlash_y_le, row, 1)
            row += 1

            layout.addWidget(QLabel("Backlash Z"), row, 0)
            self.backlash_z_le = QLineEdit("%f" % backlashes["z"])
            self.backlash_z_le.setReadOnly(True)
            layout.addWidget(self.backlash_z_le, row, 1)
            row += 1

            gb = QGroupBox("Planner")
            gb.setLayout(layout)
            return gb

        layout.addWidget(planner_gb(), row, 0)
        row += 1

        self.setLayout(layout)

    def update_pconfig_stack(self, pconfig):
        images_per_stack = int(str(self.stacker_number_le.text()))
        if images_per_stack <= 1:
            return
        distance = float(self.stacker_distance_le.text())
        pconfig["points-stacker"] = {
            "number": images_per_stack,
            "distance": distance,
        }

    def update_pconfig_hdr(self, pconfig):
        raw = str(self.hdr_le.text())
        if not raw:
            return
        properties = []
        for val in [int(x) for x in raw.split(",")]:
            properties.append({"expotime": val})
        ret = {
            "properties": properties,
            # this is probably a good approximation for now
            "tsettle": self.usc.planner.hdr_tsettle()
        }
        pconfig["imager"]["hdr"] = ret

    def update_pconfig(self, pconfig):
        self.update_pconfig_stack(pconfig)
        self.update_pconfig_hdr(pconfig)


class USCArgus:
    def __init__(self, j=None):
        """
        j: usj["app"]["argus"]
        """
        self.j = j

    # FIXME: default should be actual max jog rate
    def jog_max(self):
        return int(self.j.get("jog_max", 1000))

    def scan_dir(self):
        """
        Where scan jobs go
        Change to "scan"?
        """
        return self.j.get("scan_dir", "out")

    def snapshot_dir(self):
        return self.j.get("snapshot_dir", "snapshot")

    def dry_default(self):
        """
        Should the dry checkbox be checked:
        -At startup
        -After job complete
        """
        return self.j.get("dry_default", True)

    def scan_name_widget(self):
        """
        simple: file name prefix only (no extension)
        sipr0n: vendor, chipid, layer enforced
        """
        ret = self.j.get("jog_name_widget", "simple")
        if ret not in ("simple", "sipr0n"):
            raise ValueError("Bad scan name widget: %s" % (ret, ))
        return ret

    def show_mdi(self):
        """
        Should argus GUI show MDI?
        For advanced users only
        Bypasses things like gearbox correction
        """
        val = os.getenv("ARGUS_MDI", None)
        if val is not None:
            return bool(val)
        return bool(self.j.get("mdi", 0))

    def jog_min(self):
        return int(self.j.get("jog_min", 1))


def snapshot_fn(user, extension, parent):
    prefix = ''
    # if self.prefix_date_cb.isChecked():
    if 1:
        # 2020-08-12_06-46-21
        prefix = datetime.datetime.utcnow().isoformat().replace(
            'T', '_').replace(':', '-').split('.')[0] + "_"

    mod = None
    while True:
        mod_str = ''
        if mod:
            mod_str = '_%u' % mod
        fn_full = os.path.join(parent, prefix + user + mod_str + extension)
        if os.path.exists(fn_full):
            if mod is None:
                mod = 1
            else:
                mod += 1
            continue
        return fn_full


def scan_dir_fn(user, parent):
    return snapshot_fn(user=user, extension="", parent=parent)


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


class JogSlider(QWidget):
    def __init__(self, usc, parent=None):
        super().__init__(parent=parent)

        self.usc = usc

        # log scaled to slider
        self.jog_min = self.usc.app("argus").jog_min()
        self.jog_max = self.usc.app("argus").jog_max()
        self.jog_cur = None

        self.slider_min = 1
        self.slider_max = 100

        self.layout = QVBoxLayout()

        def labels():
            layout = QHBoxLayout()
            layout.addWidget(QLabel("1"))
            layout.addWidget(QLabel("10"))
            layout.addWidget(QLabel("100"))
            layout.addWidget(QLabel("1000"))
            return layout

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

    def get_val(self):
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


class MotionWidget(AWidget):
    def __init__(self, ac, motion_thread, usc, log, parent=None):
        super().__init__(ac=ac, parent=parent)

        assert self.ac.usc.motion.hal() == "grbl-ser", (
            "FIXME", self.ac.usc.motion.hal())

        self.usc = usc
        self.log = log
        self.motion_thread = motion_thread

        self.axis_map = {
            # Upper left origin
            Qt.Key_A: ("x", -1),
            Qt.Key_D: ("x", 1),
            Qt.Key_S: ("y", -1),
            Qt.Key_W: ("y", 1),
            Qt.Key_Q: ("z", -1),
            Qt.Key_E: ("z", 1),
        }

        self.last_send = time.time()

    def initUI(self):
        self.setWindowTitle('Demo')

        layout = QVBoxLayout()
        self.listener = JogListener("Jog", self)
        layout.addWidget(self.listener)
        self.slider = JogSlider(usc=self.usc)
        layout.addWidget(self.slider)

        def move_abs():
            layout = QHBoxLayout()

            layout.addWidget(QLabel("Absolute move"))
            self.move_abs_le = QLineEdit()
            self.move_abs_le.returnPressed.connect(self.move_abs_le_process)
            layout.addWidget(self.move_abs_le)

            layout.addWidget(QLabel("Backlash compensate?"))
            self.move_abs_backlash_cb = QCheckBox()
            self.move_abs_backlash_cb.setChecked(False)
            layout.addWidget(self.move_abs_backlash_cb)

            return layout

        def mdi():
            layout = QHBoxLayout()
            layout.addWidget(QLabel("MDI"))
            self.mdi_le = QLineEdit()
            self.mdi_le.returnPressed.connect(self.mdi_le_process)
            layout.addWidget(self.mdi_le)
            return layout

        layout.addLayout(move_abs())

        self.mdi_le = None
        if self.usc.app("argus").show_mdi():
            layout.addLayout(mdi())

        # XXX: make this a proper signal emitting changed value
        self.slider.slider.valueChanged.connect(self.sliderChanged)
        self.sliderChanged()

        self.setLayout(layout)

    def move_abs_le_process(self):
        s = str(self.move_abs_le.text())
        try:
            pos = motion_util.parse_move(s)
        except ValueError:
            self.ac.log("Failed to parse move. Need like: X1.0 Y2.4")
            return
        """
        # FIXME: should be able to override?
        if self.move_abs_backlash_cb.isChecked():
            bpos = backlash_move_absolute(
                pos, self.usc.motion.backlash(),
                self.usc.motion.backlash_compensation())
            self.motion_thread.move_relative(bpos)
        """
        self.motion_thread.move_absolute(pos)

    def mdi_le_process(self):
        if self.mdi_le:
            s = str(self.mdi_le.text())
            self.ac.log("Sending MDI: %s" % s)
            self.motion_thread.mdi(s)

    # XXX: make this a proper signal emitting changed value
    def sliderChanged(self):
        self.jog_cur = self.slider.get_val()
        self.motion_thread.set_jog_rate(self.jog_cur)

    def keyPressEventCaptured(self, event):
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

        axis = self.axis_map.get(k, None)
        # print("press %s" % (axis, ))
        # return
        if axis:
            axis, sign = axis
            # print("Key jogging %s%c" % (axis, {1: '+', -1: '-'}[sign]))
            # don't spam events if its not done processing
            if self.motion_thread.qsize() <= 1:
                self.motion_thread.jog({axis: sign})
            else:
                # print("qsize drop jog")
                pass

    def keyReleaseEventCaptured(self, event):
        # Don't move around with moving around text boxes, etc
        # if not self.video_container.hasFocus():
        #    return
        k = event.key()

        # Hmm larger GUI doesn't get these if this handler is active
        if k == Qt.Key_Escape:
            self.motion_thread.stop()

        # Ignore duplicates, want only real presses
        if event.isAutoRepeat():
            return

        axis = self.axis_map.get(k, None)
        # print("release %s" % (axis, ))
        # return
        if axis:
            # print("cancel jog on release")
            self.motion_thread.stop()


class SimpleScanNameWidget(QWidget):
    """
    Job name is whatever the user wants
    """
    def __init__(self, parent=None):
        super().__init__(parent=parent)

        layout = QHBoxLayout()

        layout.addWidget(QLabel("Job name"))
        self.le = QLineEdit("unknown")
        layout.addWidget(self.le)

        self.setLayout(layout)

    def getName(self, parent):
        return scan_dir_fn(user=str(self.le.text()), parent=parent)


class SiPr0nScanNameWidget(QWidget):
    """
    Force a name compatible with siliconpr0n.org naming convention
    """
    def __init__(self, parent=None):
        super().__init__(parent=parent)

        layout = QHBoxLayout()

        # old: freeform
        # layout.addWidget(QLabel('Job name'), 0, 0, 1, 2)
        # self.job_name_le = QLineEdit('default')
        # layout.addWidget(self.job_name_le)

        # Will add _ between elements to make final name

        layout.addWidget(QLabel('Vendor'))
        self.vendor_name_le = QLineEdit('unknown')
        layout.addWidget(self.vendor_name_le)

        layout.addWidget(QLabel('Product'))
        self.product_name_le = QLineEdit('unknown')
        layout.addWidget(self.product_name_le)

        layout.addWidget(QLabel('Layer'))
        self.layer_name_le = QLineEdit('mz')
        layout.addWidget(self.layer_name_le)

        layout.addWidget(QLabel('Ojbective'))
        self.objective_name_le = QLineEdit('unkx')
        layout.addWidget(self.objective_name_le)

        self.setLayout(layout)

    def getName(self, parent):
        # old: freeform
        # return str(self.job_name_le.text())
        vendor = str(self.vendor_name_le.text())
        if not vendor:
            vendor = "unknown"

        product = str(self.product_name_le.text())
        if not product:
            product = "unknown"

        layer = str(self.layer_name_le.text())
        if not layer:
            layer = "unknown"

        objective = str(self.objective_name_le.text())
        if not objective:
            objective = "unkx"

        ret = vendor + "_" + product + "_" + layer + "_" + objective
        return os.path.join(parent, ret)


"""
Common GUI related services to a typical application
Owns core logging, motion, and imaging objects
However how they are displayed and used is up to end applications
"""


class ArgusCommon(QObject):
    """
    was:
    pictures_to_take, pictures_taken, image, first
    """
    cncProgress = pyqtSignal(dict)
    log_msg = pyqtSignal(str)

    # pos = pyqtSignal(int)

    def __init__(self, microscope=None, mw=None):
        QObject.__init__(self)

        self.mw = mw
        self.logs = []
        self.update_pconfigs = []

        self.motion_thread = None
        self.pt = None
        self.microscope = microscope
        self.usj = get_usj(name=microscope)
        self.usc = USC(usj=self.usj)

        self.scan_configs = None
        self.imager = None
        self.vidpip = GstVideoPipeline(usc=self.usc,
                                       overview=True,
                                       overview2=True,
                                       roi=True)
        # FIXME: review sizing
        self.vidpip.size_widgets(frac=0.2)
        # self.capture_sink = Gst.ElementFactory.make("capturesink")

        # TODO: some pipelines output jpeg directly
        # May need to tweak this
        cropped_width, cropped_height = self.usc.imager.cropped_wh()
        self.capture_sink = CaptureSink(width=cropped_width,
                                        height=cropped_height,
                                        raw_input=True)
        assert self.capture_sink
        self.vidpip.player.add(self.capture_sink)
        # jpegenc is probably obsolete here
        # Now data is normalized in CaptureSink to do jpeg conversion etc
        if 1:
            self.vidpip.setupGst(raw_tees=[self.capture_sink])
        else:
            self.jpegenc = Gst.ElementFactory.make("jpegenc")
            self.vidpip.player.add(self.jpegenc)
            self.vidpip.setupGst(raw_tees=[self.jpegenc])
            self.jpegenc.link(self.capture_sink)

        # Special UI initialization
        # Requires video pipeline already setup
        self.control_scroll = get_control_scroll(self.vidpip, usc=self.usc)

        self.pt = None
        motion = get_motion_hal(usc=self.usc, log=self.emit_log)
        # motion.progress = self.hal_progress
        self.motion_thread = MotionThread(motion=motion)
        self.motion_thread.log_msg.connect(self.log)

    def initUI(self):
        self.vidpip.setupWidgets()

    def post_ui_init(self):
        self.control_scroll.run()
        self.vid_fd = None
        self.motion_thread.start()
        self.vidpip.run()
        self.init_imager()

    def shutdown(self):
        if self.motion_thread:
            self.motion_thread.hal.ar_stop()
            self.motion_thread.thread_stop()
            self.motion_thread = None
        if self.pt:
            self.pt.stop()
            self.pt = None

    def init_imager(self):
        source = self.vidpip.source_name
        self.log('Loading imager %s...' % source)
        # Gst is pretty ingrained for the GUI
        #
        self.imager = plugin.get_gui_imager(source, self)

    def emit_log(self, s='', newline=True):
        # event must be omitted from the correct thread
        # however, if it hasn't been created yet assume we should log from this thread
        self.log_msg.emit(s)

    def log(self, s='', newline=True):
        for log in self.logs:
            log(s, newline=newline)

    def update_pconfig(self, pconfig):
        for update_pconfig in self.update_pconfigs:
            update_pconfig(pconfig)

    def auto_exposure_enabled(self):
        # XXX: not portable, touptek only
        return self.control_scroll.prop_read("auto-exposure")

    # FIXME: better abstraction
    def is_idle(self):
        if not self.mw.mainTab.snapshot_widget.snapshot_pb.isEnabled():
            self.log("Wait for snapshot to complete before CNC'ing")
            return False
        return True


class MainWindow(QMainWindow):
    def __init__(self, microscope=None, verbose=False):
        QMainWindow.__init__(self)
        self.verbose = verbose
        self.ac = None
        self.init_objects(microscope=microscope)
        self.ac.logs.append(self.mainTab.log)
        self.initUI()
        self.post_ui_init()

    def __del__(self):
        self.shutdown()

    def shutdown(self):
        try:
            if self.ac:
                self.ac.shutdown()
        except AttributeError:
            pass

    def init_objects(self, microscope=None):
        self.ac = ArgusCommon(microscope=microscope, mw=self)
        self.ac.usc.app_register("argus", USCArgus)
        # Tabs
        self.mainTab = MainTab(ac=self.ac, parent=self)
        self.imagerTab = ImagerTab(ac=self.ac, parent=self)
        self.batchTab = BatchImageTab(ac=self.ac, parent=self)
        self.advancedTab = AdvancedTab(ac=self.ac, parent=self)
        self.ac.mainTab = self.mainTab

    def initUI(self):
        self.ac.initUI()
        self.setWindowTitle("pyuscope")
        self.setWindowIcon(QIcon(config.GUI.icon_files["logo"]))
        """
        tabs = [
            ("Main", MainTab),
            ("Imager", MainTab),
            ("Batch", MainTab),
            ("Advanced", MainTab),
            ]
        """
        self.tabs = QTabWidget()

        # Setup UI based on objects
        self.mainTab.initUI()
        self.tabs.addTab(self.mainTab, "Main")
        self.imagerTab.initUI()
        self.tabs.addTab(self.imagerTab, "Imager")
        self.batchTab.initUI()
        self.tabs.addTab(self.batchTab, "Batch")
        self.advancedTab.initUI()
        self.tabs.addTab(self.advancedTab, "Advanced")

        self.batchTab.add_pconfig_source(self.mainTab, "Main tab")

        self.setCentralWidget(self.tabs)
        self.showMaximized()
        self.show()

        dbg("initUI done")

    def post_ui_init(self):
        self.ac.update_pconfigs.append(self.advancedTab.update_pconfig)
        # Start services
        self.ac.post_ui_init()
        # Tabs
        self.mainTab.post_ui_init()
        self.imagerTab.post_ui_init()
        self.batchTab.post_ui_init()
        self.advancedTab.post_ui_init()

    def keyPressEvent(self, event):
        k = event.key()
        if k == Qt.Key_Escape:
            self.mainTab.stop()


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
        print(traceback.format_exc(-1))
        error(str(e))


if __name__ == '__main__':
    main()
