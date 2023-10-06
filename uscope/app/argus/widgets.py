from uscope.config import PC, get_data_dir
from uscope.benchmark import Benchmark
from uscope.app.argus.threads import QPlannerThread, StitcherThread
from uscope.planner.planner_util import microscope_to_planner_config
from uscope import config
from uscope.motion import motion_util
from uscope.imagep.util import RC_CONST
import json
import json5
from collections import OrderedDict
from uscope.cloud_stitch import CSInfo

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import time
import datetime
import os.path
from PIL import Image
from io import StringIO
import math
"""
Argus Widget
"""


class EventSequener:
    def __init__(self, args):
        self.args = args
        # XXX: some sort of timeout?

    def run(self, *_args, **_kwargs):
        print("EventSequener: %u remaining" % len(self.args))
        if len(self.args) == 0:
            return
        func, kwargs = self.args[0]
        del self.args[0]
        # Require a callback only when more events to trigger?
        # maybe better to have a final timeout close out
        func(**kwargs, callback=self.run)


# TODO: register events in lieu of callbacks
class AWidget(QWidget):
    def __init__(self, ac, parent=None):
        """
        Low level objects should be instantiated here
        """
        super().__init__(parent=parent)
        self.ac = ac
        self.awidgets = OrderedDict()

    def add_awidget(self, name, awidget):
        assert name not in self.awidgets, name
        self.awidgets[name] = awidget

    def initUI(self):
        """
        Called to initialize GUI elements
        """
        for awidget in self.awidgets.values():
            awidget.initUI()

    def post_ui_init(self):
        """
        Called after all GUI elements are instantiated
        """
        for awidget in self.awidgets.values():
            awidget.post_ui_init()

    def shutdown(self):
        """
        Called when GUI is shutting down
        """
        for awidget in self.awidgets.values():
            awidget.shutdown()

    def cache_save(self, cachej):
        """
        Called when saving GUI state to file
        Add your state to JSON object j
        """
        for awidget in self.awidgets.values():
            awidget.cache_save(cachej)

    def cache_load(self, cachej):
        """
        Called when loading GUI state from file
        Read your state from JSON object j
        """
        for awidget in self.awidgets.values():
            awidget.cache_load(cachej)

    def poll_misc(self):
        for awidget in self.awidgets.values():
            awidget.poll_misc()


class ArgusTab(AWidget):
    pass


"""
Select objective and show FoV
"""


class ObjectiveWidget(AWidget):
    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)
        self.objective_name_le = None
        self.obj_config = None
        self.obj_configi = None
        self.default_objective_index = 0

    def initUI(self):
        cl = QGridLayout()

        row = 0
        l = QLabel("Objective")
        cl.addWidget(l, row, 0)

        self.obj_cb = QComboBox()
        cl.addWidget(self.obj_cb, row, 1)
        self.obj_view = QLabel("")
        cl.addWidget(self.obj_view, row, 2)
        row += 1

        self.setLayout(cl)

    def post_ui_init(self):
        self.reload_obj_cb()

    def reload_obj_cb(self):
        '''Re-populate the objective combo box'''
        self.obj_cb.clear()
        for objective in self.ac.usc.get_scaled_objectives(self.ac.microscope):
            self.obj_cb.addItem(objective['name'])

        if self.default_objective_index >= len(
                self.ac.usc.get_scaled_objectives(self.ac.microscope)):
            self.ac.log(
                "Warning: Argus cache loaded invalid selected objective: wanted index %s but have %s"
                % (self.obj_configi, self.obj_cb.count()))
            self.default_objective_index = 0

        self.obj_cb.currentIndexChanged.connect(self.update_obj_config)
        self.obj_cb.setCurrentIndex(self.default_objective_index)
        self.update_obj_config()

    def update_obj_config(self):
        '''Make resolution display reflect current objective'''
        self.obj_configi = self.obj_cb.currentIndex()
        self.obj_config = self.ac.usc.get_scaled_objective(
            self.ac.microscope, self.obj_configi)
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
        self.ac.objectiveChanged.emit(self.obj_config)

    """
    FIXME: these are microscpoe specific
    Probably need a per microscope cache for this
    Might also be better to select by name
    """

    def cache_save(self, cachej):
        cachej["objective"] = {
            "index": self.obj_configi,
        }

    def cache_load(self, cachej):
        j = cachej.get("objective", {})
        index = j.get("index", 0)
        try:
            self.default_objective_index = int(index)
        except Exception:
            print(f"WARNING: invalid objective index: {index}")


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
        # joystick can stack up events
        if not self.snapshot_pb.isEnabled():
            self.ac.log("Snapshot already requested. Please wait before requesting another")
            return

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

        image = self.ac.capture_sink.pop_image(image_id)
        """
        # FIXME: should unify this with Imager better
        # For now assertion guards help make sure pipeline is correct
        factor = self.ac.usc.imager.scalar()
        image = get_scaled(image, factor, filt=Image.NEAREST)
        expected_wh = self.ac.usc.imager.final_wh()
        assert expected_wh[0] == image.size[0] and expected_wh[
            1] == image.size[
                1], "Unexpected image size: expected %s, got %s" % (
                    expected_wh, image.size)
        fn_full = self.snapshot_fn()
        """

        self.ac.log(f"Snapshot: image received, post-processing")

        options = {}
        options["image"] = image
        options["save_filename"] = self.snapshot_fn()
        extension = str(self.snapshot_suffix_le.text())
        if extension == ".jpg":
            options["save_quality"] = self.ac.usc.imager.save_quality()
        options["scale_factor"] = self.ac.usc.imager.scalar()
        options["scale_expected_wh"] = self.ac.usc.imager.final_wh()

        def callback(command, ret_e):
            if type(ret_e) is Exception:
                self.ac.log(f"Snapshot: save failed")
            else:
                filename = command["options"]["save_filename"]
                self.ac.log(f"Snapshot: saved to {filename}")

        self.ac.image_processing_thread.process_snapshot(options=options,
                                                         callback=callback)
        self.snapshot_pb.setEnabled(True)

    def post_ui_init(self):
        pass

    def cache_save(self, cachej):
        cachej["snapshot"] = {
            "file_name": str(self.snapshot_fn_le.text()),
        }

    def cache_load(self, cachej):
        j = cachej.get("snapshot", {})
        self.snapshot_fn_le.setText(j.get("file_name", "snapshot"))


"""
Provides camera overview and ROI side by side
"""


