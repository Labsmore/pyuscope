#!/usr/bin/env python3

from uscope.gui.gstwidget import GstVideoPipeline, gstwidget_main
from uscope.gui.control_scrolls import get_control_scroll
from uscope.util import add_bool_arg

from uscope.config import get_usj, cal_load_all
from uscope.imager.imager import Imager, MockImager
from uscope.img_util import get_scaled
from uscope.benchmark import Benchmark

from uscope.motion import hal as cnc_hal
from uscope.motion.lcnc import hal as lcnc_hal
from uscope.motion.lcnc import hal_ar as lcnc_ar
from uscope.motion.lcnc.client import LCNCRPC
from uscope.motion.grbl import GrblHal

from uscope.gst_util import Gst, CaptureSink

from uscope.app.main_gui.threads import MotionThread, PlannerThread
from io import StringIO
import math

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import time
import datetime
import os.path
from PIL import Image
import socket
import sys
import traceback
import threading
import json

from uscope.motion.grbl import get_grbl

usj = get_usj()

debug = 1


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


def get_cnc_hal(log=print):
    try:
        lcnc_host = usj["motion"]["lcnc"]["host"]
    except KeyError:
        lcnc_host = "mk"
    engine = usj['motion']['engine']
    log('get_cnc_hal: %s' % engine)

    if engine == 'mock':
        return cnc_hal.MockHal(log=log)
    # we are on the actual linuxcnc system and can use the API directly
    elif engine == 'lcnc-py':
        import linuxcnc

        return lcnc_hal.LcncPyHal(linuxcnc=linuxcnc, log=log)
    elif engine == 'lcnc-rpc':
        try:
            return lcnc_hal.LcncPyHal(linuxcnc=LCNCRPC(host=lcnc_host),
                                      log=log)
        except socket.error:
            raise
            raise Exception("Failed to connect to LCNCRPC %s" % lcnc_host)
    elif engine == 'lcnc-arpc':
        return lcnc_ar.LcncPyHalAr(host=lcnc_host, log=log)
    elif engine == 'lcnc-rsh':
        return lcnc_hal.LcncRshHal(log=log)
    elif engine == 'grbl':
        return GrblHal()
    else:
        raise Exception("Unknown CNC engine %s" % engine)


class GstGUIImager(Imager):

    class Emitter(QObject):
        change_properties = pyqtSignal(dict)

    def __init__(self, gui):
        Imager.__init__(self)
        self.gui = gui
        self.image_ready = threading.Event()
        self.image_id = None
        self.emitter = GstGUIImager.Emitter()
        # FIXME
        self.width, self.height = (640, 480)

    def wh(self):
        return self.width, self.height

    def next_image(self):
        #self.gui.emit_log('gstreamer imager: taking image to %s' % file_name_out)
        def got_image(image_id):
            self.gui.emit_log('Image captured reported: %s' % image_id)
            self.image_id = image_id
            self.image_ready.set()

        self.image_id = None
        self.image_ready.clear()
        self.gui.capture_sink.request_image(got_image)
        self.gui.emit_log('Waiting for next image...')
        self.image_ready.wait()
        self.gui.emit_log('Got image %s' % self.image_id)
        return self.gui.capture_sink.pop_image(self.image_id)

    def get_normal(self):
        image = self.next_image()
        factor = float(usj['imager']['scalar'])
        scaled = get_scaled(image, factor, Image.ANTIALIAS)
        return {"0": scaled}

    def get_hdr(self, hdr):
        ret = {}
        factor = float(usj['imager']['scalar'])
        for hdri, hdrv in enumerate(hdr["properties"]):
            print("hdr: set %u %s" % (hdri, hdrv))
            self.emitter.change_properties.emit(hdrv)
            # Wait for setting to take effect
            time.sleep(hdr["tsleep"])
            image = self.next_image()
            scaled = get_scaled(image, factor, Image.ANTIALIAS)
            ret["%u" % hdri] = scaled
        return ret

    def get(self):
        # FIXME: cache at beginning of scan somehow
        hdr = None
        source = usj['imager']['source']
        cal = cal_load_all(source)
        if cal:
            hdr = cal.get("hdr", None)
        if hdr:
            return self.get_hdr(hdr)
        else:
            return self.get_normal()

    def add_planner_metadata(self, imagerj):
        """
        # TODO: instead dump from actual v4l
        # safer and more comprehensive
        v4lj = {}
        for k, v in self.v4ls.iteritems():
            v4lj[k] = int(str(v.text()))
        imagerj["v4l"] = v4lj
        """


