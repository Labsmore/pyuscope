from uscope.planner.planner_util import microscope_to_planner_config
from uscope import config
from collections import OrderedDict
from uscope.gui.widgets import AWidget, ArgusTab, StackingWidget, StabilizationWidget
from uscope.gui.video_record import VideoRecordWidget
from uscope.motion import motion_util
from uscope.scan_runner import ScanRunnerListener
from uscope.microscope import StopEvent, MicroscopeStop
from uscope import version as uscope_version
from uscope import app_log
from uscope.scan_util import out_dir_config_to_dir
from uscope import fiji_stitch

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import os.path
from uscope.util import utcnow
from uscope.imager.imager_util import hdr_exposure_sequence, format_exposure_csv
import threading


def snapshot_fn(user, extension, parent):
    prefix = ''
    # if self.prefix_date_cb.isChecked():
    if 1:
        # 2020-08-12_06-46-21
        prefix = utcnow().isoformat().replace(
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


# def scan_dir_fn(user, parent):
#    return snapshot_fn(user=user, extension="", parent=parent)


# Not to be confused with the smaller TopMotionWidget
class MotionWidget(AWidget):
    def __init__(self, ac, log, aname=None, parent=None):
        super().__init__(ac=ac, aname=aname, parent=parent)

        self.usc = self.ac.usc
        self.log = log
        self.motion_thread = self.ac.motion_thread

    def _initUI(self):
        layout = QVBoxLayout()

        self.advanced_movement_widgets = []

        def advanced_movement_widget(widget):
            self.advanced_movement_widgets.append(widget)
            return widget

        def move_abs():
            layout = QHBoxLayout()

            layout.addWidget(advanced_movement_widget(QLabel("Absolute move")))
            self.move_abs_le = advanced_movement_widget(QLineEdit())
            self.move_abs_le.returnPressed.connect(self.move_abs_le_process)
            layout.addWidget(self.move_abs_le)

            layout.addWidget(advanced_movement_widget(QLabel("Relative move")))
            self.move_rel_le = advanced_movement_widget(QLineEdit())
            self.move_rel_le.returnPressed.connect(self.move_rel_le_process)
            layout.addWidget(self.move_rel_le)
            """
            layout.addWidget(advanced_movement_widget(QLabel("Backlash compensate?")))
            self.move_abs_backlash_cb = advanced_movement_widget(QCheckBox())
            self.move_abs_backlash_cb.setChecked(True)
            # FIXME: always enabled right now
            self.move_abs_backlash_cb.setEnabled(False)
            layout.addWidget(self.move_abs_backlash_cb)
            """

            return layout

        def measure():
            layout = QHBoxLayout()

            self.set_difference_pb = advanced_movement_widget(
                QPushButton("Set reference"))

            self.set_difference_pb.clicked.connect(
                self.set_difference_pb_pushed)
            layout.addWidget(self.set_difference_pb)

            layout.addWidget(advanced_movement_widget(QLabel("Reference")))
            self.reference_le = advanced_movement_widget(QLineEdit())
            layout.addWidget(self.reference_le)

            self.reference_moveto_pb = advanced_movement_widget(
                QPushButton("MoveTo"))
            self.reference_moveto_pb.clicked.connect(
                self.reference_moveto_pb_pushed)
            layout.addWidget(self.reference_moveto_pb)

            layout.addWidget(advanced_movement_widget(QLabel("Difference")))
            self.difference_le = advanced_movement_widget(QLineEdit())
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

        self.show_advanced_movement(config.bc.dev_mode())

        self.setLayout(layout)

    def show_advanced_movement(self, visible):
        for widget in self.advanced_movement_widgets:
            widget.setVisible(visible)

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
            self.ac.motion_thread.get_pos_cache()))
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
            self.ac.motion_thread.get_pos_cache()))
        self.motion_thread.move_relative(pos)

    def mdi_le_process(self):
        if self.mdi_le:
            s = str(self.mdi_le.text())
            self.ac.log("Sending MDI: %s" % s)
            self.motion_thread.mdi(s)

    def set_difference_pb_pushed(self):
        pos = self.ac.motion_thread.get_pos_cache()
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
            pos = self.ac.motion_thread.get_pos_cache()
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

    def _poll_misc(self):
        self.update_reference()

    def _cache_save(self, cachej):
        j = {}
        j["reference"] = str(self.reference_le.text())
        cachej["motion"] = j

    def _cache_load(self, cachej):
        j = cachej.get("motion", {})
        self.reference_le.setText(j.get("reference", ""))


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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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

    def _cache_save(self, cachej):
        j = {}
        cachej["joystick"] = j

    def _cache_load(self, cachej):
        j = cachej.get("joystick", {})