class PlannerWidget(AWidget):
    def __init__(self, ac, scan_widget, objective_widget, parent=None):
        super().__init__(ac=ac, parent=parent)
        self.scan_widget = scan_widget
        self.objective_widget = objective_widget

    # FIXME: abstract these better

    def get_out_dir_j(self):
        j = self.scan_widget.jobNameWidget.getNameJ()
        out_dir = out_dir_config_to_dir(j, self.ac.usc.app("argus").scan_dir())
        if os.path.exists(out_dir):
            self.ac.log("Refusing to create config: dir already exists: %s" %
                        out_dir)
            return None
        return j

    def get_objective(self):
        return self.objective_widget.obj_config

    def show_minmax(self, visible):
        for label in self.axis_machine_min_label.values():
            label.setVisible(visible)
        for label in self.axis_soft_min_label.values():
            label.setVisible(visible)
        for label in self.axis_soft_max_label.values():
            label.setVisible(visible)
        for label in self.axis_machine_max_label.values():
            label.setVisible(visible)
        for label in self.minmax_labels:
            label.setVisible(visible)

    def fill_minmax(self):
        """
        These values are fixed per machine as currently configured
        As in you can't change soft limit after launch
        """

        # Access motion before motion thread starts while its still thread safe
        # although it should be cached at startup
        machine_limits = self.ac.motion_thread.motion.get_machine_limits()
        soft_limits = self.ac.motion_thread.motion.get_soft_limits()

        # Sanity check
        # 2023-09-18: VM1 Z axis sign issue
        # want soft limit, so disable this check
        if 0:
            for axis in self.ac.motion_thread.motion.axes():
                machine_min = machine_limits["mins"].get(axis)
                soft_min = soft_limits["mins"].get(axis)
                if machine_min is not None and soft_min is not None:
                    assert machine_min <= soft_min, f"Invalid limit min config on {axis}, expect {machine_min} <= {soft_min}"

                machine_max = machine_limits["maxs"].get(axis)
                soft_max = soft_limits["maxs"].get(axis)
                if machine_max is not None and soft_max is not None:
                    assert machine_max >= soft_max, f"Invalid limit max config on {axis}, expect {machine_max} >= {soft_max}"

        def fill_group(label_group, limits_group, axis):
            val = limits_group.get(axis, None)
            if val is None:
                s = "None"
            else:
                s = self.ac.usc.motion.format_position(axis, val)
            label = label_group[axis]
            label.setText(s)

        for axis in "xyz":
            fill_group(self.axis_machine_min_label,
                       machine_limits.get("mins", {}), axis)
            fill_group(self.axis_soft_min_label, soft_limits.get("mins", {}),
                       axis)
            fill_group(self.axis_soft_max_label, soft_limits.get("maxs", {}),
                       axis)
            fill_group(self.axis_machine_max_label,
                       machine_limits.get("maxs", {}), axis)

    def add_axis_rows(self, gl, row):
        gl.addWidget(QLabel("X (mm)"), row, 1)
        gl.addWidget(QLabel("Y (mm)"), row, 2)
        gl.addWidget(QLabel("Z (mm)"), row, 3)
        row += 1

        self.minmax_labels = []

        def add_axis_row(label_dict, label):
            nonlocal row

            def minmax_label(txt):
                label = QLabel(txt)
                self.minmax_labels.append(label)
                return label

            gl.addWidget(minmax_label(label), row, 0)
            label = QLabel("?")
            gl.addWidget(label, row, 1)
            label_dict['x'] = label
            label = QLabel("?")
            gl.addWidget(label, row, 2)
            label_dict['y'] = label
            label = QLabel("?")
            gl.addWidget(label, row, 3)
            label_dict['z'] = label
            row += 1

        self.axis_machine_min_label = {}
        add_axis_row(self.axis_machine_min_label, "Machine Minimum")
        self.axis_soft_min_label = {}
        add_axis_row(self.axis_soft_min_label, "Soft Minimum")
        self.axis_pos_label = {}
        add_axis_row(self.axis_pos_label, "Current")
        self.axis_soft_max_label = {}
        add_axis_row(self.axis_soft_max_label, "Soft Maximum")
        self.axis_machine_max_label = {}
        add_axis_row(self.axis_machine_max_label, "Machine Maximum")

        # Useful but clutters the UI a bit
        # Give a drop down option for now
        # but show if you want "programmer GUI"
        self.show_minmax(config.bc.dev_mode())
        # self.show_minmax(True)

        return row

    def post_ui_init(self):
        self.fill_minmax()


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
        self.pb_gos = {}

        row = self.add_axis_rows(gl, row)

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
        self.plan_x0_le = QLineEdit("")
        gl.addWidget(self.plan_x0_le, row, 1)
        self.plan_y0_le = QLineEdit("")
        gl.addWidget(self.plan_y0_le, row, 2)
        self.pb_gos["0"] = {
            "x_le": self.plan_x0_le,
            "y_le": self.plan_y0_le,
            "pb": QPushButton("MoveTo"),
        }
        gl.addWidget(self.pb_gos["0"]["pb"], row, 3)
        row += 1

        self.plan_end_pb = QPushButton(end_label)
        self.plan_end_pb.clicked.connect(self.set_end_pos)
        self.plan_end_pb.setIcon(QIcon(end_icon))
        gl.addWidget(self.plan_end_pb, row, 0)
        self.plan_x1_le = QLineEdit("")
        gl.addWidget(self.plan_x1_le, row, 1)
        self.plan_y1_le = QLineEdit("")
        gl.addWidget(self.plan_y1_le, row, 2)
        self.pb_gos["1"] = {
            "x_le": self.plan_x1_le,
            "y_le": self.plan_y1_le,
            "pb": QPushButton("MoveTo"),
        }
        gl.addWidget(self.pb_gos["1"]["pb"], row, 3)
        row += 1

        for corner_name in ("0", "1"):

            def connect_corner_widget(corner_name, ):
                def go_clicked():
                    pos = self.get_corner_move_pos(corner_name)
                    if pos is not None:
                        self.ac.motion_thread.move_absolute(pos)

                self.pb_gos[corner_name]["pb"].clicked.connect(go_clicked)

            connect_corner_widget(corner_name)

        self.setLayout(gl)

    def cache_save(self, cachej):
        cachej["XY2P"] = {
            "x0": str(self.plan_x0_le.text()),
            "y0": str(self.plan_y0_le.text()),
            "x1": str(self.plan_x1_le.text()),
            "y1": str(self.plan_y1_le.text()),
        }

    def cache_load(self, cachej):
        j = cachej.get("XY2P", {})
        self.plan_x0_le.setText(j.get("x0", ""))
        self.plan_y0_le.setText(j.get("y0", ""))
        self.plan_x1_le.setText(j.get("x1", ""))
        self.plan_y1_le.setText(j.get("y1", ""))

    def poll_misc(self):
        last_pos = self.ac.motion_thread.pos_cache
        if last_pos:
            self.update_pos(last_pos)

    def update_pos(self, pos):
        # FIXME: this is causing screen flickering
        # https://github.com/Labsmore/pyuscope/issues/34
        for axis, axis_pos in pos.items():
            # hack...not all systems use z but is included by default
            if axis == 'z' and axis not in self.axis_pos_label:
                continue
            self.axis_pos_label[axis].setText(
                self.ac.usc.motion.format_position(axis, axis_pos))

    def mk_contour_json(self):
        pos0 = self.get_corner_planner_pos("0")
        if pos0 is None:
            return
        pos1 = self.get_corner_planner_pos("1")
        if pos1 is None:
            return

        # Planner will sort order as needed
        ret = {"start": pos0, "end": pos1}

        return ret

    def get_current_scan_config(self):
        contour_json = self.mk_contour_json()
        if not contour_json:
            return

        objective = self.get_objective()
        pconfig = microscope_to_planner_config(self.ac.usj,
                                               objective=objective,
                                               contour=contour_json)

        try:
            self.ac.update_pconfig(pconfig)
        # especially ValueError from bad GUI items
        except Exception as e:
            self.log(f"Scan config aborted: {e}")
            return

        # Ignored app specific metadata
        pconfig["app"] = {
            "app": "argus",
            "objective": objective,
            "microscope": self.ac.microscope_name,
        }

        out_dir_config = self.get_out_dir_j()
        if not out_dir_config:
            return

        return {
            "pconfig": pconfig,
            "out_dir_config": out_dir_config,
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
        self.plan_x0_le.setText(
            self.ac.usc.motion.format_position("x", pos["x"]))
        self.plan_y0_le.setText(
            self.ac.usc.motion.format_position("y", pos["y"]))

    def x_view(self):
        # XXX: maybe put better abstraction on this
        return self.objective_widget.obj_config["x_view"]

    def get_view(self):
        x_view = self.x_view()
        im_w_pix, im_h_pix = self.ac.usc.imager.cropped_wh()
        y_view = 1.0 * x_view * im_h_pix / im_w_pix
        return x_view, y_view

    def set_end_pos(self):
        # take as lower right corner of view area
        # this is the current XY position + view size
        pos = self.ac.motion_thread.pos_cache
        #self.ac.log("Updating end pos from %s" % (str(pos)))
        self.plan_x1_le.setText(
            self.ac.usc.motion.format_position("x", pos["x"]))
        self.plan_y1_le.setText(
            self.ac.usc.motion.format_position("y", pos["y"]))

    def get_corner_widget_pos(self, corner_name):
        widgets = self.pb_gos[corner_name]
        try:
            x = float(widgets["x_le"].text().replace(" ", ""))
            y = float(widgets["y_le"].text().replace(" ", ""))
        except ValueError:
            self.ac.log("Bad scan x/y")
            return None

        return {"x": x, "y": y}

    def get_corner_move_pos(self, corner_name):
        return self.get_corner_widget_pos(corner_name)

    def get_corner_planner_pos(self, corner_name):
        pos = self.get_corner_widget_pos(corner_name)
        if pos is None:
            return None
        x_view, y_view = self.get_view()
        # ll
        if corner_name == "0":
            pos["x"] -= x_view / 2
            pos["y"] -= y_view / 2
        # ur
        elif corner_name == "1":
            pos["x"] += x_view / 2
            pos["y"] += y_view / 2
        else:
            assert 0, corner_name
        return pos


class XYPlanner3PWidget(PlannerWidget):
    click_corner = pyqtSignal(tuple)
    go_corner = pyqtSignal(tuple)

    def __init__(self, ac, scan_widget, objective_widget, parent=None):
        super().__init__(ac=ac,
                         scan_widget=scan_widget,
                         objective_widget=objective_widget,
                         parent=parent)

        self.click_corner.connect(self.click_corner_slot)
        self.go_corner.connect(self.go_corner_slot)

    def initUI(self):
        gl = QGridLayout()
        row = 0

        row = self.add_axis_rows(gl, row)

        self.corner_widgets = OrderedDict()

        def make_corner_widget(corner_name, button_text):
            pb_set = QPushButton(button_text)
            pb_go = QPushButton("MoveTo")

            def set_clicked():
                self.corner_clicked(corner_name)

            pb_set.clicked.connect(set_clicked)

            def go_clicked():
                pos = self.get_corner_move_pos(corner_name)
                if pos is not None:
                    self.ac.motion_thread.move_absolute(pos)

            pb_go.clicked.connect(go_clicked)

            gl.addWidget(pb_set, row, 0)
            x_le = QLineEdit("")
            gl.addWidget(x_le, row, 1)
            y_le = QLineEdit("")
            gl.addWidget(y_le, row, 2)
            z_le = QLineEdit("")
            gl.addWidget(z_le, row, 3)
            gl.addWidget(pb_go, row, 4)
            self.corner_widgets[corner_name] = {
                "pb": pb_set,
                "x_le": x_le,
                "y_le": y_le,
                "z_le": z_le,
                "pb_go": pb_go,
            }

        make_corner_widget("ll", "Lower left")
        row += 1
        make_corner_widget("ul", "Upper left")
        row += 1
        make_corner_widget("lr", "Lower right")
        row += 1

        gl.addWidget(QLabel("Track Z?"), row, 0)
        self.track_z_cb = QCheckBox()
        self.track_z_cb.stateChanged.connect(self.track_z_cb_changed)
        self.track_z_cb_changed(None)
        gl.addWidget(self.track_z_cb, row, 1)
        self.track_z_cb.setEnabled(self.ac.microscope.has_z())
        self.pb_afgo = QPushButton("AF + Scan")
        self.pb_afgo.clicked.connect(self.afgo)
        self.pb_afgo.setIcon(QIcon(config.GUI.icon_files['go']))
        gl.addWidget(self.pb_afgo, row, 2)

        row += 1

        self.setLayout(gl)

    def cache_save(self, cachej):
        j1 = {}
        j1["track_z"] = self.track_z_cb.isChecked()
        for group in ("ll", "ul", "lr"):
            widgets = self.corner_widgets[group]
            j2 = {
                "x_le": str(widgets["x_le"].text()),
                "y_le": str(widgets["y_le"].text()),
                "z_le": str(widgets["z_le"].text()),
            }
            j1[group] = j2
        cachej["XY3P"] = j1

    def cache_load(self, cachej):
        j1 = cachej.get("XY3P", {})
        self.track_z_cb.setChecked(j1.get("track_z", 1))
        for group in ("ll", "ul", "lr"):
            widgets = self.corner_widgets[group]
            j2 = j1.get(group, {})
            widgets["x_le"].setText(j2.get("x_le", ""))
            widgets["y_le"].setText(j2.get("y_le", ""))
            widgets["z_le"].setText(j2.get("z_le", ""))

    def moving_z(self):
        return self.track_z_cb.isChecked()

    def track_z_cb_changed(self, arg):
        for corner_widgets in self.corner_widgets.values():
            le = corner_widgets["z_le"]
            if self.moving_z():
                le.setReadOnly(False)
                le.setStyleSheet(None)
            else:
                le.setReadOnly(True)
                le.setStyleSheet("background-color: rgb(240, 240, 240);")

    def poll_misc(self):
        last_pos = self.ac.motion_thread.pos_cache
        if last_pos:
            self.update_pos(last_pos)

    def update_pos(self, pos):
        # FIXME: this is causing screen flickering
        # https://github.com/Labsmore/pyuscope/issues/34
        # self.ac.log("update_pos(), %s" % (pos,))
        for axis, axis_pos in pos.items():
            # hack...not all systems use z but is included by default
            if axis == 'z' and axis not in self.axis_pos_label:
                continue
            self.axis_pos_label[axis].setText(
                self.ac.usc.motion.format_position(axis, axis_pos))

    def mk_corner_json(self):
        corners = OrderedDict()
        for name in self.corner_widgets.keys():
            corner = self.get_corner_planner_pos(name)
            if corner is None:
                return None
            corners[name] = corner

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
            "microscope": self.ac.microscope_name,
        }

        out_dir_config = self.get_out_dir_j()
        if not out_dir_config:
            return

        return {
            "pconfig": pconfig,
            "out_dir_config": out_dir_config,
        }

    def x_view(self):
        # XXX: maybe put better abstraction on this
        return self.objective_widget.obj_config["x_view"]

    def get_view(self):
        x_view = self.x_view()
        im_w_pix, im_h_pix = self.ac.usc.imager.cropped_wh()
        y_view = 1.0 * x_view * im_h_pix / im_w_pix
        return x_view, y_view

    def corner_clicked(self, corner_name):
        pos_cur = self.ac.motion_thread.pos_cache
        widgets = self.corner_widgets[corner_name]

        widgets["x_le"].setText(
            self.ac.usc.motion.format_position("x", pos_cur["x"]))
        widgets["y_le"].setText(
            self.ac.usc.motion.format_position("y", pos_cur["y"]))
        widgets["z_le"].setText(
            self.ac.usc.motion.format_position("z", pos_cur["z"]))

    def get_corner_widget_pos(self, corner_name):
        widgets = self.corner_widgets[corner_name]
        try:
            x = float(widgets["x_le"].text().replace(" ", ""))
            y = float(widgets["y_le"].text().replace(" ", ""))
            if self.moving_z():
                z = float(widgets["z_le"].text().replace(" ", ""))
        except ValueError:
            self.ac.log("Bad scan x/y")
            return None
        corner = {"x": x, "y": y}
        if self.moving_z():
            corner["z"] = z
        return corner

    def get_corner_move_pos(self, corner_name):
        return self.get_corner_widget_pos(corner_name)

    def get_corner_planner_pos(self, corner_name):
        assert self.ac.usc.motion.origin(
        ) == "ll", "fixme: support other origin"

        pos = self.get_corner_widget_pos(corner_name)
        if pos is None:
            return pos
        x_view, y_view = self.get_view()
        if corner_name == "ll":
            pos["x"] -= x_view / 2
            pos["y"] -= y_view / 2
        elif corner_name == "ul":
            pos["x"] -= x_view / 2
            pos["y"] += y_view / 2
        elif corner_name == "lr":
            pos["x"] += x_view / 2
            pos["y"] -= y_view / 2
        else:
            assert 0
        return pos

    # Thread safety to bring back to GUI thread for GUI operations
    def emit_click_corner(self, corner_name, callback=None):
        self.click_corner.emit((corner_name, callback))

    def click_corner_slot(self, args):
        corner_name, callback = args
        self.corner_clicked(corner_name)
        if callback:
            callback()

    def emit_go_corner(self, corner_name, callback=None):
        self.go_corner.emit((corner_name, callback))

    def go_corner_slot(self, args):
        corner_name, callback = args
        pos = self.get_corner_move_pos(corner_name)
        if pos is None:
            raise Exception("Failed to get corner")
        self.ac.motion_thread.move_absolute(pos, callback=callback)

    def afgo(self):
        """
        Autofocus corners and kick off
        """
        ret = QMessageBox.question(
            self, "Start?",
            "Autofocus corners and start? Note: Dry: %s, AE: %s" %
            (self.ac.mainTab.scan_widget.dry(),
             self.ac.auto_exposure_enabled()),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if ret != QMessageBox.Yes:
            return

        # XXX: should settle in between XY moves?
        # autofocus moves anyway so probably fine
        # End at LL corner
        # Could either autofocus at end or sweep back
        # Sweeping back is preferred b/c you can see if die shifted
        self.sequencer = EventSequener([
            (self.emit_go_corner, {
                "corner_name": "ul"
            }),
            (self.ac.image_processing_thread.auto_focus, {}),
            (self.emit_click_corner, {
                "corner_name": "ul"
            }),
            (self.emit_go_corner, {
                "corner_name": "ll"
            }),
            (self.ac.image_processing_thread.auto_focus, {}),
            (self.emit_click_corner, {
                "corner_name": "ll"
            }),
            (self.emit_go_corner, {
                "corner_name": "lr"
            }),
            (self.ac.image_processing_thread.auto_focus, {}),
            (self.emit_click_corner, {
                "corner_name": "lr"
            }),
            # Ensure even dry scan goes back
            (self.emit_go_corner, {
                "corner_name": "ll"
            }),
            (self.ac.mainTab.emit_go_current_pconfig, {})
        ])
        self.sequencer.run()


"""
Monitors the current scan
Set output job name
"""


class ScanWidget(AWidget):
    def __init__(self,
                 ac,
                 go_current_pconfig,
                 setControlsEnabled,
                 parent=None):
        super().__init__(ac=ac, parent=parent)
        self.go_current_pconfig = go_current_pconfig
        self.setControlsEnabled = setControlsEnabled
        self.current_scan_config = None
        self.restore_properties = None
        self.log_fd_scan = None

    def initUI(self):
        """
        Line up Go/Stop w/ "Job name" to make visually appealing
        """
        def getProgressLayout():
            layout = QHBoxLayout()

            self.go_pause_pb = QPushButton("Scan")
            self.go_pause_pb.clicked.connect(self.go_pause_clicked)
            self.go_pause_pb.setIcon(QIcon(config.GUI.icon_files['go']))
            layout.addWidget(self.go_pause_pb)

            self.stop_pb = QPushButton("Stop")
            self.stop_pb.clicked.connect(self.stop_clicked)
            self.stop_pb.setIcon(QIcon(config.GUI.icon_files['stop']))
            layout.addWidget(self.stop_pb)

            layout.addWidget(QLabel("Dry?"))
            self.dry_cb = QCheckBox()
            self.dry_cb.setChecked(self.ac.usc.app("argus").dry_default())
            layout.addWidget(self.dry_cb)

            # Used as generic "should stitch", although is labeled CloudStitch
            layout.addWidget(QLabel("CloudStitch?"))
            self.stitch_cb = QCheckBox()
            self.stitch_cb.setChecked(False)
            layout.addWidget(self.stitch_cb)

            self.progress_bar = QProgressBar()
            layout.addWidget(self.progress_bar)

            return layout

        def getScanNameWidget():
            name = self.ac.usc.app("argus").scan_name_widget()
            if name == "simple":
                return SimpleScanNameWidget(self.ac)
            elif name == "sipr0n":
                return SiPr0nScanNameWidget(self.ac)
            else:
                raise ValueError(name)

        layout = QVBoxLayout()
        gb = QGroupBox("Scan")
        self.jobNameWidget = getScanNameWidget()
        self.awidgets["job_name"] = self.jobNameWidget
        layout.addWidget(self.jobNameWidget)
        layout.addLayout(getProgressLayout())
        gb.setLayout(layout)

        layout = QVBoxLayout()
        layout.addWidget(gb)
        self.setLayout(layout)

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

    def plannerDone(self, result):
        self.ac.log("RX planner done, result: %s" % (result["result"], ))

        # Reset any planner specific configuration
        self.go_pause_pb.setText("Scan")
        # Cleanup camera objects
        if self.log_fd_scan:
            self.log_fd_scan.close()
        self.log_fd_scan = None

        self.ac.planner_thread = None
        last_scan_config = self.current_scan_config
        self.current_scan_config = None

        # Restore defaults between each run
        # Ex: if HDR doesn't clean up simplifies things
        if self.restore_properties:
            self.ac.imager.set_properties(self.restore_properties)

        if result["result"] == "ok":
            self.ac.stitchingTab.scan_completed(last_scan_config, result)

        callback = last_scan_config.get("callback")
        if callback:
            callback(result)

        run_next = result["result"] == "ok" or (
            not self.ac.batchTab.abort_on_failure())
        # More scans?
        if run_next and self.scan_configs and not result.get("hard_fail"):
            self.run_next_scan_config()
        else:
            self.scan_configs = None
            self.restore_properties = None
            self.setControlsEnabled(True)
            # Prevent accidental start after done
            self.dry_cb.setChecked(True)
            self.ac.control_scroll.enable_user_controls(True)

    def run_next_scan_config(self):
        try:
            self.ac.joystick_disable(asneeded=True)
            self.current_scan_config = self.scan_configs[0]
            assert self.current_scan_config
            del self.scan_configs[0]

            dry = self.dry()
            self.current_scan_config["dry"] = dry

            out_dir_config = self.current_scan_config["out_dir_config"]
            out_dir = out_dir_config_to_dir(
                out_dir_config,
                self.ac.usc.app("argus").scan_dir())
            self.current_scan_config["out_dir"] = out_dir
            pconfig = self.current_scan_config["pconfig"]

            if os.path.exists(out_dir):
                self.ac.log("Run aborted: directory already exists")
                self.plannerDone({"result": "init_failure"})
                return
            if not dry:
                os.mkdir(out_dir)

            if "hdr" in pconfig["imager"] and self.ac.auto_exposure_enabled():
                self.ac.log(
                    "Run aborted: requested HDR but auto-exposure enabled")
                self.plannerDone({"result": "init_failure"})
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
                "microscope": self.ac.microscope,

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

            self.ac.planner_thread = QPlannerThread(
                planner_args, progress_cb=emitCncProgress, parent=self)
            self.ac.planner_thread.log_msg.connect(self.ac.log)
            self.ac.planner_thread.plannerDone.connect(self.plannerDone)
            self.setControlsEnabled(False)
            # FIXME: move to planner somehow
            if dry:
                self.log_fd_scan = StringIO()
            else:
                self.log_fd_scan = open(os.path.join(out_dir, "log.txt"), "w")

            self.go_pause_pb.setText("Pause")
            self.ac.control_scroll.enable_user_controls(False)
            self.ac.planner_thread.start()
        except:
            self.plannerDone({"result": "init_failure", "hard_fail": True})
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
        # dbg("Waiting for previous movement (if any) to cease")
        # TODO: make this not block GUI
        self.ac.motion_thread.wait_idle()

        dry = self.dry()
        # dry and dbg('Dry run checked')
        if not dry:
            self.restore_properties = self.ac.imager.get_properties()

        base_out_dir = self.ac.usc.app("argus").scan_dir()
        if not dry and not os.path.exists(base_out_dir):
            os.mkdir(base_out_dir)

        # Kick off first job
        self.run_next_scan_config()

    def go_pause_clicked(self):
        # CNC already running? pause/continue
        if self.ac.planner_thread:
            # Pause
            if self.ac.planner_thread.is_paused():
                self.go_pause_pb.setText("Pause")
                self.ac.planner_thread.unpause()
            else:
                self.go_pause_pb.setText("Continue")
                self.ac.planner_thread.pause()
        # Go go go!
        else:
            self.go_current_pconfig()

    def stop_clicked(self):
        if self.ac.planner_thread:
            self.ac.log('Stop requested')
            self.ac.planner_thread.shutdown()


def awidgets_initUI(awidgets):
    for awidget in awidgets.values():
        awidget.initUI()


def awidgets_post_ui_init(awidgets):
    for awidget in awidgets.values():
        awidget.post_ui_init()


class MainTab(ArgusTab):
    go_current_pconfig_signal = pyqtSignal(tuple)

    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)

        self.log_fd = None

        fn = os.path.join(get_data_dir(), "log.txt")
        existed = os.path.exists(fn)
        self.log_fd = open(fn, "w+")
        if existed:
            self.log_fd.write("\n\n\n")
            self.log_fd.flush()
        # must be created early to accept early logging
        # not displayed until later though
        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        # Is this used for something?
        self.log_widget.setObjectName("log_widget")
        # Special case for logging that might occur out of thread
        self.ac.log_msg.connect(self.log)

        self.snapshot_widget = SnapshotWidget(ac=ac)
        self.add_awidget("snapshot", self.snapshot_widget)
        self.objective_widget = ObjectiveWidget(ac=ac)
        self.add_awidget("objective", self.objective_widget)

        self.planner_widget_tabs = QTabWidget()
        # First sets up algorithm specific parameters
        # Second is a more generic monitoring / control widget
        self.scan_widget = ScanWidget(
            ac=ac,
            go_current_pconfig=self.go_current_pconfig,
            setControlsEnabled=self.setControlsEnabled)
        self.add_awidget("scan", self.scan_widget)
        self.planner_widget_xy2p = XYPlanner2PWidget(
            ac=ac,
            scan_widget=self.scan_widget,
            objective_widget=self.objective_widget)
        self.add_awidget("XY2P", self.planner_widget_xy2p)
        self.planner_widget_xy3p = XYPlanner3PWidget(
            ac=ac,
            scan_widget=self.scan_widget,
            objective_widget=self.objective_widget)
        self.add_awidget("XY3P", self.planner_widget_xy3p)

        self.motion_widget = MotionWidget(ac=self.ac,
                                          motion_thread=self.ac.motion_thread,
                                          usc=self.ac.usc,
                                          log=self.ac.log)
        self.add_awidget("motion", self.motion_widget)

        self.go_current_pconfig_signal.connect(self.go_current_pconfig_slot)

    def initUI(self):
        def get_axes_gb():
            layout = QVBoxLayout()
            # Make this default since its more widely used
            self.planner_widget_tabs.addTab(self.planner_widget_xy3p, "XY3P")
            self.planner_widget_tabs.addTab(self.planner_widget_xy2p, "XY2P")
            layout.addWidget(self.planner_widget_tabs)
            layout.addWidget(self.motion_widget)
            gb = QGroupBox("Motion")
            gb.setLayout(layout)
            return gb

        awidgets_initUI(self.awidgets)

        def left_layout():
            layout = QVBoxLayout()
            layout.addWidget(self.objective_widget)
            layout.addWidget(get_axes_gb())
            layout.addWidget(self.snapshot_widget)
            layout.addWidget(self.scan_widget)
            layout.addWidget(self.log_widget)

            # hmm when the window shrinks these widgets just get really small
            # so this isn't working as intended...
            scroll = QScrollArea()
            scroll.setLayout(layout)
            return scroll

        layout = QHBoxLayout()
        layout.addWidget(left_layout())
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

        self.log_fd.write(s)
        self.log_fd.flush()
        if self.scan_widget.log_fd_scan is not None:
            self.scan_widget.log_fd_scan.write(s)
            self.scan_widget.log_fd_scan.flush()

    def go_current_pconfig(self, callback=None):
        scan_config = self.active_planner_widget().get_current_scan_config()
        if scan_config is None:
            self.ac.log("Failed to get scan config :(")
            return
        # Leave image controls at current value when not batching?
        # Should be a nop but better to just leave alone
        del scan_config["pconfig"]["imager"]["properties"]
        if callback:
            scan_config["callback"] = callback
        self.scan_widget.go_scan_configs([scan_config])

    def emit_go_current_pconfig(self, callback=None):
        self.go_current_pconfig_signal.emit((callback, ))

    def go_current_pconfig_slot(self, args):
        callback, = args
        self.go_current_pconfig(callback=callback)

    def setControlsEnabled(self, yes):
        self.snapshot_widget.snapshot_pb.setEnabled(yes)

    def active_planner_widget(self):
        return self.planner_widget_tabs.currentWidget()

    def cache_save(self, cachej):
        cachej["main"] = {
            "planner_tab": self.planner_widget_tabs.currentIndex(),
        }
        super().cache_save(cachej)

    def cache_load(self, cachej):
        super().cache_load(cachej)
        j = cachej.get("main", {})
        planner = j.get("planner_tab")
        if planner is not None:
            self.planner_widget_tabs.setCurrentIndex(planner)

    def show_minmax(self, visible):
        self.planner_widget_xy2p.show_minmax(visible)
        self.planner_widget_xy3p.show_minmax(visible)