"""
Widget that listens for WSAD keys for linear stage movement
"""


class JogListener(QPushButton):

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


class MotionWidget(QWidget):

    def __init__(self, motion_thread, parent=None):
        super().__init__(parent=parent)
        self.motion_thread = motion_thread

        self.axis_map = {
            # Upper left origin
            Qt.Key_A: ("X", -1),
            Qt.Key_D: ("X", 1),
            Qt.Key_S: ("Y", -1),
            Qt.Key_W: ("Y", 1),
            Qt.Key_Q: ("Z", -1),
            Qt.Key_E: ("Z", 1),
        }

        # log scaled to slider
        self.jog_min = 1
        self.jog_max = 1000
        self.jog_cur = None
        # careful hard coded below as 2.0
        self.slider_min = 1
        self.slider_max = 100

        self.initUI()
        self.last_send = time.time()

    def initUI(self):
        self.setWindowTitle('Demo')

        def labels():
            layout = QHBoxLayout()
            layout.addWidget(QLabel("1"))
            layout.addWidget(QLabel("10"))
            layout.addWidget(QLabel("100"))
            layout.addWidget(QLabel("1000"))
            return layout

        layout = QVBoxLayout()
        self.listener = JogListener("Jog", self)
        layout.addWidget(self.listener)
        layout.addLayout(labels())
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(self.slider_min)
        self.slider.setMaximum(self.slider_max)
        self.slider.setValue(self.slider_max // 2)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTickInterval(33)
        # Send keyboard events to CNC navigation instead
        self.slider.setFocusPolicy(Qt.NoFocus)
        layout.addWidget(self.slider)

        def mdi():
            layout = QHBoxLayout()
            layout.addWidget(QLabel("MDI"))
            self.mdi_le = QLineEdit()
            self.mdi_le.returnPressed.connect(self.mdi_process)
            layout.addWidget(self.mdi_le)
            return layout

        layout.addLayout(mdi())

        self.slider.valueChanged.connect(self.sliderChanged)
        self.sliderChanged()

        self.setLayout(layout)

    def mdi_process(self):
        self.motion_thread.mdi(str(self.mdi_le.text()))

    def sliderChanged(self):
        slider_val = float(self.slider.value())
        v = math.log(slider_val, 10)
        # Scale in log space
        log_scalar = (math.log(self.jog_max, 10) -
                      math.log(self.jog_min, 10)) / 2.0
        v = math.log(self.jog_min, 10) + v * log_scalar
        # Convert back to linear space
        v = 10**v
        self.jog_cur = max(min(v, self.jog_max), self.jog_min)
        print("jog: slider %u => jog %u (was %u)" %
              (slider_val, self.jog_cur, v))
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
            self.motion_thread.jog({axis: sign})

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
            self.motion_thread.cancel_jog()


class MainWindow(QMainWindow):
    cncProgress = pyqtSignal(int, int, str, int)
    snapshotCaptured = pyqtSignal(int)
    log_msg = pyqtSignal(str)
    pos = pyqtSignal(int)

    def __init__(self, source=None, controls=True):
        QMainWindow.__init__(self)
        self.showMaximized()
        self.usj = usj
        self.objective_name_le = None

        self.vidpip = GstVideoPipeline(source=source,
                                       overview=True,
                                       overview2=True,
                                       roi=True)
        # FIXME: review sizing
        self.vidpip.size_widgets(frac=0.5)
        # self.capture_sink = Gst.ElementFactory.make("capturesink")

        # TODO: some pipelines output jpeg directly
        # May need to tweak this
        raw_input = True
        self.capture_sink = CaptureSink(width=int(self.usj['imager']['width']),
                                        height=int(
                                            self.usj['imager']['height']),
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
        hal = get_cnc_hal(log=self.emit_log)
        hal.progress = self.hal_progress
        self.motion_thread = MotionThread(hal=hal, cmd_done=self.cmd_done)
        self.motion_thread.log_msg.connect(self.log)
        self.initUI()

        # Special UI initialization
        # Requires video pipeline already setup
        self.control_scroll = get_control_scroll(self.vidpip)
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

        if self.usj['motion']['startup_run']:
            self.run()

    def __del__(self):
        self.shutdown()

    def shutdown(self):
        try:
            self.motion_thread.hal.ar_stop()
            if self.motion_thread:
                self.motion_thread.stop()
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
        # FIXME: hack to avoid concurrency issues with planner and motion thread fighting
        # merge them together?
        if not self.pt:
            self.update_pos(self.motion_thread.pos())
        self.position_poll_timer.start(1000)

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
        for objective in self.usj['objective']:
            self.obj_cb.addItem(objective['name'])

    def update_obj_config(self):
        '''Make resolution display reflect current objective'''
        self.obj_configi = self.obj_cb.currentIndex()
        self.obj_config = self.usj['objective'][self.obj_configi]
        self.log('Selected objective %s' % self.obj_config['name'])

        im_w_pix = int(self.usj['imager']['width'])
        im_h_pix = int(self.usj['imager']['height'])
        im_w_um = self.obj_config["x_view"]
        im_h_um = im_w_um * im_h_pix / im_w_pix
        self.obj_view.setText('View : %0.3fx %0.3fy' % (im_w_um, im_h_um))
        if self.objective_name_le:
            self.objective_name_le.setText(self.obj_config["suffix"])

    def init_imager(self):
        source = self.vidpip.source_name
        self.log('Loading imager %s...' % source)
        if source == 'mock':
            self.imager = MockImager()
        elif source.find("gst-") == 0:
            self.imager = GstGUIImager(self)
            self.imager.emitter.change_properties.connect(
                self.control_scroll.set_disp_properties)
        else:
            raise Exception('Invalid imager type %s' % source)

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

    def write_scan_json(self):
        scan_json = {
            "overlap": 0.7,
            "border": 0.1,
            "start": {
                "x": None,
                "y": None
            },
            "end": {
                "x": None,
                "y": None
            }
        }

        try:
            # scan_json['overlap'] = float(self.overlap_le.text())
            # scan_json['border'] = float(self.border_le.text())

            scan_json['start']['x'] = float(self.plan_x0_le.text())
            scan_json['start']['y'] = float(self.plan_y0_le.text())
            scan_json['end']['x'] = float(self.plan_x1_le.text())
            scan_json['end']['y'] = float(self.plan_y1_le.text())
        except ValueError:
            self.log("Bad position")
            return False
        json.dump(scan_json, open('scan.json', 'w'), indent=4, sort_keys=True)
        return True

    def go(self):
        if not self.snapshot_pb.isEnabled():
            self.log("Wait for snapshot to complete before CNC'ing")
            return

        dry = self.dry()
        if dry:
            dbg('Dry run checked')

        if not self.write_scan_json():
            return

        def emitCncProgress(pictures_to_take, pictures_taken, image, first):
            #print 'Emitting CNC progress'
            if image is None:
                image = ''
            self.cncProgress.emit(pictures_to_take, pictures_taken, image,
                                  first)

        if not dry and not os.path.exists(self.usj['out_dir']):
            os.mkdir(self.usj['out_dir'])

        out_dir = os.path.join(self.usj['out_dir'], self.getJobName())
        if os.path.exists(out_dir):
            self.log("job name dir %s already exists" % out_dir)
            return
        if not dry:
            os.mkdir(out_dir)

        rconfig = {
            'motion': self.motion_thread.hal,

            # Will be offloaded to its own thread
            # Operations must be blocking
            # We enforce that nothing is running and disable all CNC GUI controls
            'imager': self.imager,

            # Callback for progress
            'progress_cb': emitCncProgress,
            'out_dir': out_dir,

            # Comprehensive config structure
            'uscope': self.usj,
            # Which objective to use in above config
            'obj': self.obj_configi,

            # Set to true if should try to mimimize hardware actions
            'dry': dry,
            'overwrite': False,
        }

        # If user had started some movement before hitting run wait until its done
        dbg("Waiting for previous movement (if any) to cease")
        # TODO: make this not block GUI
        self.motion_thread.wait_idle()
        """
        {
            //input directly into planner
            "params": {
                x0: 123,
                y0: 356,
            }
            //planner generated parameters
            "planner": {
                "mm_width": 2.280666667,
                "mm_height": 2.232333333,
                "pix_width": 6842,
                "pix_height": 6697,
                "pix_nm": 333.000000,
            },
            //source specific parameters 
            "imager": {
                "microscope.json": {
                    ...
                }
                "objective": "mit20x",
                "v4l": {
                    "rbal": 123,
                    "bbal": 234,
                    "gain": 345,
                    "exposure": 456
            },
            "sticher": {
                "type": "xystitch"
            },
            "copyright": "&copy; 2020 John McMaster, CC-BY",
        }
        """
        # obj = rconfig['uscope']['objective'][rconfig['obj']]

        imagerj = {}
        imagerj["microscope.json"] = usj
        usj["imager"]["calibration"] = self.control_scroll.get_disp_properties(
        )

        # not sure if this is the right place to add this
        # imagerj['copyright'] = "&copy; %s John McMaster, CC-BY" % datetime.datetime.today().year
        imagerj['objective'] = rconfig['obj']

        self.imager.add_planner_metadata(imagerj)

        self.pt = PlannerThread(self, rconfig, imagerj)
        self.pt.log_msg.connect(self.log)
        self.pt.plannerDone.connect(self.plannerDone)
        self.setControlsEnabled(False)
        if dry:
            self.log_fd = StringIO()
        else:
            self.log_fd = open(os.path.join(out_dir, 'log.txt'), 'w')

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
        source = usj['imager']['source']
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
        self.motion_thread.hal.dry = False
        self.setControlsEnabled(True)
        if self.usj['motion']['startup_run_exit']:
            print('Planner debug break on completion')
            os._exit(1)
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
        pos = self.motion_thread.pos()
        #self.log("Updating start pos w/ %s" % (str(pos)))
        self.plan_x0_le.setText('%0.3f' % pos['x'])
        self.plan_y0_le.setText('%0.3f' % pos['y'])

    def set_end_pos(self):
        # take as lower right corner of view area
        # this is the current XY position + view size
        pos = self.motion_thread.pos()
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
            row += 1

            self.axis_pos_label = {}
            gl.addWidget(QLabel("Current"), row, 0)
            label = QLabel("?")
            gl.addWidget(label, row, 1)
            self.axis_pos_label['x'] = label
            label = QLabel("?")
            gl.addWidget(label, row, 2)
            self.axis_pos_label['y'] = label
            row += 1

            self.plan_start_pb = QPushButton("Start")
            self.plan_start_pb.clicked.connect(self.set_start_pos)
            gl.addWidget(self.plan_start_pb, row, 0)
            self.plan_x0_le = QLineEdit('0.000')
            gl.addWidget(self.plan_x0_le, row, 1)
            self.plan_y0_le = QLineEdit('0.000')
            gl.addWidget(self.plan_y0_le, row, 2)
            row += 1

            self.plan_end_pb = QPushButton("End")
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
            self.motion_widget = MotionWidget(motion_thread=self.motion_thread)
            layout.addWidget(self.motion_widget)

            self.position_poll_timer = QTimer()
            self.position_poll_timer.timeout.connect(self.poll_update_pos)

        gb = QGroupBox('Motion')
        gb.setLayout(layout)
        return gb

    def get_snapshot_layout(self):
        gb = QGroupBox('Snapshot')
        layout = QGridLayout()

        snapshot_dir = self.usj['imager']['snapshot_dir']
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
            fn_full = os.path.join(self.usj['imager']['snapshot_dir'],
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
            self.dry_cb.setChecked(self.usj['motion']['dry'])
            layout.addWidget(self.dry_cb)

            self.pb = QProgressBar()
            layout.addWidget(self.pb)

            return layout

        def getNameLayout():
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

            return layout

        layout = QVBoxLayout()
        gb = QGroupBox('Scan')
        layout.addLayout(getNameLayout())
        layout.addLayout(getProgressLayout())
        gb.setLayout(layout)
        return gb

    def getJobName(self):
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
        self.setWindowTitle('pr0ncnc')

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
    add_bool_arg(parser, '--controls', default=True)
    parser.add_argument('source', nargs="?", default=None)
    args = parser.parse_args()

    return vars(args)


def main():
    try:
        gstwidget_main(MainWindow, parse_args=parse_args)
    except Exception as e:
        print(traceback.format_exc(-1))
        error(str(e))


if __name__ == '__main__':
    main()