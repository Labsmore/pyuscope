#!/usr/bin/env python3

from uscope.gui.gstwidget import GstVideoPipeline, gstwidget_main, get_raw_wh
from uscope.gui.control_scrolls import get_control_scroll
from uscope.util import add_bool_arg
from uscope.config import get_usj, cal_load_all
from uscope.img_util import get_scaled
from uscope.benchmark import Benchmark
from uscope.gui import plugin
from uscope.gst_util import Gst, CaptureSink
from uscope.motion.plugins import get_motion_hal
from uscope.app.argus.threads import MotionThread, PlannerThread
from uscope.planner import microscope_to_planner
from uscope import util
from uscope import config
from uscope.motion import motion_util

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


# XXX: maybe UI preferences should be in different config file?
# need some way to apply preferences while easily pulling in core updates


def argus_job_name_widget(usj=None):
    return usj.get("argus", {}).get("jog_name_widget", "simple")


def argus_show_mdi(usj=None):
    """
    Should argus GUI show MDI?
    For advanced users only
    Bypasses things like gearbox correction
    """
    if not usj:
        usj = get_usj()
    val = os.getenv("ARGUS_MDI", None)
    if val is not None:
        return bool(val)
    return bool(usj.get("argus", {}).get("mdi", 0))


def argus_jog_min(usj=None):
    return int(usj.get("argus", {}).get("jog_min", 1))


"""
GRBL / 3018 stock vlaues
$110=1000.000
$111=1000.000
$112=600.000
"""


# FIXME: default should be actual max jog rate
def argus_jog_max(usj=None):
    return int(usj.get("argus", {}).get("jog_max", 1000))


def get_out_dir(usj):
    return usj.get("argus", {}).get("out_dir", "out")


def get_snapshot_dir(usj):
    return usj.get("argus", {}).get("snapshot_dir", "snapshot")


class JogListener(QPushButton):
    """
    Widget that listens for WSAD keys for linear stage movement
    """
    def __init__(self, label, parent=None):
        super().__init__(label, parent=parent)
        self.parent = parent

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
    def __init__(self, usj, parent=None):
        super().__init__(parent=parent)

        self.usj = usj

        # log scaled to slider
        self.jog_min = argus_jog_min(self.usj)
        self.jog_max = argus_jog_max(self.usj)
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


class MotionWidget(QWidget):
    def __init__(self, motion_thread, usj, parent=None):
        super().__init__(parent=parent)
        self.usj = usj
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

        self.initUI()
        self.last_send = time.time()

    def initUI(self):
        self.setWindowTitle('Demo')

        layout = QVBoxLayout()
        self.listener = JogListener("Jog", self)
        layout.addWidget(self.listener)
        self.slider = JogSlider(usj=self.usj)
        layout.addWidget(self.slider)

        def move_abs():
            layout = QHBoxLayout()
            layout.addWidget(QLabel("Absolute move"))
            self.move_abs_le = QLineEdit()
            self.move_abs_le.returnPressed.connect(self.move_abs_le_process)
            layout.addWidget(self.move_abs_le)
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
        if argus_show_mdi(self.usj):
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
            self.log("Failed to parse move. Need like: X1.0 Y2.4")
        self.motion_thread.move_absolute(pos)

    def mdi_le_process(self):
        if self.mdi_le:
            s = str(self.mdi_le.text())
            self.log("Sending MDI: %s" % s)
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
        # Ignore duplicates, want only real presses
        if event.isAutoRepeat():
            return

        axis = self.axis_map.get(k, None)
        # print("release %s" % (axis, ))
        # return
        if axis:
            # print("cancel jog on release")
            self.motion_thread.stop()


class SimpleNameWidget(QWidget):
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

    def getName(self):
        return str(self.le.text())


'''
FIXME: implement
class DatetimeWidget(QWidget):
    """
    Job name is prefixed with current date and time
    """

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        layout = QHBoxLayout()

        layout.addWidget(QLabel("Job name"))
        self.le = QLineEdit("unknown")
        layout.addWidget(self.le )

        self.setLayout(layout)

    def getName(self):
        return str(self.le.text())
'''