class ImagerTab(ArgusTab):
    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)

    def initUI(self):
        # Most of the layout is filled in from the ControlScroll
        self.layout = QVBoxLayout()

        def hdr_gb():
            layout = QGridLayout()
            row = 0

            layout.addWidget(QLabel("HDR exposure sequence (csv in us)"), row,
                             0)
            self.hdr_le = QLineEdit("")
            layout.addWidget(self.hdr_le, row, 1)
            row += 1

            layout.addWidget(QLabel("Auto-HDR?"), row, 0)
            self.hdr_auto = QCheckBox()
            layout.addWidget(self.hdr_auto, row, 1)
            row += 1

            layout.addWidget(QLabel("+/- stops"), row, 0)
            self.hdr_auto_stops = QLineEdit("1")
            layout.addWidget(self.hdr_auto_stops, row, 1)
            row += 1

            layout.addWidget(QLabel("Stops per exposure"), row, 0)
            self.hdr_auto_stops_per = QLineEdit("2")
            layout.addWidget(self.hdr_auto_stops_per, row, 1)
            row += 1

            gb = QGroupBox("HDR")
            gb.setLayout(layout)
            return gb

        self.layout.addWidget(hdr_gb())
        self.layout.addWidget(self.ac.control_scroll)

        self.setLayout(self.layout)

    def poll_misc(self):
        auto = self.hdr_auto.isChecked()
        self.hdr_auto_stops.setReadOnly(not auto)
        self.hdr_auto_stops_per.setReadOnly(not auto)
        if not auto:
            return

        # val = self.ac.imager.get_property(self.exposure_property)
        val = self.ac.get_exposure()
        if val is None:
            return None
        pm_stops = int(self.hdr_auto_stops.text())
        stops_per = int(self.hdr_auto_stops_per.text())

        hdr_seq = []
        # add in reverse then reverse list
        val_tmp = val
        for _stopi in range(pm_stops):
            val_tmp /= 2**stops_per
            hdr_seq.append(val_tmp)
        hdr_seq.reverse()
        hdr_seq.append(val)
        val_tmp = val
        for _stopi in range(pm_stops):
            val_tmp *= 2**stops_per
            hdr_seq.append(val_tmp)

        le_str = ",".join(["%u" % x for x in hdr_seq])
        self.hdr_le.setText(le_str)

    def update_pconfig_hdr(self, pconfig):
        raw = str(self.hdr_le.text()).strip()
        if not raw:
            return

        try:
            # XXX: consider gain as well
            properties_list = []
            for val in [int(x) for x in raw.split(",")]:
                properties_list.append(
                    {self.ac.get_exposure_disp_property(): val})
        except ValueError:
            self.log("Invalid HDR exposure value")
            raise
        ret = {
            "properties_list": properties_list,
            # this is probably a good approximation for now
            "tsettle": self.ac.usc.kinematics.tsettle_hdr()
        }
        pconfig.setdefault("imager", {})["hdr"] = ret

    def update_pconfig(self, pconfig):
        pconfig.setdefault("imager",
                           {})["properties"] = self.ac.imager.get_properties()
        self.update_pconfig_hdr(pconfig)

    def cache_save(self, cachej):
        cachej["imager"] = {
            "hdr_le": str(self.hdr_le.text()),
        }

    def cache_load(self, cachej):
        j = cachej.get("imager", {})
        self.hdr_le.setText(j.get("hdr_le", ""))


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

        # FIXME: allow editing scan parameters
        """
        self.layout.addWidget(QLabel("Output directory"))
        self.output_dir = QLineEdit()
        self.output_dir.setReadOnly(True)
        """

        # Which tab to get config from
        # In advanced setups multiple algorithms are possible
        self.layout.addWidget(QLabel("Planner config source"))
        self.pconfig_sources = []
        self.pconfig_source_cb = QComboBox()
        self.layout.addWidget(self.pconfig_source_cb)

        self.pconfig_cb = QComboBox()
        self.layout.addWidget(self.pconfig_cb)
        self.pconfig_cb.currentIndexChanged.connect(self.update_state)

        self.display = QTextEdit()
        self.display.setReadOnly(True)
        self.layout.addWidget(self.display)

        self.setLayout(self.layout)

        self.scan_configs = []
        self.scani = 0

        self.batch_cache_load()

    def abort_on_failure(self):
        return self.abort_cb.isChecked()

    def add_pconfig_source(self, widget, name):
        self.pconfig_sources.append(widget)
        self.pconfig_source_cb.addItem(name)

    def update_state(self):
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
        self.batch_cache_save()

    def get_scan_config(self):
        mainTab = self.pconfig_sources[self.pconfig_source_cb.currentIndex()]
        return mainTab.active_planner_widget().get_current_scan_config()

    def add_cb(self, scan_config):
        self.scani += 1
        self.pconfig_cb.addItem(
            f"Batch {self.scani}: " +
            os.path.basename(scan_config["out_dir_config"]["user_basename"]))

    def add_clicked(self):
        scan_config = self.get_scan_config()
        self.add_cb(scan_config)
        self.scan_configs.append(scan_config)
        self.update_state()

    def del_clicked(self):
        if len(self.scan_configs):
            index = self.pconfig_cb.currentIndex()
            del self.scan_configs[index]
            self.pconfig_cb.removeItem(index)
        self.update_state()

    def del_all_clicked(self):
        ret = QMessageBox.question(self, "Delete all",
                                   "Delete all batch jobs?",
                                   QMessageBox.Yes | QMessageBox.Cancel,
                                   QMessageBox.Cancel)
        if ret != QMessageBox.Yes:
            return

        for _i in range(len(self.scan_configs)):
            del self.scan_configs[0]
            self.pconfig_cb.removeItem(0)
        self.update_state()

    def run_all_clicked(self):
        self.ac.mainTab.scan_widget.go_scan_configs(self.scan_configs)

    def batch_cache_save(self):
        s = json.dumps(self.scan_configs,
                       sort_keys=True,
                       indent=4,
                       separators=(",", ": "))
        with open(self.ac.aconfig.batch_cache_fn(), "w") as f:
            f.write(s)

    def batch_cache_load(self):
        fn = self.ac.aconfig.batch_cache_fn()
        if not os.path.exists(fn):
            return
        with open(fn, "r") as f:
            j = json5.load(f)
        self.scan_configs = list(j)
        for scan_config in self.scan_configs:
            self.add_cb(scan_config)
        self.update_state()