"""


class ObjectiveWidget(AWidget):
    """
    View over the core objective state (microscope.objective_state)
    """

    # Objective config dict. Lets the state notify from any thread
    stateChanged = pyqtSignal(dict)

    def __init__(self, ac, aname=None, parent=None):
        super().__init__(ac=ac, aname=aname, parent=parent)
        self.state = self.ac.microscope.objective_state
        # Ignore combo box changes we make ourselves
        self.updating_objectives = False
        self.stateChanged.connect(self.state_changed)
        self.state.add_listener(self.stateChanged.emit)

    @property
    def obj_config(self):
        """Active objective config (dict)"""
        return self.state.config()

    def _initUI(self):
        self.advanced_widgets = []

        def advanced_widget(widget):
            self.advanced_widgets.append(widget)
            return widget

        layout = QGridLayout()

        row = 0
        l = QLabel("Objective")
        layout.addWidget(l, row, 0)

        self.obj_cb = QComboBox()
        layout.addWidget(self.obj_cb, row, 1)
        self.obj_view = advanced_widget(QLabel(""))
        layout.addWidget(self.obj_view, row, 2)

        self.setLayout(layout)

    def show_advanced(self, visible):
        for widget in self.advanced_widgets:
            widget.setVisible(visible)

    def _post_ui_init(self):
        self.state.reload()

    def state_changed(self, config):
        """Make the widgets reflect the core state"""
        self.updating_objectives = True
        try:
            names = self.state.names()
            if [self.obj_cb.itemText(i)
                    for i in range(self.obj_cb.count())] != names:
                self.obj_cb.clear()
                for name in names:
                    self.obj_cb.addItem(name)
            self.obj_cb.setCurrentText(config["name"])
            im_w_um, im_h_um = self.state.view_um()
            self.obj_view.setText('View : %0.3fx %0.3fy' % (im_w_um, im_h_um))
        finally:
            self.updating_objectives = False
        self.ac.objectiveChanged.emit(config)

    def obj_cb_changed(self):
        if self.updating_objectives:
            return
        name = str(self.obj_cb.currentText())
        if name:
            self.state.select(name)

    def go_parfocal_z(self, name=None):
        """Move Z by the stored offset for name versus the current objective."""
        self.state.go_parfocal_z(name)

    def get_objective_meta_cache(self):
        return self.state.config()

    def _cache_sn_save(self, cachej):
        self.state.cache_sn_save(cachej)

    def _cache_sn_load(self, cachej):
        self.state.cache_sn_load(cachej)
        self.obj_cb.currentIndexChanged.connect(self.obj_cb_changed)


class CameraResolutionWidget(AWidget):
    """
    Camera resolution, applied immediately (see SessionGeometry)
    Shows the resulting image size after crop / scalar
    """
    def __init__(self, ac, aname=None, parent=None):
        super().__init__(ac=ac, aname=aname, parent=parent)
        # Reasons the selection is temporarily disabled (ex: "scan")
        self.busy = set()
        self.selectable = False

    def _initUI(self):
        layout = QGridLayout()
        layout.addWidget(QLabel("Camera resolution"), 0, 0)
        self.resolution_cb = QComboBox()
        layout.addWidget(self.resolution_cb, 0, 1)
        self.size_l = QLabel("")
        layout.addWidget(self.size_l, 0, 2)
        self.setLayout(layout)

    def _post_ui_init(self):
        self.resolution_cb.activated.connect(self.resolution_activated)
        self.ac.imageGeometryChanged.connect(self.refresh)
        self.refresh()

    def refresh(self):
        selector = self.ac.resolution_selector()
        usc_imager = self.ac.usc.imager
        self.resolution_cb.blockSignals(True)
        try:
            self.resolution_cb.clear()
            if selector is not None and selector.can_select():
                self.selectable = True
                active = selector.selected_wh()
                for r in selector.resolutions:
                    wh = (r["width"], r["height"])
                    self.resolution_cb.addItem(selector.label(r), wh)
                    if wh == tuple(active):
                        self.resolution_cb.setCurrentIndex(
                            self.resolution_cb.count() - 1)
                self.resolution_cb.setToolTip(
                    "Applied immediately. Saved per microscope and camera")
            else:
                self.selectable = False
                self.resolution_cb.addItem("%u x %u" % usc_imager.raw_wh())
                if selector is None:
                    why = "Camera source %s can't select resolution" % (
                        usc_imager.source(), )
                else:
                    why = selector.unavailable_reason()
                self.resolution_cb.setToolTip(why)
        finally:
            self.resolution_cb.blockSignals(False)
        self.update_enabled()

        crop_w, crop_h = usc_imager.cropped_wh()
        final_w, final_h = usc_imager.final_wh()
        if (crop_w, crop_h) == (final_w, final_h):
            self.size_l.setText("Image %u x %u" % (final_w, final_h))
        else:
            self.size_l.setText("Cropped %u x %u, final %u x %u" %
                                (crop_w, crop_h, final_w, final_h))
        self.size_l.setToolTip("After crop (%s) and image scalar (%g)" %
                               (usc_imager.crop_tblr() or "none",
                                usc_imager.scalar()))

    def update_enabled(self):
        self.resolution_cb.setEnabled(self.selectable and not self.busy)

    def set_busy(self, reason, busy):
        if busy:
            self.busy.add(reason)
        else:
            self.busy.discard(reason)
        self.update_enabled()

    def resolution_activated(self, index):
        wh = self.resolution_cb.itemData(index)
        if wh is None:
            return
        try:
            self.ac.set_camera_resolution(tuple(wh))
        except Exception as e:
            self.ac.log(f"Failed to set camera resolution: {e}")
        # Also snaps back on failure
        self.refresh()


"""
Provides camera overview and ROI side by side
"""


class PlannerWidget(AWidget):
    click_corner = pyqtSignal(tuple)
    go_corner = pyqtSignal(tuple)

    def __init__(self,
                 ac,
                 scan_widget,
                 objective_widget,
                 aname=None,
                 parent=None):
        super().__init__(ac=ac, aname=aname, parent=parent)
        self.imaging_widget = scan_widget
        self.objective_widget = objective_widget
        self.click_corner.connect(self.click_corner_slot)
        self.go_corner.connect(self.go_corner_slot)
        self.corner_widgets = OrderedDict()

    # FIXME: abstract these better

    def get_out_dir_j(self):
        j = self.imaging_widget.getNameJ()
        out_dir = out_dir_config_to_dir(j, self.ac.usc.app("argus").scan_dir())
        if os.path.exists(out_dir):
            self.ac.log("Refusing to create config: dir already exists: %s" %
                        out_dir)
            return None
        return j

    def get_objective_meta_cache(self):
        return self.objective_widget.get_objective_meta_cache()

    def show_minmax(self, visible):
        self.showing_minmax = visible
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
        self.show_minmax(self.ac.bc.dev_mode())

        return row

    def _post_ui_init(self):
        self.fill_minmax()

    # Thread safety to bring back to GUI thread for GUI operations
    def emit_click_corner(self, corner_name, done=None):
        self.click_corner.emit((corner_name, done))

    def click_corner_slot(self, args):
        corner_name, done = args
        self.corner_clicked(corner_name)
        if done:
            done.set()

    def emit_go_corner(self, corner_name, done=None):
        self.go_corner.emit((corner_name, done))

    def go_corner_slot(self, args):
        corner_name, done = args
        pos = self.get_corner_move_pos(corner_name)
        if pos is None:
            raise Exception("Failed to get corner")
        self.ac.motion_thread.move_absolute(pos, done=done)


"""
Integrates both 2D planner controls and current display
2.5D: XY planner controls + XYZ display
"""


class XYPlanner2PWidget(PlannerWidget):
    def __init__(self,
                 ac,
                 scan_widget,
                 objective_widget,
                 aname=None,
                 parent=None):
        super().__init__(ac=ac,
                         scan_widget=scan_widget,
                         objective_widget=objective_widget,
                         aname=aname,
                         parent=parent)

    def _initUI(self):
        gl = QGridLayout()
        row = 0

        row = self.add_axis_rows(gl, row)

        # TODO 2023-10-15: all modern systems are ll
        # we should consider removing non-ll origin support entirely
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
        self.corner_widgets["ll"] = {
            "x_le": self.plan_x0_le,
            "y_le": self.plan_y0_le,
            "pb": QPushButton("MoveTo"),
        }
        gl.addWidget(self.corner_widgets["ll"]["pb"], row, 3)
        row += 1

        self.plan_end_pb = QPushButton(end_label)
        self.plan_end_pb.clicked.connect(self.set_end_pos)
        self.plan_end_pb.setIcon(QIcon(end_icon))
        gl.addWidget(self.plan_end_pb, row, 0)
        self.plan_x1_le = QLineEdit("")
        gl.addWidget(self.plan_x1_le, row, 1)
        self.plan_y1_le = QLineEdit("")
        gl.addWidget(self.plan_y1_le, row, 2)
        self.corner_widgets["ur"] = {
            "x_le": self.plan_x1_le,
            "y_le": self.plan_y1_le,
            "pb": QPushButton("MoveTo"),
        }
        gl.addWidget(self.corner_widgets["ur"]["pb"], row, 3)
        row += 1

        for corner_name in ("ll", "ur"):

            def connect_corner_widget(corner_name, ):
                def go_clicked():
                    pos = self.get_corner_move_pos(corner_name)
                    if pos is not None:
                        self.ac.motion_thread.move_absolute(pos)

                self.corner_widgets[corner_name]["pb"].clicked.connect(
                    go_clicked)

            connect_corner_widget(corner_name)

        self.setLayout(gl)

    def af_corners(self):
        # return ("ll", "ur")
        # Only makes sense to focus one corner since we don't track z
        # TODO: do center instead?
        return ("ll", )

    def _cache_save(self, cachej):
        cachej["XY2P"] = {
            "x0": str(self.plan_x0_le.text()),
            "y0": str(self.plan_y0_le.text()),
            "x1": str(self.plan_x1_le.text()),
            "y1": str(self.plan_y1_le.text()),
        }

    def _cache_load(self, cachej):
        j = cachej.get("XY2P", {})
        self.plan_x0_le.setText(j.get("x0", ""))
        self.plan_y0_le.setText(j.get("y0", ""))
        self.plan_x1_le.setText(j.get("x1", ""))
        self.plan_y1_le.setText(j.get("y1", ""))

    def _poll_misc(self):
        last_pos = self.ac.motion_thread.get_pos_cache()
        if last_pos:
            self.update_pos(last_pos)

    def update_pos(self, pos):
        for axis, axis_pos in pos.items():
            # hack...not all systems use z but is included by default
            if axis == 'z' and axis not in self.axis_pos_label:
                continue
            self.axis_pos_label[axis].setText(
                self.ac.usc.motion.format_position(axis, axis_pos))

    def mk_contour_json(self):
        pos0 = self.get_corner_planner_pos("ll")
        if pos0 is None:
            return
        pos1 = self.get_corner_planner_pos("ur")
        if pos1 is None:
            return

        # Planner will sort order as needed
        ret = {"start": pos0, "end": pos1}

        return ret

    def get_current_planner_hconfig(self):
        contour_json = self.mk_contour_json()
        if not contour_json:
            return

        objective = self.get_objective_meta_cache()
        pconfig = microscope_to_planner_config(microscope=self.ac.microscope,
                                               objective=objective,
                                               contour=contour_json)

        try:
            self.ac.mw.update_pconfig(pconfig)
        # especially ValueError from bad GUI items
        except Exception as e:
            self.log(f"Scan config aborted: {e}")
            return

        # Ignored app specific metadata
        pconfig["app"] = {
            "app": "argus",
            "objective": objective,
            "microscope": self.ac.microscope.name,
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
        pos = self.ac.motion_thread.get_pos_cache()
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
        pos = self.ac.motion_thread.get_pos_cache()
        #self.ac.log("Updating end pos from %s" % (str(pos)))
        self.plan_x1_le.setText(
            self.ac.usc.motion.format_position("x", pos["x"]))
        self.plan_y1_le.setText(
            self.ac.usc.motion.format_position("y", pos["y"]))

    def corner_clicked(self, corner_name):
        pos_cur = self.ac.motion_thread.get_pos_cache()
        widgets = self.corner_widgets[corner_name]

        widgets["x_le"].setText(
            self.ac.usc.motion.format_position("x", pos_cur["x"]))
        widgets["y_le"].setText(
            self.ac.usc.motion.format_position("y", pos_cur["y"]))

    def get_corner_widget_pos(self, corner_name):
        widgets = self.corner_widgets[corner_name]
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
        if corner_name == "ll":
            pos["x"] -= x_view / 2
            pos["y"] -= y_view / 2
        # ur
        elif corner_name == "ur":
            pos["x"] += x_view / 2
            pos["y"] += y_view / 2
        else:
            assert 0, corner_name
        return pos


class XYPlanner3PWidget(PlannerWidget):
    def __init__(self,
                 ac,
                 scan_widget,
                 objective_widget,
                 aname=None,
                 parent=None):
        super().__init__(ac=ac,
                         scan_widget=scan_widget,
                         objective_widget=objective_widget,
                         aname=aname,
                         parent=parent)

    def _initUI(self):
        gl = QGridLayout()
        row = 0

        row = self.add_axis_rows(gl, row)

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

        # FIXME: consider removing entirely
        # this is an advanced feature not needed in most use cases
        show_track_z = False
        self.track_z_label = QLabel("Track Z?")
        gl.addWidget(self.track_z_label, row, 0)
        self.track_z_cb = QCheckBox()
        self.track_z_cb.stateChanged.connect(self.track_z_cb_changed)
        self.track_z_cb_changed(None)
        gl.addWidget(self.track_z_cb, row, 1)
        self.track_z_cb.setEnabled(self.ac.microscope.has_z())
        self.track_z_label.setVisible(show_track_z)
        self.track_z_cb.setVisible(show_track_z)

        row += 1

        self.setLayout(gl)

    def _cache_save(self, cachej):
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

    def _cache_load(self, cachej):
        j1 = cachej.get("XY3P", {})

        # This needs to be tracked per s/n or it can become "contaminated"
        # just force for now since no-track-z is basically deprecated anyway
        self.track_z_cb.setChecked(self.ac.microscope.has_z())
        #if self.ac.microscope.has_z():
        #    self.track_z_cb.setChecked(j1.get("track_z", True))

        for group in ("ll", "ul", "lr"):
            widgets = self.corner_widgets[group]
            j2 = j1.get(group, {})
            widgets["x_le"].setText(j2.get("x_le", ""))
            widgets["y_le"].setText(j2.get("y_le", ""))
            widgets["z_le"].setText(j2.get("z_le", ""))

    def af_corners(self):
        return ("ul", "ll", "lr")

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

    def _poll_misc(self):
        last_pos = self.ac.motion_thread.get_pos_cache()
        if last_pos:
            self.update_pos(last_pos)

    def update_pos(self, pos):
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

    def get_current_planner_hconfig(self):
        corner_json = self.mk_corner_json()
        if not corner_json:
            return

        objective = self.get_objective_meta_cache()
        pconfig = microscope_to_planner_config(microscope=self.ac.microscope,
                                               objective=objective,
                                               corners=corner_json)

        self.ac.mw.update_pconfig(pconfig)

        # Ignored app specific metadata
        pconfig["app"] = {
            "app": "argus",
            "objective": objective,
            "microscope": self.ac.microscope.name,
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
        pos_cur = self.ac.motion_thread.get_pos_cache()
        widgets = self.corner_widgets[corner_name]

        widgets["x_le"].setText(
            self.ac.usc.motion.format_position("x", pos_cur["x"]))
        widgets["y_le"].setText(
            self.ac.usc.motion.format_position("y", pos_cur["y"]))
        if "z" in pos_cur:
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

    def set_corners(self, corners):
        """
        corners: corner name => {"x", "y"[, "z"]} as corner_clicked() records
        """
        for corner_name, pos in corners.items():
            widgets = self.corner_widgets[corner_name]
            for axis in "xyz":
                if axis in pos:
                    widgets[axis + "_le"].setText(
                        self.ac.usc.motion.format_position(axis, pos[axis]))

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


"""
Monitors the current scan
Set output job name
"""


class ImagingOptions:
    """
    Snapshot and panorama options
    Shown directly on the Main tab (snapshot) and Panorama tab (scan)
    """
    def __init__(self, itw):
        self.itw = itw
        self.ac = self.itw.ac
        self.snapshot_widget = self.snapshot_options()
        self.panorama_widget = self.panorama_options()

    def snapshot_options(self):
        self.add_scalebar_cb = QCheckBox("Add scalebar")
        self.add_scalebar_cb.stateChanged.connect(
            self.itw.update_imaging_config)
        return self.add_scalebar_cb

    def panorama_options(self):
        layout = QVBoxLayout()

        self.autofocus_cb = QCheckBox("Autofocus corners")
        self.autofocus_cb.setChecked(self.ac.microscope.has_z())
        self.autofocus_cb.stateChanged.connect(self.itw.update_imaging_config)
        layout.addWidget(self.autofocus_cb)

        def process_gb():
            layout = QVBoxLayout()

            self.cloudstitch_cb = QCheckBox("CloudStitch")
            if self.ac.stitchingTab.has_cs_info():
                self.ac.log("CloudStitch: available")
                self.cloudstitch_cb.setChecked(True)
            else:
                # Currently don't officially support changing at runtime
                self.cloudstitch_cb.setEnabled(False)
                self.ac.log(
                    "CloudStitch: not configured. Contact support@labsmore.com to enable"
                )
            self.cloudstitch_cb.stateChanged.connect(
                self.itw.update_imaging_config)
            layout.addWidget(self.cloudstitch_cb)

            self.fiji_stitch_cb = QCheckBox("Fiji stitch (local)")
            try:
                fiji = fiji_stitch.find_fiji()
            except ValueError as e:
                fiji = None
                self.ac.log(f"Fiji stitch: ERROR: {e}")
            if fiji:
                self.ac.log(f"Fiji stitch: available ({fiji})")
            else:
                self.fiji_stitch_cb.setEnabled(False)
                self.fiji_stitch_cb.setToolTip(
                    "Fiji not found. Install Fiji or set fiji_path")
            self.fiji_stitch_cb.stateChanged.connect(
                self.itw.update_imaging_config)
            layout.addWidget(self.fiji_stitch_cb)

            self.keep_intermediate_cb = QCheckBox(
                "Keep intermediate (unstitched) files")
            self.keep_intermediate_cb.setChecked(True)
            self.keep_intermediate_cb.stateChanged.connect(
                self.itw.update_imaging_config)
            layout.addWidget(self.keep_intermediate_cb)

            self.html_cb = QCheckBox("HTML viewer")
            self.html_cb.stateChanged.connect(self.itw.update_imaging_config)
            layout.addWidget(self.html_cb)

            self.quick_stitch_cb = QCheckBox("Quick stitch")
            self.quick_stitch_cb.stateChanged.connect(
                self.itw.update_imaging_config)
            layout.addWidget(self.quick_stitch_cb)

            self.snapshot_grid_cb = QCheckBox("Snapshot grid overview")
            self.snapshot_grid_cb.stateChanged.connect(
                self.itw.update_imaging_config)
            layout.addWidget(self.snapshot_grid_cb)

            self.stitch_gb = QGroupBox("Post-processing")
            self.stitch_gb.setCheckable(True)
            self.stitch_gb.setLayout(layout)
            self.stitch_gb.toggled.connect(self.itw.update_imaging_config)

            return self.stitch_gb

        layout.addWidget(process_gb())

        gb = QGroupBox("Scan options")
        gb.setLayout(layout)
        return gb


# 2023-11-15: combined ScanWidget + SnapshotWidget
class QScanRunnerListener(QObject, ScanRunnerListener):
    """
    Delivers scan runner events to the GUI thread
    """
    scanStarted = pyqtSignal(dict)
    scanProgress = pyqtSignal(dict)
    scanDone = pyqtSignal(dict, dict)
    queueDone = pyqtSignal(list)

    def scan_started(self, hconfig):
        self.scanStarted.emit(hconfig)

    def scan_progress(self, state):
        self.scanProgress.emit(state)

    def scan_done(self, hconfig, result):
        self.scanDone.emit(hconfig, result)

    def queue_done(self, results):
        self.queueDone.emit(results)


class ImagingTaskWidget(AWidget):
    snapshotDone = pyqtSignal()

    def __init__(self,
                 ac,
                 go_current_pconfig,
                 setControlsEnabled,
                 aname=None,
                 parent=None):
        super().__init__(ac=ac, aname=aname, parent=parent)
        # self.pos.connect(self.update_pos)
        self.imaging_config = None
        self.go_current_pconfig = go_current_pconfig
        self.setControlsEnabled = setControlsEnabled
        self._save_extension = None
        self.taking_snapshot = False
        self.snapshotDone.connect(self.snapshot_done)
        self.scan_listener = QScanRunnerListener()
        self.scan_listener.scanProgress.connect(self.scan_progress)
        self.scan_listener.scanDone.connect(self.scan_done)
        self.scan_listener.queueDone.connect(self.queue_done)
        self.ac.scan_runner.add_listener(self.scan_listener)
        self.capture_mode = "composite"
        self.video_record_widget = VideoRecordWidget(
            ac=ac,
            filename_base=self.video_fn,
            aname="video_record",
            parent=self)
        # Shown in PanoramaTab, but also drive batch runs
        self.go_pause_pb = QPushButton("Scan")
        self.go_pause_pb.clicked.connect(self.go_pause_clicked)
        self.go_pause_pb.setIcon(QIcon(config.GUI.icon_files['go']))
        self.progress_bar = QProgressBar()

    def _initUI(self):
        def getNameLayout():
            hl = QHBoxLayout()
            hl.addWidget(QLabel("File name"))

            self.job_name_le = QLineEdit("unknown")
            self.snapshot_suffix_cb = QComboBox()
            self.snapshot_suffix_cb_map = {
                0: ".jpg",
                1: ".tif",
            }
            self.snapshot_suffix_cb.addItem(".jpg")
            self.snapshot_suffix_cb.addItem(".tif")

            self.snapshot_suffix_cb.setSizePolicy(
                QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))

            hl.addWidget(self.job_name_le)
            hl.addWidget(self.snapshot_suffix_cb)
            self.snapshot_suffix_cb.currentIndexChanged.connect(
                self.update_save_extension)
            return hl

        # Panorama options are placed by PanoramaTab
        self.options = ImagingOptions(self)

        layout = QVBoxLayout()

        layout.addLayout(getNameLayout())
        layout.addWidget(self.options.snapshot_widget)

        self.snapshot_pb = QPushButton("Snapshot")
        self.snapshot_pb.setIcon(QIcon(config.GUI.icon_files["camera"]))
        self.snapshot_pb.clicked.connect(self.take_snapshot)
        layout.addWidget(self.snapshot_pb)
        layout.addWidget(self.video_record_widget)

        gb = QGroupBox("Imaging")
        gb.setLayout(layout)

        layoutW = QVBoxLayout()
        layoutW.addWidget(gb)
        self.setLayout(layoutW)

    def getNameJ(self):
        # return scan_dir_fn(user=str(self.le.text()), parent=parent)
        return {
            "dt_prefix": True,
            "user_basename": str(self.job_name_le.text()),
        }

    def dry(self):
        return False

    def scan_progress(self, state):
        if state["type"] == "begin":
            self.progress_bar.setMinimum(0)
            self.progress_bar.setMaximum(state["images_to_capture"])
            self.progress_bar.setValue(0)
        elif state["type"] == "image":
            self.ac.log('Captured: %s' % (state["image_filename_rel"], ))
            progress = self.ac.scan_runner.progress_ts()
            if progress:
                self.ac.log('%s' % (progress["eta_message"], ))
            self.progress_bar.setValue(state["images_captured"])

    def scan_done(self, hconfig, result):
        if result["result"] == "ok":
            self.ac.stitchingTab.scan_completed(hconfig, result)

    def queue_done(self, _results):
        self.go_pause_pb.setText("Scan")
        self.setControlsEnabled(True)
        self.ac.control_scroll.enable_user_controls(True)

    def go_planner_hconfigs(self, scan_hconfigs):
        """
        scan_config["pconfig"]: planner config
        scan_config["done_callback"]: callback
        scan_config["out_dir_config"] out_dir_config
        """
        if not scan_hconfigs:
            return
        if not self.ac.is_idle():
            return

        self.setControlsEnabled(False)
        self.go_pause_pb.setText("Pause")
        self.ac.control_scroll.enable_user_controls(False)
        try:
            self.ac.scan_runner.run(
                scan_hconfigs,
                dry=self.dry(),
                abort_on_failure=self.ac.batchTab.abort_on_failure())
        except:
            self.queue_done([])
            raise

    def afgo(self, planner_widget):
        def offload(ac):
            done = threading.Event()
            try:
                with StopEvent(self.ac.microscope) as se:
                    for corner in planner_widget.af_corners():
                        se.poll()
                        done.clear()
                        planner_widget.emit_go_corner(corner_name=corner,
                                                      done=done)
                        done.wait()

                        se.poll()
                        self.ac.image_processing_thread.auto_focus(
                            objective_config=self.ac.objective_config(),
                            block=True)

                        se.poll()
                        done.clear()
                        planner_widget.emit_click_corner(corner_name=corner,
                                                         done=done)
                        done.wait()

                    se.poll()
                    self.ac.panoramaTab.emit_go_current_pconfig()
            except MicroscopeStop:
                self.ac.log("Autofocus + Go cancelled")

        self.ac.task_thread.offload(offload)

    def go_pause_clicked(self):
        # CNC already running? pause/continue
        scan_runner = self.ac.scan_runner
        if scan_runner.is_running():
            # Pause
            if scan_runner.is_paused():
                self.go_pause_pb.setText("Pause")
                scan_runner.unpause()
            else:
                self.go_pause_pb.setText("Continue")
                scan_runner.pause()
        # Go go go!
        else:
            autofocus = self.options.autofocus_cb.isChecked()
            auto_exposure = self.ac.auto_exposure_enabled()
            auto_color = self.ac.auto_color_enabled()
            auto_color = False
            mb_type = QMessageBox.question

            warning = ""
            if auto_exposure or auto_color:
                warning = f"\n\nWARNING: you have automatic exposure ({auto_exposure}) and/or color correction ({auto_color}) enabled. This will lead to an inconsistent capture"
                mb_type = QMessageBox.warning

            scan_settings = "Scan settings:"
            scan_settings += "\nAutofocus corners: %s" % (autofocus, )
            if self.ac.imagerTab.image_stacking_enabled():
                pm_n = self.ac.imagerTab.image_stacking_pm_n()
                scan_settings += "\nFocus stacking enabled (+/- %u images => %u per stack)" % (
                    pm_n, pm_n * 2 + 1)
                if self.ac.imagerTab.stack_drift_correction_enabled():
                    scan_settings += "\nFocus stacking drift correction enabled"
            stabilization = self.ac.imagerTab.stabilization_widget
            if stabilization.image_stablization_enabled():
                scan_settings += "\nImage stabilization enabled (n=%u)" % (
                    stabilization.get_image_stablization(), )

            if self.options.stitch_gb.isChecked():
                post_settings = "Post processing (stitching) settings:"
                ippj = {}
                self.update_ippj(ippj)
                post_settings += "\nCloudStitch: %s" % (ippj["cloud_stitch"], )
                post_settings += "\nFiji stitch: %s" % (ippj["fiji_stitch"], )
                post_settings += "\nKeeping intermediate files: %s" % (
                    ippj["keep_intermediates"], )
                if ippj["write_html_viewer"]:
                    post_settings += "\nWriting overview as HTML index"
                if ippj["write_quick_pano"]:
                    post_settings += "\nWriting overview as quick image stitch"
                if ippj["write_snapshot_grid"]:
                    post_settings += "\nWriting overview as grid image"
            else:
                post_settings = "Post processing (stitching) disabled"

            ret = mb_type(
                self, "Start scan?", "Start scan?%s\n\n%s\n\n%s" %
                (warning, scan_settings, post_settings),
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if ret != QMessageBox.Yes:
                return

            if autofocus:
                self.afgo(self.ac.panoramaTab.active_planner_widget())
            else:
                self.go_current_pconfig()

    def take_snapshot(self):
        # joystick can stack up events
        if not self.snapshot_pb.isEnabled():
            self.ac.log(
                "Snapshot already requested. Please wait before requesting another"
            )
            return

        # self.ac.log('Requesting snapshot')
        # Disable until snapshot is completed
        self.snapshot_pb.setEnabled(False)
        self.taking_snapshot = True

        options = {}
        options["save_filename"] = self.snapshot_fn()
        extension = self.save_extension()
        if extension == ".jpg":
            options["save_quality"] = self.ac.usc.imager.save_quality()
        options["scale_factor"] = self.ac.usc.imager.scalar()

        imaging_config = self.ac.imaging_config()
        plugins = {}
        if imaging_config.get("add_scalebar", False):
            plugins["annotate-scalebar"] = {}
        options["plugins"] = plugins
        qr_regex = config.bc.qr_regex()
        if qr_regex:
            options["qr_regex"] = qr_regex

        def offload(ac):
            try:
                capim = self.ac.microscope.imager_ts().get_by_mode(
                    mode=self.capture_mode, processing_options=options)
                filename = capim.meta["save_filename"]
                self.ac.microscope.log(f"Snapshot: saved to {filename}")

                data = {
                    "image": capim.image,
                    "captured_image": capim,
                    "objective_config": capim.meta["objective_config"]
                }
                self.ac.snapshotCaptured.emit(data)
            finally:
                self.snapshotDone.emit()

        self.ac.task_thread.offload(offload)

    def snapshot_done(self):
        self.snapshot_pb.setEnabled(True)
        self.taking_snapshot = False

    def save_extension(self):
        # ex: .jpg, .tif
        return self._save_extension

    def update_save_extension(self):
        self._save_extension = self.snapshot_suffix_cb_map[
            self.snapshot_suffix_cb.currentIndex()]

    def snapshot_fn(self):
        return snapshot_fn(user=str(self.job_name_le.text()),
                           extension=self.save_extension(),
                           parent=self.ac.usc.app("argus").snapshot_dir())

    def video_fn(self, extension):
        return snapshot_fn(user=str(self.job_name_le.text()),
                           extension=extension,
                           parent=self.ac.usc.app("argus").snapshot_dir())

    def _post_ui_init(self):
        self.update_save_extension()
        snapshot_dir = self.ac.usc.app("argus").snapshot_dir()
        if not os.path.isdir(snapshot_dir):
            self.ac.log('Snapshot dir %s does not exist' % snapshot_dir)
            if os.path.exists(snapshot_dir):
                raise Exception("Snapshot directory is not accessible")
            os.mkdir(snapshot_dir)
            self.ac.log('Snapshot dir %s created' % snapshot_dir)

        self.update_imaging_config()

    def _update_pconfig(self, pconfig):
        imagerj = pconfig.setdefault("imager", {})
        imagerj["save_extension"] = self.save_extension()
        imagerj["save_quality"] = self.ac.usc.imager.save_quality()
        # FIXME: move scan autofocus from Argus to planner
        # imagerj["autofocus"] = self.options.autofocus_cb.isChecked()

        # Serialized into cs_auto.py CLI option
        ippj = pconfig.setdefault("ipp", {})
        self.update_ippj(ippj)

    def update_ippj(self, ippj):
        ippj["cloud_stitch"] = self.options.cloudstitch_cb.isChecked()
        ippj["fiji_stitch"] = self.options.fiji_stitch_cb.isChecked()
        ippj["write_html_viewer"] = self.options.html_cb.isChecked()
        ippj["write_quick_pano"] = self.options.quick_stitch_cb.isChecked()
        ippj["write_snapshot_grid"] = self.options.snapshot_grid_cb.isChecked()
        ippj["keep_intermediates"] = self.options.keep_intermediate_cb.isChecked()

    def _cache_save(self, cachej):
        cachej["imaging"] = {
            "file_name": str(self.job_name_le.text()),
            "extension": self.snapshot_suffix_cb.currentIndex(),
            "stitch": self.options.stitch_gb.isChecked(),
            "cloudstitch": self.options.cloudstitch_cb.isChecked(),
            "fiji_stitch": self.options.fiji_stitch_cb.isChecked(),
            "autofocus": self.options.autofocus_cb.isChecked(),
            "add_scalebar": self.options.add_scalebar_cb.isChecked(),
            "keep_intermediate": self.options.keep_intermediate_cb.isChecked(),
            "html": self.options.html_cb.isChecked(),
            "quick_stitch": self.options.quick_stitch_cb.isChecked(),
            "snapshot_grid": self.options.snapshot_grid_cb.isChecked(),
        }

    def _cache_load(self, cachej):
        j = cachej.get("imaging", {})
        self.job_name_le.setText(j.get("file_name", "unknown"))
        self.snapshot_suffix_cb.setCurrentIndex(j.get("extension", 0))
        self.options.stitch_gb.setChecked(j.get("stitch", True))
        # Without creds the checkbox is disabled and must stay unchecked
        if self.options.cloudstitch_cb.isEnabled():
            self.options.cloudstitch_cb.setChecked(j.get("cloudstitch", True))
        if self.options.fiji_stitch_cb.isEnabled():
            self.options.fiji_stitch_cb.setChecked(j.get("fiji_stitch", False))
        self.options.autofocus_cb.setChecked(
            j.get("autofocus", self.ac.microscope.has_z()))
        self.options.add_scalebar_cb.setChecked(j.get("add_scalebar", False))
        self.options.keep_intermediate_cb.setChecked(
            j.get("keep_intermediate", True))
        self.options.html_cb.setChecked(j.get("html", False))
        self.options.quick_stitch_cb.setChecked(j.get("quick_stitch", False))
        self.options.snapshot_grid_cb.setChecked(j.get("snapshot_grid", False))

    def update_imaging_config(self):
        self.imaging_config = {
            "stitch": self.options.stitch_gb.isChecked(),
            "add_scalebar": self.options.add_scalebar_cb.isChecked(),
            "autofocus": self.options.autofocus_cb.isChecked(),
        }


class MainTab(ArgusTab):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Normally opened at startup so stderr is also captured
        self.app_log = app_log.get()
        if self.app_log is None:
            self.app_log = app_log.AppLog(
                os.path.join(self.ac.microscope.usc.bc.get_data_dir(),
                             "log.txt"))
        # must be created early to accept early logging
        # not displayed until later though
        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        # Is this used for something?
        self.log_widget.setObjectName("log_widget")
        # Special case for logging that might occur out of thread
        self.ac.log_msg.connect(self.log)

        self.objective_widget = ObjectiveWidget(ac=self.ac,
                                                aname="objective",
                                                parent=self)
        self.camera_resolution_widget = CameraResolutionWidget(
            ac=self.ac, aname="camera_resolution", parent=self)
        self.imaging_widget = ImagingTaskWidget(
            ac=self.ac,
            go_current_pconfig=lambda **kwargs: self.ac.panoramaTab.
            go_current_pconfig(**kwargs),
            setControlsEnabled=self.setControlsEnabled,
            aname="imaging",
            parent=self)

        version = uscope_version.get_meta().get("description", "null")
        self.log(f"pyuscope starting (version {version})")
        self.log("https://github.com/Labsmore/pyuscope/")
        self.log("For enquiries contact support@labsmore.com")
        self.log("")

        self.motion_widget = MotionWidget(ac=self.ac,
                                          log=self.ac.log,
                                          aname="motion",
                                          parent=self)

    def _initUI(self):
        def get_axes_gb():
            layout = QVBoxLayout()
            layout.addWidget(self.motion_widget)
            gb = QGroupBox("Motion")
            gb.setLayout(layout)
            return gb

        def left_layout():
            layout = QVBoxLayout()
            layout.addWidget(self.objective_widget)
            layout.addWidget(self.camera_resolution_widget)
            layout.addWidget(get_axes_gb())
            layout.addWidget(self.imaging_widget)
            layout.addWidget(self.log_widget, 1)

            widget = QWidget()
            widget.setLayout(layout)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(widget)
            return scroll

        layout = QHBoxLayout()
        layout.addWidget(left_layout())
        self.setLayout(layout)

        # Offload callback to GUI thread so it can do GUI ops

    def log(self, s='', newline=True):
        # This is a "high risk" way for non main thread things to modify GUI
        self.ac.check_thread_safety()

        s = str(s)
        # print("LOG: %s" % s)
        if newline:
            s += '\n'

        c = self.log_widget.textCursor()
        c.clearSelection()
        c.movePosition(QTextCursor.End)
        c.insertText(s)
        self.log_widget.setTextCursor(c)

        self.app_log.write_log(s)
        self.ac.scan_runner.scan_log_write(s)

    def clear_log(self):
        self.log_widget.clear()

    def _post_ui_init(self):
        self.imaging_widget.video_record_widget.recordingChanged.connect(
            lambda recording: self.camera_resolution_widget.set_busy(
                "recording", recording))

    def setControlsEnabled(self, yes):
        self.imaging_widget.snapshot_pb.setEnabled(yes)
        self.camera_resolution_widget.set_busy("scan", not yes)

    def _update_pconfig(self, pconfig):
        self.imaging_widget.update_pconfig(pconfig)


class PanoramaTab(ArgusTab):
    """
    Panoramic (XY tiled) scan setup and run
    Shares file name / options with the Main tab imaging widget
    """
    go_current_pconfig_signal = pyqtSignal(tuple)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imaging_widget = self.ac.mainTab.imaging_widget
        objective_widget = self.ac.mainTab.objective_widget

        self.planner_widget_tabs = QTabWidget()
        self.planner_widget_xy2p = XYPlanner2PWidget(
            ac=self.ac,
            scan_widget=self.imaging_widget,
            objective_widget=objective_widget,
            aname="XY2P",
            parent=self)
        self.planner_widget_xy3p = XYPlanner3PWidget(
            ac=self.ac,
            scan_widget=self.imaging_widget,
            objective_widget=objective_widget,
            aname="XY3P",
            parent=self)

        self.go_current_pconfig_signal.connect(self.go_current_pconfig_slot)

    def _initUI(self):
        layout = QVBoxLayout()
        # Make this default since its more widely used
        self.planner_widget_tabs.addTab(self.planner_widget_xy3p, "XY3P")
        self.planner_widget_tabs.addTab(self.planner_widget_xy2p, "XY2P")
        layout.addWidget(self.planner_widget_tabs)
        layout.addWidget(self.imaging_widget.options.panorama_widget)
        layout.addWidget(self.imaging_widget.go_pause_pb)
        layout.addWidget(self.imaging_widget.progress_bar)
        layout.addStretch()
        self.setLayout(layout)

    def go_current_pconfig(self, callback_done=None):
        planner_hconfig = self.active_planner_widget(
        ).get_current_planner_hconfig()
        if planner_hconfig is None:
            self.ac.log("Failed to get scan config :(")
            return
        # Leave image controls at current value when not batching?
        # Should be a nop but better to just leave alone
        del planner_hconfig["pconfig"]["imager"]["properties"]
        if callback_done:
            planner_hconfig["callback_done"] = callback_done
        self.imaging_widget.go_planner_hconfigs([planner_hconfig])

    def emit_go_current_pconfig(self, callback_done=None):
        self.go_current_pconfig_signal.emit((callback_done, ))

    def go_current_pconfig_slot(self, args):
        callback_done, = args
        self.go_current_pconfig(callback_done=callback_done)

    def active_planner_widget(self):
        return self.planner_widget_tabs.currentWidget()

    def set_xy3p_corners(self, corners):
        self.planner_widget_xy3p.set_corners(corners)
        self.planner_widget_tabs.setCurrentWidget(self.planner_widget_xy3p)

    def _cache_save(self, cachej):
        cachej["panorama"] = {
            "planner_tab": self.planner_widget_tabs.currentIndex(),
        }

    def _cache_load(self, cachej):
        # Was under "main" before panorama got its own tab
        j = cachej.get("panorama") or cachej.get("main", {})
        planner = j.get("planner_tab")
        if planner is not None:
            self.planner_widget_tabs.setCurrentIndex(planner)

    def show_minmax(self, visible):
        self.planner_widget_xy2p.show_minmax(visible)
        self.planner_widget_xy3p.show_minmax(visible)


class ImagerTab(ArgusTab):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hdr_pconfig = None
        self._hdr_checked = None
        self.stabilization_widget = StabilizationWidget(ac=self.ac,
                                                        aname="stabilization",
                                                        parent=self)
        self.stacking_widget = None
        if self.ac.microscope.has_z():
            self.stacking_widget = StackingWidget(ac=self.ac,
                                                  aname="stacking",
                                                  parent=self)

    def _initUI(self):
        # Most of the layout is filled in from the ControlScroll
        self.layout = QVBoxLayout()

        def hdr_widget():
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

            layout.setRowStretch(row, 1)
            widget = QWidget()
            widget.setLayout(layout)
            return widget

        self.capture_tabs = QTabWidget()
        self.capture_tabs.addTab(hdr_widget(), "HDR")
        if self.stacking_widget:
            self.capture_tabs.addTab(self.stacking_widget, "Stacking")
        self.capture_tabs.addTab(self.stabilization_widget, "Stabilization")
        self.capture_tabs.setSizePolicy(QSizePolicy.Preferred,
                                        QSizePolicy.Maximum)
        self.layout.addWidget(self.capture_tabs)
        self.layout.addWidget(self.ac.control_scroll)

        self.setLayout(self.layout)

    def _poll_misc(self):
        auto = self.hdr_auto.isChecked()
        self.hdr_auto_stops.setReadOnly(not auto)
        self.hdr_auto_stops_per.setReadOnly(not auto)
        if auto:
            try:
                # val = self.ac.imager.get_property(self.exposure_property)
                val = self.ac.get_exposure()
                if val is None:
                    return None
                hdr_seq = hdr_exposure_sequence(
                    val, int(self.hdr_auto_stops.text()),
                    float(self.hdr_auto_stops_per.text()))
                le_str = format_exposure_csv(hdr_seq)
                if le_str != self.hdr_le.text():
                    self.hdr_le.setText(le_str)
            except ValueError:
                return

        try:
            # Update cache for snapshot engine
            self._update_pconfig({})
        except ValueError:
            pass

    def hdr_pconfig(self):
        return self._hdr_pconfig

    def image_stabilization_pconfig(self):
        return self.stabilization_widget.image_stabilization_pconfig()

    def stacker_pconfig(self):
        if not self.stacking_widget:
            return None
        return self.stacking_widget.stacker_pconfig()

    def image_stacking_enabled(self):
        return bool(self.stacking_widget
                    and self.stacking_widget.image_stacking_enabled())

    def image_stacking_pm_n(self):
        return self.stacking_widget.image_stacking_pm_n()

    def stack_drift_correction_enabled(self):
        return bool(self.stacking_widget
                    and self.stacking_widget.drift_correction_enabled())

    def check_hdr_resolution(self, properties_list):
        """
        Warn if the camera can't distinguish the requested exposures
        """
        disp = self.ac.get_exposure_disp_property()
        vals = [x[disp] for x in properties_list]
        if vals == self._hdr_checked:
            return
        self._hdr_checked = vals
        prop = self.ac.imager_properties.disp2prop.get(disp, {})
        if prop.get("type") == "int":
            vals = [int(round(x)) for x in vals]
        if len(set(vals)) != len(vals):
            self.ac.log(
                "WARNING: HDR exposures %s are not distinct at camera resolution. Increase exposure or stops per exposure"
                % (",".join(str(x) for x in vals), ))

    def update_pconfig_hdr(self, pconfig):
        raw = str(self.hdr_le.text()).strip()
        if not raw:
            self._hdr_pconfig = None
            return

        try:
            # XXX: consider gain as well
            properties_list = []
            for val in [float(x) for x in raw.split(",")]:
                properties_list.append(
                    {self.ac.get_exposure_disp_property(): val})
        except ValueError:
            self.ac.log("Invalid HDR exposure value")
            raise
        self.check_hdr_resolution(properties_list)
        ret = {
            "properties_list": properties_list,
            # this is probably a good approximation for now
            "tsettle": self.ac.usc.kinematics.tsettle_hdr()
        }
        self._hdr_pconfig = ret
        pconfig.setdefault("imager", {})["hdr"] = ret

    def _update_pconfig(self, pconfig):
        pconfig.setdefault("imager",
                           {})["properties"] = self.ac.imager.get_properties()
        self.update_pconfig_hdr(pconfig)

    def _cache_save(self, cachej):
        cachej["imager"] = {
            "hdr_le": str(self.hdr_le.text()),
        }

    def _cache_load(self, cachej):
        j = cachej.get("imager", {})
        self.hdr_le.setText(j.get("hdr_le", ""))