class SiPr0nJobNameWidget(QWidget):
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

    def getName(self):
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

        return vendor + "_" + product + "_" + layer + "_" + objective


class MainWindow(QMainWindow):
    cncProgress = pyqtSignal(int, int, str, int)
    snapshotCaptured = pyqtSignal(int)
    log_msg = pyqtSignal(str)
    pos = pyqtSignal(int)

    def __init__(self, microscope=None, verbose=False):
        QMainWindow.__init__(self)
        self.verbose = verbose
        self.showMaximized()
        self.usj = get_usj(name=microscope)
        self.objective_name_le = None

        self.vidpip = GstVideoPipeline(usj=self.usj,
                                       overview=True,
                                       overview2=True,
                                       roi=True)
        # FIXME: review sizing
        self.vidpip.size_widgets(frac=0.2)
        # self.capture_sink = Gst.ElementFactory.make("capturesink")

        # TODO: some pipelines output jpeg directly
        # May need to tweak this
        raw_input = True
        raw_width, raw_height = get_raw_wh(self.usj)
        self.capture_sink = CaptureSink(width=raw_width,
                                        height=raw_height,
                                        raw_input=raw_input)
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

        # must be created early to accept early logging
        # not displayed until later though
        self.log_widget = QTextEdit()
        # Special case for logging that might occur out of thread
        self.log_msg.connect(self.log)
        # self.pos.connect(self.update_pos)
        self.snapshotCaptured.connect(self.captureSnapshot)

        self.pt = None
        self.log_fd = None
        hal = get_motion_hal(self.usj, log=self.emit_log)
        hal.progress = self.hal_progress
        self.motion_thread = MotionThread(hal=hal, cmd_done=self.cmd_done)
        self.motion_thread.log_msg.connect(self.log)
        self.initUI()

        # Special UI initialization
        # Requires video pipeline already setup
        self.control_scroll = get_control_scroll(self.vidpip, usj=self.usj)
        # screws up the original
        self.imagerTabLayout.addWidget(self.vidpip.get_widget("overview2"))
        self.imagerTabLayout.addWidget(self.control_scroll)
        self.control_scroll.run()

        self.vid_fd = None

        self.motion_thread.start()
        if self.position_poll_timer:
            self.position_poll_timer.start(200)

        # Offload callback to GUI thread so it can do GUI ops
        self.cncProgress.connect(self.processCncProgress)

        self.vidpip.run()

        self.init_imager()

    def __del__(self):
        self.shutdown()

    def shutdown(self):
        try:
            self.motion_thread.hal.ar_stop()
            if self.motion_thread:
                self.motion_thread.thread_stop()
                self.motion_thread = None
            if self.pt:
                self.pt.stop()
                self.pt = None
        except AttributeError:
            pass

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

    def emit_log(self, s='', newline=True):
        # event must be omitted from the correct thread
        # however, if it hasn't been created yet assume we should log from this thread
        self.log_msg.emit(s)

    def poll_update_pos(self):
        last_pos = self.motion_thread.pos_cache
        if last_pos:
            self.update_pos(last_pos)
        # FIXME: hack to avoid concurrency issues with planner and motion thread fighting
        # merge them together?
        if not self.pt:
            self.motion_thread.update_pos_cache()
        self.position_poll_timer.start(400)

    def update_pos(self, pos):
        # FIXME: this is causing screen flickering
        # https://github.com/Labsmore/pyuscope/issues/34
        for axis, axis_pos in pos.items():
            # hack...not all systems use z but is included by default
            if axis == 'z' and axis not in self.axis_pos_label:
                continue
            self.axis_pos_label[axis].setText('%0.3f' % axis_pos)

    def hal_progress(self, pos):
        # self.pos.emit(pos)
        pass

    def emit_pos(self, pos):
        # self.pos.emit(pos)
        pass

    def cmd_done(self, command, args, ret):
        # print("FIXME: poll position instead of manually querying")
        pass

    def reload_obj_cb(self):
        '''Re-populate the objective combo box'''
        self.obj_cb.clear()
        self.obj_config = None
        self.obj_configi = None
        for objective in self.usj["objectives"]:
            self.obj_cb.addItem(objective['name'])

    def update_obj_config(self):
        '''Make resolution display reflect current objective'''
        self.obj_configi = self.obj_cb.currentIndex()
        self.obj_config = self.usj['objectives'][self.obj_configi]
        self.log('Selected objective %s' % self.obj_config['name'])

        im_w_pix = int(self.usj['imager']['width'])
        im_h_pix = int(self.usj['imager']['height'])
        im_w_um = self.obj_config["x_view"]
        im_h_um = im_w_um * im_h_pix / im_w_pix
        self.obj_view.setText('View : %0.3fx %0.3fy' % (im_w_um, im_h_um))
        if self.objective_name_le:
            suffix = self.obj_config.get("suffix")
            if not suffix:
                suffix = self.obj_config.get("name")
            self.objective_name_le.setText(suffix)

    def init_imager(self):
        source = self.vidpip.source_name
        self.log('Loading imager %s...' % source)
        # Gst is pretty ingrained for the GUI
        #
        self.imager = plugin.get_gui_imager(source, self)

    def get_config_layout(self):
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

        return cl

    def get_video_layout(self):
        # Overview
        def low_res_layout():
            layout = QVBoxLayout()
            layout.addWidget(QLabel("Overview"))
            layout.addWidget(self.vidpip.get_widget("overview"))

            return layout

        # Higher res in the center for focusing
        def high_res_layout():
            layout = QVBoxLayout()
            layout.addWidget(QLabel("Focus"))
            layout.addWidget(self.vidpip.get_widget("roi"))

            return layout

        layout = QHBoxLayout()
        layout.addLayout(low_res_layout())
        layout.addLayout(high_res_layout())
        return layout

    def processCncProgress(self, pictures_to_take, pictures_taken, image,
                           first):
        #dbg('Processing CNC progress')
        if first:
            #self.log('First CB with %d items' % pictures_to_take)
            self.pb.setMinimum(0)
            self.pb.setMaximum(pictures_to_take)
            self.bench = Benchmark(pictures_to_take)
        else:
            #self.log('took %s at %d / %d' % (image, pictures_taken, pictures_to_take))
            self.bench.set_cur_items(pictures_taken)
            self.log('Captured: %s' % (image, ))
            self.log('%s' % (str(self.bench)))

        self.pb.setValue(pictures_taken)

    def dry(self):
        return self.dry_cb.isChecked()

    def stop(self):
        if self.pt:
            self.log('Stop requested')
            self.pt.stop()

    def mk_contour_json(self):
        try:
            x0 = float(self.plan_x0_le.text())
            y0 = float(self.plan_y0_le.text())
            x1 = float(self.plan_x1_le.text())
            y1 = float(self.plan_y1_le.text())
        except ValueError:
            self.log("Bad scan x/y")
            return None

        # Planner coordinates must be increasing
        if x0 > x1:
            self.log("X0 must be less than X1")
        if y0 > y1:
            self.log("X0 must be less than X1")

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

    def go(self):
        if not self.snapshot_pb.isEnabled():
            self.log("Wait for snapshot to complete before CNC'ing")
            return

        dry = self.dry()
        if dry:
            dbg('Dry run checked')

        contour_json = self.mk_contour_json()
        if not contour_json:
            return

        def emitCncProgress(pictures_to_take, pictures_taken, image, first):
            #print 'Emitting CNC progress'
            if image is None:
                image = ''
            self.cncProgress.emit(pictures_to_take, pictures_taken, image,
                                  first)

        base_out_dir = get_out_dir(self.usj)
        if not dry and not os.path.exists(base_out_dir):
            os.mkdir(base_out_dir)

        out_dir = os.path.join(base_out_dir, self.jobName.getName())
        if os.path.exists(out_dir):
            self.log("Already exists: %s" % out_dir)
            return
        if not dry:
            os.mkdir(out_dir)

        # If user had started some movement before hitting run wait until its done
        dbg("Waiting for previous movement (if any) to cease")
        # TODO: make this not block GUI
        self.motion_thread.wait_idle()

        pconfig = microscope_to_planner(self.usj,
                                        objective=self.obj_config,
                                        contour=contour_json)

        # not sure if this is the right place to add this
        # plannerj['copyright'] = "&copy; %s John McMaster, CC-BY" % datetime.datetime.today().year

        if 0:
            print("planner_json")
            util.printj(pconfig)

        # Directly goes into planner constructor
        # Make sure everything here is thread safe
        # log param is handled by other thread
        planner_params = {
            # Simple settings written to disk, no objects
            "pconfig": pconfig,
            "motion": self.motion_thread.hal,

            # Typically GstGUIImager
            # Will be offloaded to its own thread
            # Operations must be blocking
            # We enforce that nothing is running and disable all CNC GUI controls
            "imager": self.imager,

            # Callback for progress
            "progress_cb": emitCncProgress,
            "out_dir": out_dir,

            # Includes microscope.json in the output
            "meta_base": {
                "microscope": self.usj
            },

            # Set to true if should try to mimimize hardware actions
            "dry": dry,
            # "overwrite": False,
            "verbosity": 2,
        }

        self.pt = PlannerThread(self, planner_params)
        self.pt.log_msg.connect(self.log)
        self.pt.plannerDone.connect(self.plannerDone)
        self.setControlsEnabled(False)
        if dry:
            self.log_fd = StringIO()
        else:
            self.log_fd = open(os.path.join(out_dir, "log.txt"), "w")

        self.go_pause_pb.setText("Pause")

        if self.get_hdr():
            # Actively driving properties during operation may cause signal thrashing
            # Only take explicit external updates
            # GUI will continue to update to reflect state though
            self.control_scroll.set_push_gui(False)
            self.control_scroll.set_push_prop(False)

        self.pt.start()

    def get_hdr(self):
        hdr = None
        source = self.usj["imager"]["source"]
        cal = cal_load_all(source)
        if cal:
            hdr = cal.get("hdr", None)
        return hdr

    def go_pause(self):
        # CNC already running? pause/continue
        if self.pt:
            # Pause
            if self.pt.is_paused():
                self.go_pause_pb.setText("Pause")
                self.pt.unpause()
            else:
                self.go_pause_pb.setText("Continue")
                self.pt.pause()
        # Go go go!
        else:
            self.go()

    def setControlsEnabled(self, yes):
        self.snapshot_pb.setEnabled(yes)

    def plannerDone(self):
        self.log('RX planner done')
        self.go_pause_pb.setText("Go")
        # Cleanup camera objects
        self.log_fd = None
        self.pt = None
        self.setControlsEnabled(True)
        # Prevent accidental start after done
        self.dry_cb.setChecked(True)

        # Return to normal state if HDR was enabled
        self.control_scroll.set_push_gui(True)
        self.control_scroll.set_push_prop(True)

    """
    def stop(self):
        '''Stop operations after the next operation'''
        self.motion_thread.stop()

    def estop(self):
        '''Stop operations immediately.  Position state may become corrupted'''
        self.motion_thread.estop()

    def clear_estop(self):
        '''Stop operations immediately.  Position state may become corrupted'''
        self.motion_thread.unestop()
    """

    def set_start_pos(self):
        '''
        try:
            lex = float(self.plan_x0_le.text())
        except ValueError:
            self.log('WARNING: bad X value')

        try:
            ley = float(self.plan_y0_le.text())
        except ValueError:
            self.log('WARNING: bad Y value')
        '''
        # take as upper left corner of view area
        # this is the current XY position
        pos = self.motion_thread.pos_cache
        #self.log("Updating start pos w/ %s" % (str(pos)))
        self.plan_x0_le.setText('%0.3f' % pos['x'])
        self.plan_y0_le.setText('%0.3f' % pos['y'])

    def set_end_pos(self):
        # take as lower right corner of view area
        # this is the current XY position + view size
        pos = self.motion_thread.pos_cache
        #self.log("Updating end pos from %s" % (str(pos)))
        x_view = self.obj_config["x_view"]
        y_view = 1.0 * x_view * self.usj['imager']['height'] / self.usj[
            'imager']['width']
        x1 = pos['x'] + x_view
        y1 = pos['y'] + y_view
        self.plan_x1_le.setText('%0.3f' % x1)
        self.plan_y1_le.setText('%0.3f' % y1)

    def get_axes_gb(self):
        """
        Grid layout
        3w x 4h

                X   Y
        Current
        Start
        End
        
        start, end should be buttons to snap current position
        """
        def top():
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

            self.origin = self.usj["motion"].get("origin", "ll")
            assert self.origin in ("ll", "ul"), "Invalid coordinate origin"
            start_label, end_label = {
                "ll": ("Lower left", "Upper right"),
                "ul": ("Upper left", "Lower right"),
            }[self.origin]

            self.plan_start_pb = QPushButton(start_label)
            self.plan_start_pb.clicked.connect(self.set_start_pos)
            gl.addWidget(self.plan_start_pb, row, 0)
            self.plan_x0_le = QLineEdit('0.000')
            gl.addWidget(self.plan_x0_le, row, 1)
            self.plan_y0_le = QLineEdit('0.000')
            gl.addWidget(self.plan_y0_le, row, 2)
            row += 1

            self.plan_end_pb = QPushButton(end_label)
            self.plan_end_pb.clicked.connect(self.set_end_pos)
            gl.addWidget(self.plan_end_pb, row, 0)
            self.plan_x1_le = QLineEdit('0.000')
            gl.addWidget(self.plan_x1_le, row, 1)
            self.plan_y1_le = QLineEdit('0.000')
            gl.addWidget(self.plan_y1_le, row, 2)
            row += 1

            return gl

        layout = QVBoxLayout()
        layout.addLayout(top())
        self.motion_widget = None
        self.position_poll_timer = None
        if 1 or self.usj["motion"]["engine"] == "grbl":
            self.motion_widget = MotionWidget(motion_thread=self.motion_thread,
                                              usj=self.usj)
            layout.addWidget(self.motion_widget)

            self.position_poll_timer = QTimer()
            self.position_poll_timer.timeout.connect(self.poll_update_pos)

        gb = QGroupBox('Motion')
        gb.setLayout(layout)
        return gb

    def get_snapshot_layout(self):
        gb = QGroupBox('Snapshot')
        layout = QGridLayout()

        snapshot_dir = get_snapshot_dir(self.usj)
        if not os.path.isdir(snapshot_dir):
            self.log('Snapshot dir %s does not exist' % snapshot_dir)
            if os.path.exists(snapshot_dir):
                raise Exception("Snapshot directory is not accessible")
            os.mkdir(snapshot_dir)
            self.log('Snapshot dir %s created' % snapshot_dir)

        # nah...just have it in the config
        # d = QFileDialog.getExistingDirectory(self, 'Select snapshot directory', snapshot_dir)

        self.snapshot_serial = -1

        self.snapshot_pb = QPushButton("Snap")
        self.snapshot_pb.clicked.connect(self.take_snapshot)
        layout.addWidget(self.snapshot_pb, 0, 0)

        self.snapshot_fn_le = QLineEdit('snapshot')
        self.snapshot_suffix_le = QLineEdit('.jpg')
        # XXX: since we already have jpegenc this is questionable
        self.snapshot_suffix_le.setEnabled(False)
        self.snapshot_suffix_le.setSizePolicy(
            QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
        hl = QHBoxLayout()
        hl.addWidget(self.snapshot_fn_le)
        hl.addWidget(self.snapshot_suffix_le)
        layout.addLayout(hl, 0, 1)

        gb.setLayout(layout)
        return gb

    def take_snapshot(self):
        self.log('Requesting snapshot')
        # Disable until snapshot is completed
        self.snapshot_pb.setEnabled(False)

        def emitSnapshotCaptured(image_id):
            self.log('Image captured: %s' % image_id)
            self.snapshotCaptured.emit(image_id)

        self.capture_sink.request_image(emitSnapshotCaptured)

    def snapshot_fn(self):
        user = str(self.snapshot_fn_le.text())

        prefix = ''
        # if self.prefix_date_cb.isChecked():
        if 1:
            # 2020-08-12_06-46-21
            prefix = datetime.datetime.utcnow().isoformat().replace(
                'T', '_').replace(':', '-').split('.')[0] + "_"

        extension = str(self.snapshot_suffix_le.text())

        mod = None
        while True:
            mod_str = ''
            if mod:
                mod_str = '_%u' % mod
            fn_full = os.path.join(get_snapshot_dir(self.usj),
                                   prefix + user + mod_str + extension)
            if os.path.exists(fn_full):
                if mod is None:
                    mod = 1
                else:
                    mod += 1
                continue
            return fn_full

    def captureSnapshot(self, image_id):
        self.log('RX image for saving')

        def try_save():
            image = self.capture_sink.pop_image(image_id)
            fn_full = self.snapshot_fn()
            self.log('Capturing %s...' % fn_full)
            factor = float(self.usj['imager']['scalar'])
            # Use a reasonably high quality filter
            try:
                scaled = get_scaled(image, factor, Image.ANTIALIAS)
                extension = str(self.snapshot_suffix_le.text())
                if extension == ".jpg":
                    scaled.save(fn_full, quality=95)
                else:
                    scaled.save(fn_full)
            # FIXME: refine
            except Exception:
                self.log('WARNING: failed to save %s' % fn_full)

        try_save()

        self.snapshot_pb.setEnabled(True)

    def get_scan_layout(self):
        """
        Line up Go/Stop w/ "Job name" to make visually appealing
        """
        def getProgressLayout():
            layout = QHBoxLayout()

            self.go_pause_pb = QPushButton("Go")
            self.go_pause_pb.clicked.connect(self.go_pause)
            layout.addWidget(self.go_pause_pb)

            self.stop_pb = QPushButton("Stop")
            self.stop_pb.clicked.connect(self.stop)
            layout.addWidget(self.stop_pb)

            layout.addWidget(QLabel('Dry?'))
            self.dry_cb = QCheckBox()
            self.dry_cb.setChecked(self.usj.get("motion", {}).get("dry", True))
            layout.addWidget(self.dry_cb)

            self.pb = QProgressBar()
            layout.addWidget(self.pb)

            return layout

        def getJobNameWidget():
            name = argus_job_name_widget(usj=self.usj)
            if name == "simple":
                return SimpleNameWidget()
            elif name == "sipr0n":
                return SiPr0nJobNameWidget()
            else:
                raise ValueError(name)

        layout = QVBoxLayout()
        gb = QGroupBox('Scan')
        self.jobName = getJobNameWidget()
        layout.addWidget(self.jobName)
        layout.addLayout(getProgressLayout())
        gb.setLayout(layout)
        return gb

    def get_bottom_layout(self):
        layout = QHBoxLayout()
        layout.addWidget(self.get_axes_gb())

        def get_lr_layout():
            layout = QVBoxLayout()
            layout.addWidget(self.get_snapshot_layout())
            layout.addWidget(self.get_scan_layout())
            return layout

        layout.addLayout(get_lr_layout())
        return layout

    def initUI(self):
        self.vidpip.setupWidgets()
        self.setWindowTitle('pyuscope')

        def mainLayout():
            layout = QVBoxLayout()
            dbg("get_config_layout()")
            layout.addLayout(self.get_config_layout())
            dbg("get_video_layout()")
            layout.addLayout(self.get_video_layout())
            dbg("get_bottom_layout()")
            layout.addLayout(self.get_bottom_layout())
            self.log_widget.setReadOnly(True)
            layout.addWidget(self.log_widget)
            return layout

        self.tabs = QTabWidget()

        self.mainTab = QWidget()
        self.tabs.addTab(self.mainTab, "Imaging")
        self.mainTab.setLayout(mainLayout())

        # Core is filled in after main UI init
        self.imagerTabLayout = QVBoxLayout()
        self.imagerTab = QWidget()
        self.imagerTab.setLayout(self.imagerTabLayout)
        self.tabs.addTab(self.imagerTab, "Imager")

        self.update_obj_config()

        self.setCentralWidget(self.tabs)
        self.show()
        dbg("initUI done")

    def keyPressEvent(self, event):
        k = event.key()
        if k == Qt.Key_Escape:
            self.stop()


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