class AdvancedTab(ArgusTab):
    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)

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
            self.stack_cb.addItem("C: normal")
            self.stack_cb.addItem("D: double distance")
            self.stack_cb.addItem("E: double steps")
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

        if self.ac.microscope.has_z():
            layout.addWidget(stack_gb(), row, 0)
            row += 1

        # FIXME: display for now, but should make editable
        # Or maybe have it log a report instead of making widgets?

        self.sysinfo_pb = QPushButton("System info")
        self.sysinfo_pb.clicked.connect(self.log_system_info)
        layout.addWidget(self.sysinfo_pb, row, 0)
        row += 1

        self.setLayout(layout)

    def log_system_info(self):
        """
        TODO: make this generic Microscope status report
        TODO: some of these we might want editable live for tuning
        But for now lets just keep a simple report
        """
        self.ac.log("")
        self.ac.log("System configuration / status")
        self.ac.log("Kinematics")
        self.ac.log("  tsettle_motion: %f" % self.ac.kinematics.tsettle_motion)
        self.ac.log("  tsettle_hdr: %f" % self.ac.kinematics.tsettle_hdr)
        self.ac.log("Image")
        self.ac.log("  scalar: %f" % self.ac.usc.imager.scalar())
        self.ac.log("Motion")
        self.ac.log("  origin: %s" % self.ac.usc.motion.origin())
        self.ac.log("  Backlash compensation")
        self.ac.log("    Status: %s" %
                    str(self.ac.usc.motion.backlash_compensation()))
        backlashes = self.ac.usc.motion.backlash()
        self.ac.log("    X: %s" % backlashes["x"])
        self.ac.log("    Y: %s" % backlashes["y"])
        if self.ac.microscope.has_z():
            self.ac.log("    Z: %s" % backlashes["z"])
        self.ac.log("Planner")
        pconfig = microscope_to_planner_config(self.ac.usj,
                                               objective={"x_view": None},
                                               contour={})
        pc = PC(j=pconfig)
        self.ac.log("  Ideal overlap X: %f" % pc.ideal_overlap("x"))
        self.ac.log("  Ideal overlap Y: %f" % pc.ideal_overlap("y"))
        self.ac.log("  XY border: %f" % pc.border())

        # This is in another thread => print race conditions
        # if we need more than one print we'll need to sequence these
        # maybe offload the whole print to another thread
        self.ac.motion_thread.log_info()

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

    def update_pconfig(self, pconfig):
        if self.ac.microscope.has_z():
            self.update_pconfig_stack(pconfig)

    def post_ui_init(self):
        if self.ac.microscope.has_z():
            self.ac.objectiveChanged.connect(self.update_stack_mode)
            self.stack_cb.currentIndexChanged.connect(self.update_stack_mode)
            self.update_stack_mode()

    def cache_save(self, cachej):
        j = {}
        if self.ac.microscope.has_z():
            j["stacking"] = {
                "images_pm": self.stacker_number_le.text(),
                "distance_pm": self.stacker_distance_le.text(),
                "mode_index": self.stack_cb.currentIndex(),
                "drift_correction": self.stack_drift_cb.isChecked(),
            }
        cachej["advanced"] = j

    def cache_load(self, cachej):
        j = cachej.get("advanced", {})
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
        """
        I've found roughly a 3 to 4x multiplier on resolution is a good rule of thumb
        Results in a decent chip image without excessive pictures
        Example: 20x objective is 0.42 NA and I use a 2 um step size
        Resolution @ 400 nm: 1.22 * 400 / (2 * 0.42) = 581 nm
        2000 / 581 = 3.44
        Let's target a ballpark and then round based on machine step size
        Better to round down or to nearest step?
        """
        def calc_normal_step():
            objective_config = self.ac.objective_config()
            na = objective_config["na"]
            # convert nm to mm
            resolution400 = RC_CONST * 400 / (2 * na) / 1e6
            machine_epsilon = self.ac.motion.epsilon()["z"]
            ideal_move = resolution400 * 3.5
            # Now round to nearest machine step
            # Don't go below machine min step size
            steps = max(1, round(ideal_move / machine_epsilon))
            rounded_move = steps * machine_epsilon
            return rounded_move

        def setup_step(distance_mult, step_mult):
            normal_step = calc_normal_step()
            normal_steps = 3
            # We calculated per step, but GUI displays a range
            # Normalize the baseline to total distance
            pm_distance = normal_steps * normal_step * distance_mult
            pm_steps = normal_steps * step_mult
            self.stacker_distance_le.setText("%0.6f" % pm_distance)
            self.stacker_number_le.setText("%u" % pm_steps)

        """
        self.stack_cb.addItem("A: None")
        self.stack_cb.addItem("B: Manual")
        self.stack_cb.addItem("C: normal")
        self.stack_cb.addItem("D: double distance")
        self.stack_cb.addItem("E: double steps")
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
            setup_step(1, 1)
        # Double distance
        elif mode == 3:
            # Keep step size constant => add more steps
            setup_step(2, 2)
        # Double step
        elif mode == 4:
            setup_step(1, 2)
        else:
            assert 0, "unknown mode"


class StitchingTab(ArgusTab):
    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)
        self.stitcher_thread = None
        self.last_cs_upload = None

    def initUI(self):
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

            layout.addWidget(QLabel("Manual stitch directory"), row, 0)
            self.manual_stitch_dir = QLineEdit("")
            layout.addWidget(self.manual_stitch_dir, row, 1)
            row += 1

            self.cs_pb = QPushButton("Manual CloudStitch")
            self.cs_pb.clicked.connect(self.stitch_begin_manual_cs)
            layout.addWidget(self.cs_pb, row, 1)
            row += 1

            gb = QGroupBox("Cloud Stitching")
            gb.setLayout(layout)
            return gb

        layout.addWidget(stitch_gb(), row, 0)
        row += 1

        self.setLayout(layout)

    def post_ui_init(self):
        self.stitcher_thread = StitcherThread(parent=self)
        self.stitcher_thread.log_msg.connect(self.ac.log)
        self.stitcher_thread.start()

    def shutdown(self):
        if self.stitcher_thread:
            self.stitcher_thread.shutdown()
            self.stitcher_thread = None

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

        if self.ac.mainTab.scan_widget.stitch_cb.isChecked():
            # CLI box is special => take priority
            # CLI may launch CloudStitch under the hood
            self.stitch_add(scan_config["out_dir"])

        # Enable joystick control if appropriate
        self.ac.joystick_enable(asneeded=True)

    def stitch_add(self, directory):
        self.ac.log(f"CloudStitch: requested {directory}")
        if not os.path.exists(directory):
            self.ac.log(
                f"Aborting stitch: directory does not exist: {directory}")
            return
        # Offload uploads etc to thread since they might take a while
        self.stitcher_thread.imagep_add(
            directory=directory,
            cs_info=self.get_cs_info(),
        )

    def get_cs_info(self):
        return CSInfo(access_key=str(self.stitch_accesskey.text()),
                      secret_key=str(self.stitch_secretkey.text()),
                      id_key=str(self.stitch_idkey.text()),
                      notification_email=str(self.stitch_email.text()))


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


"""
TODO:
-XY w/ third point to correct for angle skew
-XYZ w/ third point to correct for height
    Maybe evolution / same as above
-Many point for distorted dies like packaged chip
"""


def out_dir_config_to_dir(j, parent):
    """
    {
        "dt_prefix": True,
        "user_basename": str(self.le.text()),
    }
    """
    prefix = ''
    # if self.prefix_date_cb.isChecked():
    if j.get("dt_prefix"):
        # 2020-08-12_06-46-21
        prefix = datetime.datetime.utcnow().isoformat().replace(
            'T', '_').replace(':', '-').split('.')[0] + "_"

    mod = None
    while True:
        mod_str = ''
        if mod:
            mod_str = '_%u' % mod
        fn_full = os.path.join(
            parent,
            prefix + j["user_basename"] + mod_str + j.get("extension", ""))
        if os.path.exists(fn_full):
            if mod is None:
                mod = 1
            else:
                mod += 1
            continue
        return fn_full


# def scan_dir_fn(user, parent):
#    return snapshot_fn(user=user, extension="", parent=parent)


class JoystickListener(QPushButton):
    """
    Widget that maintains state of joystick enabled/disabled.
    """
    def __init__(self, label, parent=None):
        super().__init__(label, parent=parent)
        self.parent = parent
        self.setCheckable(True)
        self.setEnabled(False)
        self.setIcon(QIcon(config.GUI.icon_files["gamepad"]))
        self.joystick_executing = False
        self.enable()
        self.activate()

    """
    def hitButton(self, pos):
        self.toggle()
        return True
    """

    def enable(self):
        # This enables activation of joystick
        # actions by the user.
        # self.setEnabled(True)
        self.setEnabled(True)
        self.joystick_executing = self.isEnabled() and self.isChecked()

    def disable(self):
        # This deactivates and disables joystick
        # actions, and user cannot re-enable.
        # self.setEnabled(False)
        self.setDisabled(True)
        self.joystick_executing = self.isEnabled() and self.isChecked()

    def activate(self):
        # This activates joystick actions, and
        # user can deactivate.
        if not self.isChecked():
            # self.toggle()
            self.setChecked(True)
        self.joystick_executing = self.isEnabled() and self.isChecked()

    def deactivate(self):
        # This deactivates joystick actions but
        # user can re-activate.
        if self.isChecked():
            # self.toggle()
            self.setChecked(False)
        self.joystick_executing = self.isEnabled() and self.isChecked()


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
    def __init__(self, usc, parent=None):
        super().__init__(parent=parent)

        self.usc = usc

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
            layout = QHBoxLayout()
            layout.addWidget(QLabel("0.1"))
            layout.addWidget(QLabel("1"))
            layout.addWidget(QLabel("10"))
            layout.addWidget(QLabel("100"))
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
        slider_val = min(
            self.slider_max,
            float(self.slider.value()) +
            self.slider_max * self.slider_adjust_factor)
        self.slider.setValue(slider_val)

    def decrease_key(self):
        slider_val = max(
            self.slider_min,
            float(self.slider.value()) -
            self.slider_max * self.slider_adjust_factor)
        self.slider.setValue(slider_val)

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


class MotionWidget(AWidget):
    def __init__(self, ac, motion_thread, usc, log, parent=None):
        super().__init__(ac=ac, parent=parent)

        self.usc = usc
        self.log = log
        self.motion_thread = motion_thread
        self.fine_move = False

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

        self.last_send = time.time()
        # Can be used to invert keyboard, joystick XY inputs
        self.kj_xy_scalar = 1.0
        # self.max_velocities = None

    # Used to invert XY for user preference
    def set_kj_xy_scalar(self, val):
        self.kj_xy_scalar = val

    def initUI(self):
        # ?
        self.setWindowTitle("Demo")

        layout = QVBoxLayout()
        self.joystick_listener = None
        if self.ac.joystick_thread:
            self.joystick_listener = JoystickListener("  Joystick Control",
                                                      self)
        self.listener = JogListener("XXX", self)
        self.update_jog_text()
        layout.addWidget(self.listener)
        if self.joystick_listener:
            layout.addWidget(self.joystick_listener)
        self.slider = JogSlider(usc=self.usc)
        layout.addWidget(self.slider)

        def move_abs():
            layout = QHBoxLayout()

            layout.addWidget(QLabel("Absolute move"))
            self.move_abs_le = QLineEdit()
            self.move_abs_le.returnPressed.connect(self.move_abs_le_process)
            layout.addWidget(self.move_abs_le)

            layout.addWidget(QLabel("Relative move"))
            self.move_rel_le = QLineEdit()
            self.move_rel_le.returnPressed.connect(self.move_rel_le_process)
            layout.addWidget(self.move_rel_le)

            layout.addWidget(QLabel("Backlash compensate?"))
            self.move_abs_backlash_cb = QCheckBox()
            self.move_abs_backlash_cb.setChecked(True)
            # FIXME: always enabled right now
            self.move_abs_backlash_cb.setEnabled(False)
            layout.addWidget(self.move_abs_backlash_cb)

            self.autofocus_pb = QPushButton("AF")
            self.autofocus_pb.clicked.connect(self.autofocus_pushed)
            layout.addWidget(self.autofocus_pb)

            return layout

        def measure():
            layout = QHBoxLayout()

            self.set_difference_pb = QPushButton("Set reference")
            self.set_difference_pb.clicked.connect(
                self.set_difference_pb_pushed)
            layout.addWidget(self.set_difference_pb)

            layout.addWidget(QLabel("Reference"))
            self.reference_le = QLineEdit()
            layout.addWidget(self.reference_le)

            self.reference_moveto_pb = QPushButton("MoveTo")
            self.reference_moveto_pb.clicked.connect(
                self.reference_moveto_pb_pushed)
            layout.addWidget(self.reference_moveto_pb)

            layout.addWidget(QLabel("Difference"))
            self.difference_le = QLineEdit()
            layout.addWidget(self.difference_le)

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

        layout.addLayout(measure())

        self.setLayout(layout)

    def post_ui_init(self):
        # self.max_velocities = self.ac.motion_thread.motion.get_max_velocities()
        self.jog_controller = self.motion_thread.get_jog_controller(0.2)
        self.keys_up = {}

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
        self.log("Move absolute to %s" %
                 self.ac.usc.motion.format_positions(pos))
        self.log("  From %s" % self.ac.usc.motion.format_positions(
            self.ac.motion_thread.pos_cache))
        self.motion_thread.move_absolute(pos)

    def move_rel_le_process(self):
        s = str(self.move_rel_le.text())
        try:
            pos = motion_util.parse_move(s)
        except ValueError:
            self.ac.log("Failed to parse move. Need like: X1.0 Y2.4")
            return
        self.log("Move relative %s" % self.ac.usc.motion.format_positions(pos))
        self.log("  From %s" % self.ac.usc.motion.format_positions(
            self.ac.motion_thread.pos_cache))
        self.motion_thread.move_relative(pos)

    def mdi_le_process(self):
        if self.mdi_le:
            s = str(self.mdi_le.text())
            self.ac.log("Sending MDI: %s" % s)
            self.motion_thread.mdi(s)

    def set_difference_pb_pushed(self):
        pos = self.ac.motion_thread.pos_cache
        self.reference_le.setText(self.ac.usc.motion.format_positions(pos))

    def reference_moveto_pb_pushed(self):
        try:
            reference = motion_util.parse_move(str(self.reference_le.text()))
        except ValueError:
            self.log("Invalid reference")
            return
        self.motion_thread.move_absolute(reference)

    def update_reference(self):
        def get_str():
            pos = self.ac.motion_thread.pos_cache
            if pos is None:
                return "Invalid"

            try:
                reference = motion_util.parse_move(
                    str(self.reference_le.text()))
            except ValueError:
                return "Invalid"

            diff = {}
            for k in reference:
                diff[k] = pos.get(k, 0.0) - reference.get(k, 0.0)

            return self.ac.usc.motion.format_positions(diff)

        self.difference_le.setText(get_str())

    def update_jog_text(self):
        if self.fine_move:
            label = "Jog (fine)"
        else:
            label = "Jog"
        self.listener.setText(label)

    def keyPressEventCaptured(self, event):
        k = event.key()
        # Ignore duplicates, want only real presses
        # if 0 and event.isAutoRepeat():
        #     return

        self.keys_up[k] = True

        if k == Qt.Key_F:
            self.fine_move = not self.fine_move
            self.update_jog_text()
            return
        elif k == Qt.Key_Z:
            self.slider.decrease_key()
        elif k == Qt.Key_C:
            self.slider.increase_key()
        else:
            pass
            # print("unknown key %s" % (k, ))

    def keyReleaseEventCaptured(self, event):
        # Don't move around with moving around text boxes, etc
        # if not self.video_container.hasFocus():
        #    return
        k = event.key()
        self.keys_up[k] = False

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

            if not self.keys_up.get(k, False):
                continue

            fine_scalar = 1.0
            # FIXME: now that using real machine units need to revisit this
            if self.fine_move:
                if axis == "z":
                    fine_scalar = 0.01
                else:
                    fine_scalar = 0.01
            jog_val = keyboard_sign * self.kj_xy_scalar * fine_scalar * self.slider.get_jog_fraction(
            )
            jogs[axis] = jog_val

        self.jog_controller.update(jogs)

    def poll_misc(self):
        self.update_reference()
        self.update_jogging()

    def autofocus_pushed(self):
        self.ac.image_processing_thread.auto_focus()

    def cache_save(self, cachej):
        j = {}
        j["reference"] = str(self.reference_le.text())
        cachej["motion"] = j

    def cache_load(self, cachej):
        j = cachej.get("motion", {})
        self.reference_le.setText(j.get("reference", ""))


class SimpleScanNameWidget(AWidget):
    """
    Job name is whatever the user wants
    """
    def __init__(self, ac, parent=None):
        super().__init__(ac, parent=parent)

        layout = QHBoxLayout()

        layout.addWidget(QLabel("Job name"))
        self.le = QLineEdit("unknown")
        layout.addWidget(self.le)

        self.setLayout(layout)

    def getNameJ(self):
        # return scan_dir_fn(user=str(self.le.text()), parent=parent)
        return {
            "dt_prefix": True,
            "user_basename": str(self.le.text()),
        }

    def cache_save(self, cachej):
        cachej["scan_name"] = {
            "file_name": str(self.le.text()),
        }

    def cache_load(self, cachej):
        j = cachej.get("scan_name", {})
        self.le.setText(j.get("file_name", "unknown"))


class SiPr0nScanNameWidget(AWidget):
    """
    Force a name compatible with siliconpr0n.org naming convention
    """
    def __init__(self, ac, parent=None):
        super().__init__(ac, parent=parent)

        layout = QHBoxLayout()

        # old: freeform
        # layout.addWidget(QLabel("Job name'), 0, 0, 1, 2)
        # self.job_name_le = QLineEdit('default')
        # layout.addWidget(self.job_name_le)

        # Will add _ between elements to make final name

        layout.addWidget(QLabel("Vendor"))
        self.vendor_name_le = QLineEdit('unknown')
        layout.addWidget(self.vendor_name_le)

        layout.addWidget(QLabel("Product"))
        self.product_name_le = QLineEdit('unknown')
        layout.addWidget(self.product_name_le)

        layout.addWidget(QLabel("Layer"))
        self.layer_name_le = QLineEdit('mz')
        layout.addWidget(self.layer_name_le)

        layout.addWidget(QLabel("Ojbective"))
        self.objective_name_le = QLineEdit('unkx')
        layout.addWidget(self.objective_name_le)

        self.setLayout(layout)

    def getNameJ(self):
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
        # return os.path.join(parent, ret)
        return {
            "user_basename": ret,
        }


"""
class JoystickTab(ArgusTab):
    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)

        layout = QGridLayout()
        row = 0

        for axis in self.ac.motion_thread.motion.axes():
            layout = QGridLayout()
            row = 0
            axis_up = axis.upper()

            widgets = {
                "sensitivity": HLinearSlider100(50),
                "deadzone": HLinearSlider100(10),
                "invert": QCheckBox()
            }

            layout.addWidget(QLabel(f"{axis_up}"), row, 0)
            layout.addWidget((axis), row, 1)
            row += 1

            gb = QGroupBox("Dead zone")
            gb.setLayout(layout)
            return gb

        def deadzone_gb():
            layout = QGridLayout()
            row = 0

            sliders = {
                "x": HLinearSlider100(10),
                "y": HLinearSlider100(10),
                "z": HLinearSlider100(10),
            }

            layout.addWidget(QLabel("X"), row, 0)
            layout.addWidget(sliders["x"], row, 1)
            row += 1

            layout.addWidget(QLabel("Y"), row, 0)
            layout.addWidget(sliders["y"], row, 1)
            row += 1

            layout.addWidget(QLabel("Z"), row, 0)
            layout.addWidget(sliders["z"], row, 1)
            row += 1

            gb = QGroupBox("Dead zone")
            gb.setLayout(layout)
            return gb

        layout.addWidget(deadzone_gb(), row, 0)
        row += 1

        self.setLayout(layout)

    def post_ui_init(self):
        pass

    def cache_save(self, cachej):
        j = {}
        cachej["joystick"] = j

    def cache_load(self, cachej):
        j = cachej.get("joystick", {})

"""
