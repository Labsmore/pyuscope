#!/usr/bin/env python

from config import get_config
from uvscada.imager import Imager
from uvscada.img_util import get_scaled
from uvscada.benchmark import Benchmark
from uvscada.imager import MockImager
from uvscada.cnc_hal import hal as cnc_hal
from uvscada.cnc_hal import lcnc_ar
from uvscada.cnc_hal import lcnc as lcnc_hal
from uvscada.lcnc.client import LCNCRPC
from uvscada import gst_util
from uvscada.v4l2_util import ctrl_set

from threads import CncThread, PlannerThread
from cStringIO import StringIO

from PyQt4 import Qt
from PyQt4.QtGui import *
from PyQt4.QtCore import *

import os.path
from PIL import Image
import re
import signal
import socket
import sys
import traceback
import threading
import json

uconfig = get_config()

gobject = None
gst = None
try:
    import gobject
    import gst
    gst_util.register()
except ImportError:
    if uconfig['imager']['engine'] == 'gstreamer' or uconfig['imager']['engine'] == 'gstreamer-testrc':
        print 'Failed to import a gstreamer package when gstreamer is required'
        raise

debug = 1
def dbg(*args):
    if not debug:
        return
    if len(args) == 0:
        print
    elif len(args) == 1:
        print 'main: %s' % (args[0], )
    else:
        print 'main: ' + (args[0] % args[1:])

def get_cnc_hal(log):
    print 'get_cnc_hal', log
    engine = uconfig['cnc']['engine']
    if engine == 'mock':
        return cnc_hal.MockHal(log=log)
    elif engine == 'lcnc-py':
        import linuxcnc

        return lcnc_hal.LcncPyHal(linuxcnc=linuxcnc, log=log)
    elif engine == 'lcnc-rpc':
        host = uconfig["cnc"]["lcnc"]["host"]
        try:
            return lcnc_hal.LcncPyHal(linuxcnc=LCNCRPC(host=host), log=log)
        except socket.error:
            raise
            raise Exception("Failed to connect to LCNCRPC %s" % host)
    elif engine == 'lcnc-arpc':
        host = uconfig["cnc"]["lcnc"]["host"]
        return lcnc_ar.LcncPyHalAr(host=host, log=log)
    elif engine == 'lcnc-rsh':
        return lcnc_hal.LcncRshHal(log=log)
    else:
        raise Exception("Unknown CNC engine %s" % engine)

    '''
    # pr0ndexer (still on MicroControle hardware though)
    elif engine == 'pdc':
        try:
            #return PDC(debug=False, log=log, config=config)
            return cnc_hal.PdcHal(log=log)
        except IOError:
            print 'Failed to open PD device'
            raise
    '''
    '''
    Instead of auto lets support a fallback allowed option
    elif engine == 'auto':
        raise Exception('FIXME')
        log('Failed to open device, falling back to mock')
        return cnc_hal.MockHal(log=log)
    '''

class AxisWidget(QWidget):
    def __init__(self, axis, cnc_thread, parent = None):
        QWidget.__init__(self, parent)

        self.axis = axis
        self.cnc_thread = cnc_thread

        self.gb = QGroupBox('Axis %s' % self.axis.upper())
        self.gl = QGridLayout()
        self.gb.setLayout(self.gl)
        row = 0

        self.gl.addWidget(QLabel("Pos (mm):"), row, 0)
        self.pos_value = QLabel("Unknown")
        self.gl.addWidget(self.pos_value, row, 1)
        row += 1

        # Return to 0 position
        self.ret0_pb = QPushButton("Ret0")
        self.ret0_pb.clicked.connect(self.ret0)
        self.gl.addWidget(self.ret0_pb, row, 0)
        # Set the 0 position
        self.home_pb = QPushButton("Home")
        self.home_pb.clicked.connect(self.home)
        self.gl.addWidget(self.home_pb, row, 1)
        row += 1

        self.abs_pos_le = QLineEdit('0.0')
        self.gl.addWidget(self.abs_pos_le, row, 0)
        self.mv_abs_pb = QPushButton("Go absolute (mm)")
        self.mv_abs_pb.clicked.connect(self.mv_abs)
        self.gl.addWidget(self.mv_abs_pb, row, 1)
        row += 1

        self.rel_pos_le = QLineEdit('0.0')
        self.gl.addWidget(self.rel_pos_le, row, 0)
        self.mv_rel_pb = QPushButton("Go relative (mm)")
        self.mv_rel_pb.clicked.connect(self.mv_rel)
        self.gl.addWidget(self.mv_rel_pb, row, 1)
        row += 1

        '''
        self.meas_label = QLabel("Meas (um)")
        self.gl.addWidget(self.meas_label, row, 0)
        self.meas_value = QLabel("Unknown")
        self.gl.addWidget(self.meas_value, row, 1)
        # Only resets in the GUI, not related to internal axis position counter
        self.meas_reset_pb = QPushButton("Reset meas")
        self.meas_reset()
        self.meas_reset_pb.clicked.connect(self.meas_reset)
        self.axisSet.connect(self.update_meas)
        self.gl.addWidget(self.meas_reset_pb, row, 0)
        row += 1
        '''

        self.l = QHBoxLayout()
        self.l.addWidget(self.gb)
        self.setLayout(self.l)

    def home(self):
        self.cnc_thread.cmd('home', [self.axis])

    def ret0(self):
        self.cnc_thread.cmd('mv_abs', {self.axis: 0.0})

    def mv_rel(self):
        self.cnc_thread.cmd('mv_rel', {self.axis: float(str(self.rel_pos_le.text()))})

    def mv_abs(self):
        self.cnc_thread.cmd('mv_abs', {self.axis: float(str(self.abs_pos_le.text()))})

class GstImager(Imager):
    def __init__(self, gui):
        Imager.__init__(self)
        self.gui = gui
        self.image_ready = threading.Event()
        self.image_id = None

    def get(self):
        #self.gui.emit_log('gstreamer imager: taking image to %s' % file_name_out)
        def emitSnapshotCaptured(image_id):
            self.gui.emit_log('Image captured reported: %s' % image_id)
            self.image_id = image_id
            self.image_ready.set()

        self.image_id = None
        self.image_ready.clear()
        self.gui.capture_sink.request_image(emitSnapshotCaptured)
        self.gui.emit_log('Waiting for next image...')
        self.image_ready.wait()
        self.gui.emit_log('Got image %s' % self.image_id)
        image = self.gui.capture_sink.pop_image(self.image_id)
        factor = float(uconfig['imager']['scalar'])
        # Use a reasonably high quality filter
        scaled = get_scaled(image, factor, Image.ANTIALIAS)
        #if not self.gui.dry():
        #    scaled.save(file_name_out)
        return scaled

class CNCGUI(QMainWindow):
    cncProgress = pyqtSignal(int, int, str, int)
    snapshotCaptured = pyqtSignal(int)

    def __init__(self):
        QMainWindow.__init__(self)
        self.showMaximized()
        self.uconfig = uconfig

        self.jog_run = None
        self.jog_running = {}

        # must be created early to accept early logging
        # not displayed until later though
        self.log_widget = QTextEdit()
        # Special case for logging that might occur out of thread
        self.connect(self, SIGNAL('log'), self.log)
        self.connect(self, SIGNAL('pos'), self.update_pos)

        self.pt = None
        self.log_fd = None
        hal = get_cnc_hal(log=self.emit_log)
        hal.progress = self.hal_progress
        self.cnc_thread = CncThread(hal=hal, cmd_done=self.cmd_done)
        self.connect(self.cnc_thread, SIGNAL('log'), self.log)
        self.initUI()

        self.vid_fd = None

        # Must not be initialized until after layout is set
        self.gstWindowId = None
        engine_config = self.uconfig['imager']['engine']
        if engine_config == 'auto':
            if os.path.exists("/dev/video0"):
                engine_config = 'gstreamer'
            else:
                engine_config = 'gstreamer-testsrc'
            self.log('Auto image engine: selected %s' % engine_config)
        if engine_config == 'gstreamer':
            self.source = gst.element_factory_make("v4l2src", "vsource")
            self.source.set_property("device", "/dev/video0")
            self.vid_fd = -1
            self.setupGst()
        elif engine_config == 'gstreamer-testsrc':
            self.source = gst.element_factory_make("videotestsrc", "video-source")
            self.setupGst()
        elif engine_config == 'mock':
            pass
        else:
            raise Exception('Unknown engine %s' % (engine_config,))

        self.cnc_thread.start()

        # Offload callback to GUI thread so it can do GUI ops
        self.cncProgress.connect(self.processCncProgress)

        if self.gstWindowId:
            dbg("Starting gstreamer pipeline")
            self.player.set_state(gst.STATE_PLAYING)

        if self.uconfig['cnc']['startup_run']:
            self.run()

    def __del__(self):
        self.shutdown()

    def shutdown(self):
        self.cnc_thread.hal.ar_stop()
        if self.cnc_thread:
            self.cnc_thread.stop()
            self.cnc_thread = None
        if self.pt:
            self.pt.stop()
            self.pt = None

    def log(self, s='', newline=True):
        if newline:
            s += '\n'

        c = self.log_widget.textCursor()
        c.clearSelection()
        c.movePosition(QTextCursor.End)
        c.insertText(s)
        self.log_widget.setTextCursor(c)

        if self.log_fd is not None:
            self.log_fd.write(s)

    def emit_log(self, s='', newline=True):
        # event must be omitted from the correct thread
        # however, if it hasn't been created yet assume we should log from this thread
        self.emit(SIGNAL('log'), s)

    def update_pos(self, pos):
        for axis, axis_pos in pos.iteritems():
            self.axes[axis].pos_value.setText('%0.3f' % axis_pos)

    def hal_progress(self, pos):
        self.emit(SIGNAL('pos'), pos)

    def emit_pos(self, pos):
        self.emit(SIGNAL('pos'), pos)

    def cmd_done(self, cmd, args, ret):
        def default(*args):
            pass

        {
            'mv_abs': self.emit_pos,
            'mv_rel': self.emit_pos,
            'home': self.emit_pos,
        }.get(cmd, default)(ret)

    def reload_obj_cb(self):
        '''Re-populate the objective combo box'''
        self.obj_cb.clear()
        self.obj_config = None
        self.obj_configi = None
        for objective in self.uconfig['objective']:
            self.obj_cb.addItem(objective['name'])

    def update_obj_config(self):
        '''Make resolution display reflect current objective'''
        self.obj_configi = self.obj_cb.currentIndex()
        self.obj_config = self.uconfig['objective'][self.obj_configi]
        self.log('Selected objective %s' % self.obj_config['name'])

        im_w_pix = int(self.uconfig['imager']['width'])
        im_h_pix = int(self.uconfig['imager']['height'])
        im_w_um = self.obj_config["x_view"]
        im_h_um = im_w_um * im_h_pix / im_w_pix
        self.obj_view.setText('View : %0.3fx %0.3fy' % (im_w_um, im_h_um))

    def update_v4l_config(self):
        pass

    def v4l_updated(self):
        for k, v in self.v4ls.iteritems():
            try:
                val = int(str(v.text()))
            except ValueError:
                continue
            if k == 'E':
                val = min(val, 800)
            else:
                val = min(val, 1023)
            ctrl_set(self.vid_fd, k, val)

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
        self.update_obj_config()
        row += 1

        if 0:
            cl.addWidget(QLabel("Sensor config"), row, 0)
            self.v4l_cb = QComboBox()
            cl.addWidget(self.v4l_cb, row, 1)
            self.v4l_cb.currentIndexChanged.connect(self.update_v4l_config)
            row += 1

        self.v4ls = {}
        # hacked driver to directly drive values
        for ki, (label, v4l_name) in enumerate((("Red", "Red Balance"), ("Green", "Gain"), ("Blue", "Blue Balance"), ("Exp", "Exposure"))):
            cols = 4
            rowoff = ki / cols
            coloff = cols * (ki % cols)

            cl.addWidget(QLabel(label), row + rowoff, coloff)
            le = QLineEdit('')
            self.v4ls[v4l_name] = le
            cl.addWidget(le, row + rowoff, coloff + 1)
            le.textChanged.connect(self.v4l_updated)
        row += 2

        return cl

    def get_video_layout(self):
        # Overview
        def low_res_layout():
            layout = QVBoxLayout()
            layout.addWidget(QLabel("Overview"))

            # Raw X-windows canvas
            self.video_container = QWidget()
            # Allows for convenient keyboard control by clicking on the video
            self.video_container.setFocusPolicy(Qt.ClickFocus)
            # TODO: do something more proper once integrating vodeo feed
            w, h = 800, 600
            w, h = 3264/8, 2448/8
            self.video_container.setMinimumSize(w, h)
            self.video_container.resize(w, h)
            policy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.video_container.setSizePolicy(policy)

            layout.addWidget(self.video_container)

            return layout

        # Higher res in the center for focusing
        def high_res_layout():
            layout = QVBoxLayout()
            layout.addWidget(QLabel("Focus"))

            # Raw X-windows canvas
            self.video_container2 = QWidget()
            # TODO: do something more proper once integrating vodeo feed
            w, h = 800, 600
            w, h = 3264/8, 2448/8
            self.video_container2.setMinimumSize(w, h)
            self.video_container2.resize(w, h)
            policy = QSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.video_container2.setSizePolicy(policy)

            layout.addWidget(self.video_container2)

            return layout

        layout = QHBoxLayout()
        layout.addLayout(low_res_layout())
        layout.addLayout(high_res_layout())
        return layout

    def setupGst(self):
        dbg("Setting up gstreamer pipeline")
        self.gstWindowId = self.video_container.winId()
        self.gstWindowId2 = self.video_container2.winId()

        self.player = gst.Pipeline("player")
        sinkx = gst.element_factory_make("ximagesink", 'sinkx_overview')
        sinkx_focus = gst.element_factory_make("ximagesink", 'sinkx_focus')
        fcs = gst.element_factory_make('ffmpegcolorspace')
        #caps = gst.caps_from_string('video/x-raw-yuv')

        self.tee = gst.element_factory_make("tee")

        self.capture_enc = gst.element_factory_make("jpegenc")
        self.capture_sink = gst.element_factory_make("capturesink")
        self.resizer =  gst.element_factory_make("videoscale")
        self.snapshotCaptured.connect(self.captureSnapshot)
        self.capture_sink_queue = gst.element_factory_make("queue")

        '''
        Per #gstreamer question evidently v4l2src ! ffmpegcolorspace ! ximagesink
            gst-launch v4l2src ! ffmpegcolorspace ! ximagesink
        allocates memory different than v4l2src ! videoscale ! xvimagesink
            gst-launch v4l2src ! videoscale ! xvimagesink
        Problem is that the former doesn't resize the window but allows taking full res pictures
        The later resizes the window but doesn't allow taking full res pictures
        However, we don't want full res in the view window
        '''
        # Video render stream
        self.player.add(self.source, self.tee)
        gst.element_link_many(self.source, self.tee)

        self.size_tee = gst.element_factory_make("tee")
        self.size_queue_overview = gst.element_factory_make("queue")
        self.size_queue_focus = gst.element_factory_make("queue")
        # First lets make this identical to keep things simpler
        self.videocrop = gst.element_factory_make("videocrop")
        '''
        TODO: make this more automagic
        w, h = 3264/8, 2448/8 => 408, 306
        Want 3264/2, 2448,2 type resolution
        Image is coming in raw at this point which menas we need to end up with
        408*2, 306*2 => 816, 612
        since its centered crop the same amount off the top and bottom:
        (3264 - 816)/2, (2448 - 612)/2 => 1224, 918
        '''
        self.videocrop.set_property("top", 918)
        self.videocrop.set_property("bottom", 918)
        self.videocrop.set_property("left", 1224)
        self.videocrop.set_property("right", 1224)
        self.scale2 = gst.element_factory_make("videoscale")

        self.player.add(fcs, self.size_tee)
        gst.element_link_many(self.tee, fcs, self.size_tee)
        self.player.add(self.size_queue_overview, self.resizer, sinkx)
        gst.element_link_many(self.size_tee, self.size_queue_overview, self.resizer, sinkx)
        # gah
        # libv4l2: error converting / decoding frame data: v4l-convert: error destination buffer too small (16777216 < 23970816)
        # aha: the culprit is that I'm running the full driver which is defaulting to lower res
        self.player.add(self.size_queue_focus, self.videocrop, self.scale2, sinkx_focus)
        gst.element_link_many(self.size_tee, self.size_queue_focus, self.videocrop, self.scale2, sinkx_focus)

        # Frame grabber stream
        # compromise
        self.player.add(self.capture_sink_queue, self.capture_enc, self.capture_sink)
        gst.element_link_many(self.tee, self.capture_sink_queue, self.capture_enc, self.capture_sink)

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)
        bus.connect("sync-message::element", self.on_sync_message)

    def on_message(self, bus, message):
        t = message.type

        if self.vid_fd is not None and self.vid_fd < 0:
            self.vid_fd = self.source.get_property("device-fd")
            if self.vid_fd >= 0:
                print 'Initializing V4L controls'
                vconfig = uconfig["imager"].get("v4l2", None)
                if vconfig:
                    for configk, configv in vconfig.iteritems():
                        break
                    print 'Selected config %s' % configk

                    for k, v in configv.iteritems():
                        #ctrl_set(self.vid_fd, k, v)
                        if k in self.v4ls:
                            self.v4ls[k].setText(str(v))

        if t == gst.MESSAGE_EOS:
            self.player.set_state(gst.STATE_NULL)
            print "End of stream"
        elif t == gst.MESSAGE_ERROR:
            err, debug = message.parse_error()
            print "Error: %s" % err, debug
            self.player.set_state(gst.STATE_NULL)

    def on_sync_message(self, bus, message):
        if message.structure is None:
            return
        message_name = message.structure.get_name()
        if message_name == "prepare-xwindow-id":
            if message.src.get_name() == 'sinkx_overview':
                #print 'sinkx_overview win_id'
                win_id = self.gstWindowId
            elif message.src.get_name() == 'sinkx_focus':
                win_id = self.gstWindowId2
                #print 'sinkx_focus win_id'
            else:
                raise Exception('oh noes')

            assert win_id
            imagesink = message.src
            imagesink.set_xwindow_id(win_id)

    def ret0(self):
        pos = dict([(k, 0.0) for k in self.axes])
        self.cnc_thread.cmd('mv_abs', pos)

    def home(self):
        self.cnc_thread.cmd('home', [k for k in self.axes])

    def mv_rel(self):
        pos = dict([(k, float(str(axis.rel_pos_le.text()))) for k, axis in self.axes.iteritems()])
        self.cnc_thread.cmd('mv_rel', pos)

    def mv_abs(self):
        pos = dict([(k, float(str(axis.abs_pos_le.text()))) for k, axis in self.axes.iteritems()])
        self.cnc_thread.cmd('mv_abs', pos)

    def processCncProgress(self, pictures_to_take, pictures_taken, image, first):
        #dbg('Processing CNC progress')
        if first:
            #self.log('First CB with %d items' % pictures_to_take)
            self.pb.setMinimum(0)
            self.pb.setMaximum(pictures_to_take)
            self.bench = Benchmark(pictures_to_take)
        else:
            #self.log('took %s at %d / %d' % (image, pictures_taken, pictures_to_take))
            self.bench.set_cur_items(pictures_taken)
            self.log('Captured: %s' % (image,))
            self.log('%s' % (str(self.bench)))

        self.pb.setValue(pictures_taken)

    def dry(self):
        return self.dry_cb.isChecked()

    def pause(self):
        if self.pause_pb.text() == 'Pause':
            self.pause_pb.setText('Run')
            self.cnc_thread.setRunning(False)
            if self.pt:
                self.pt.setRunning(False)
            self.log('Pause requested')
        else:
            self.pause_pb.setText('Pause')
            self.cnc_thread.setRunning(True)
            if self.pt:
                self.pt.setRunning(True)
            self.log('Resume requested')

    def write_scan_json(self):
        scan_json = {
	        "overlap": 0.7,
	        "border": 0.1,
	        "start":{
		        "x": None,
		        "y": None
	        },
	        "end":{
		        "x": None,
		        "y": None
	        }
        }

        try:
            scan_json['overlap'] = float(self.overlap_le.text())
            scan_json['border'] = float(self.border_le.text())

            scan_json['start']['x'] = float(self.start_pos_x_le.text())
            scan_json['start']['y'] = float(self.start_pos_y_le.text())
            scan_json['end']['x'] = float(self.end_pos_x_le.text())
            scan_json['end']['y'] = float(self.end_pos_y_le.text())
        except ValueError:
            self.log("Bad position")
            return False
        json.dump(scan_json, open('scan.json', 'w'), indent=4, sort_keys=True)
        return True

    def run(self):
        if not self.snapshot_pb.isEnabled():
            self.log("Wait for snapshot to complete before CNC'ing")
            return

        dry = self.dry()
        if dry:
            dbg('Dry run checked')

        if not self.write_scan_json():
            return

        imager = None
        if not dry:
            self.log('Loading imager...')
            itype = self.uconfig['imager']['engine']

            if itype == 'auto':
                if os.path.exists('/dev/video0'):
                    itype = 'gstreamer'
                else:
                    itype = 'gstreamer-testsrc'

            if itype == 'mock':
                imager = MockImager()
            elif itype == 'gstreamer' or itype == 'gstreamer-testsrc':
                imager = GstImager(self)
            else:
                raise Exception('Invalid imager type %s' % itype)

        def emitCncProgress(pictures_to_take, pictures_taken, image, first):
            #print 'Emitting CNC progress'
            if image is None:
                image = ''
            self.cncProgress.emit(pictures_to_take, pictures_taken, image, first)

        if not dry and not os.path.exists(self.uconfig['out_dir']):
            os.mkdir(self.uconfig['out_dir'])

        out_dir = os.path.join(self.uconfig['out_dir'], str(self.job_name_le.text()))
        if os.path.exists(out_dir):
            self.log("job name dir %s already exists" % out_dir)
            return
        if not dry:
            os.mkdir(out_dir)

        rconfig = {
                'cnc_hal': self.cnc_thread.hal,

                # Will be offloaded to its own thread
                # Operations must be blocking
                # We enforce that nothing is running and disable all CNC GUI controls
                'imager': imager,

                # Callback for progress
                'progress_cb': emitCncProgress,

                'out_dir': out_dir,

                # Comprehensive config structure
                'uscope': self.uconfig,
                # Which objective to use in above config
                'obj': self.obj_configi,

                # Set to true if should try to mimimize hardware actions
                'dry': dry,
                'overwrite': False,
                }

        # If user had started some movement before hitting run wait until its done
        dbg("Waiting for previous movement (if any) to cease")
        # TODO: make this not block GUI
        self.cnc_thread.wait_idle()

        self.pt = PlannerThread(self, rconfig)
        self.connect(self.pt, SIGNAL('log'), self.log)
        self.pt.plannerDone.connect(self.plannerDone)
        self.setControlsEnabled(False)
        if dry:
            self.log_fd = StringIO()
        else:
            self.log_fd = open(os.path.join(out_dir, 'log.txt'), 'w')

        self.pt.start()

    def setControlsEnabled(self, yes):
        self.go_pb.setEnabled(yes)
        self.mv_abs_pb.setEnabled(yes)
        self.mv_rel_pb.setEnabled(yes)
        self.snapshot_pb.setEnabled(yes)

    def plannerDone(self):
        self.log('RX planner done')
        # Cleanup camera objects
        self.log_fd = None
        self.pt = None
        self.cnc_thread.hal.dry = False
        self.setControlsEnabled(True)
        if self.uconfig['cnc']['startup_run_exit']:
            print 'Planner debug break on completion'
            os._exit(1)
        # Prevent accidental start after done
        self.dry_cb.setChecked(True)

    def stop(self):
        '''Stop operations after the next operation'''
        self.cnc_thread.stop()
        # just in case
        if self.jog_run:
            self.jog_run.clear()

    def estop(self):
        '''Stop operations immediately.  Position state may become corrupted'''
        self.cnc_thread.estop()

    def clear_estop(self):
        '''Stop operations immediately.  Position state may become corrupted'''
        self.cnc_thread.unestop()

    def set_start_pos(self):
        '''
        try:
            lex = float(self.start_pos_x_le.text())
        except ValueError:
            self.log('WARNING: bad X value')

        try:
            ley = float(self.start_pos_y_le.text())
        except ValueError:
            self.log('WARNING: bad Y value')
        '''
        # take as upper left corner of view area
        # this is the current XY position
        pos = self.cnc_thread.pos()
        #self.log("Updating start pos w/ %s" % (str(pos)))
        self.start_pos_x_le.setText('%0.3f' % pos['x'])
        self.start_pos_y_le.setText('%0.3f' % pos['y'])

    def set_end_pos(self):
        # take as lower right corner of view area
        # this is the current XY position + view size
        pos = self.cnc_thread.pos()
        #self.log("Updating end pos from %s" % (str(pos)))
        x_view = self.obj_config["x_view"]
        y_view = 1.0 * x_view * self.uconfig['imager']['height'] / self.uconfig['imager']['width']
        x1 = pos['x'] + x_view
        y1 = pos['y'] + y_view
        self.end_pos_x_le.setText('%0.3f' % x1)
        self.end_pos_y_le.setText('%0.3f' % y1)

    def get_axes_layout(self):
        layout = QHBoxLayout()
        gb = QGroupBox('Axes')

        def get_general_layout():
            layout = QVBoxLayout()

            def get_go():
                layout = QHBoxLayout()

                self.ret0_pb = QPushButton("Ret0 all")
                self.ret0_pb.clicked.connect(self.ret0)
                layout.addWidget(self.ret0_pb)

                self.mv_abs_pb = QPushButton("Go abs all")
                self.mv_abs_pb.clicked.connect(self.mv_abs)
                layout.addWidget(self.mv_abs_pb)

                self.mv_rel_pb = QPushButton("Go rel all")
                self.mv_rel_pb.clicked.connect(self.mv_rel)
                layout.addWidget(self.mv_rel_pb)

                return layout

            def get_stop():
                layout = QHBoxLayout()

                self.stop_pb = QPushButton("Stop")
                self.stop_pb.clicked.connect(self.stop)
                layout.addWidget(self.stop_pb)

                self.estop_pb = QPushButton("Emergency stop")
                self.estop_pb.clicked.connect(self.estop)
                layout.addWidget(self.estop_pb)

                self.clear_estop_pb = QPushButton("Clear e-stop")
                self.clear_estop_pb.clicked.connect(self.clear_estop)
                layout.addWidget(self.clear_estop_pb)

                return layout

            def get_pos_start():
                layout = QHBoxLayout()

                layout.addWidget(QLabel("Start X0 Y0"))
                self.start_pos_x_le = QLineEdit('0.0')
                layout.addWidget(self.start_pos_x_le)
                self.start_pos_y_le = QLineEdit('0.0')
                layout.addWidget(self.start_pos_y_le)
                self.start_pos_pb = QPushButton("Set")
                self.start_pos_pb.clicked.connect(self.set_start_pos)
                layout.addWidget(self.start_pos_pb)

                return layout

            def get_pos_end():
                layout = QHBoxLayout()

                layout.addWidget(QLabel("End X0 Y0"))
                self.end_pos_x_le = QLineEdit('0.0')
                layout.addWidget(self.end_pos_x_le)
                self.end_pos_y_le = QLineEdit('0.0')
                layout.addWidget(self.end_pos_y_le)
                self.end_pos_pb = QPushButton("Set")
                self.end_pos_pb.clicked.connect(self.set_end_pos)
                layout.addWidget(self.end_pos_pb)

                return layout

            def get_pos_misc():
                layout = QGridLayout()

                layout.addWidget(QLabel('Overlap'), 0, 0)
                self.overlap_le = QLineEdit('0.7')
                layout.addWidget(self.overlap_le, 0, 1)

                layout.addWidget(QLabel('Border'), 1, 0)
                self.border_le = QLineEdit('0.1')
                layout.addWidget(self.border_le, 1, 1)

                return layout

            layout.addLayout(get_go())
            layout.addLayout(get_stop())
            layout.addLayout(get_pos_start())
            layout.addLayout(get_pos_end())
            layout.addLayout(get_pos_misc())
            return layout

        layout.addLayout(get_general_layout())

        self.axes = {}
        dbg('Axes: %u' % len(self.cnc_thread.hal.axes()))
        for axis in sorted(self.cnc_thread.hal.axes()):
            axisw = AxisWidget(axis, self.cnc_thread)
            self.axes[axis] = axisw
            layout.addWidget(axisw)

        gb.setLayout(layout)
        return gb

    def get_snapshot_layout(self):
        gb = QGroupBox('Snapshot')
        layout = QGridLayout()

        snapshot_dir = self.uconfig['imager']['snapshot_dir']
        if not os.path.isdir(snapshot_dir):
            self.log('Snapshot dir %s does not exist' % snapshot_dir)
            if os.path.exists(snapshot_dir):
                raise Exception("Snapshot directory is not accessible")
            os.mkdir(snapshot_dir)
            self.log('Snapshot dir %s created' % snapshot_dir)

        # nah...just have it in the config
        # d = QFileDialog.getExistingDirectory(self, 'Select snapshot directory', snapshot_dir)

        self.snapshot_serial = -1
        layout.addWidget(QLabel('File name'), 0, 0)
        self.snapshot_fn_le = QLineEdit('')
        self.snapshot_suffix_le = QLineEdit('.jpg')
        hl = QHBoxLayout()
        hl.addWidget(self.snapshot_fn_le)
        hl.addWidget(self.snapshot_suffix_le)
        layout.addLayout(hl, 0, 1)

        layout.addWidget(QLabel('Auto-number?'), 1, 0)
        self.auto_number_cb = QCheckBox()
        self.auto_number_cb.setChecked(True)
        layout.addWidget(self.auto_number_cb, 1, 1)

        self.snapshot_pb = QPushButton("Snapshot")
        self.snapshot_pb.clicked.connect(self.take_snapshot)

        self.time_lapse_timer = None
        self.time_lapse_pb = QPushButton("Time lapse")
        self.time_lapse_pb.clicked.connect(self.time_lapse)
        layout.addWidget(self.time_lapse_pb, 2, 1)
        layout.addWidget(self.snapshot_pb, 2, 0)

        gb.setLayout(layout)
        print 'snap serial'
        self.snapshot_next_serial()
        return gb

    def snapshot_next_serial(self):
        if not self.auto_number_cb.isChecked():
            print 'snap serial not checked'
            return
        prefix = self.snapshot_fn_le.text().split('.')[0]
        if prefix == '':
            print 'no base'
            self.snapshot_serial = 0
            prefix = 'snapshot_'
        else:
            dbg('Image prefix: %s' % prefix)
            m = re.search('([a-zA-z0-9_\-]*_)([0-9]+)', prefix)
            if m:
                dbg('snapshot Group 1: ' + m.group(1))
                dbg('snapshot Group 2: ' + m.group(2))
                prefix = m.group(1)
                self.snapshot_serial = int(m.group(2))

        while True:
            self.snapshot_serial += 1
            fn_base = '%s%03u' % (prefix, self.snapshot_serial)
            fn_full = os.path.join(self.uconfig['imager']['snapshot_dir'], fn_base + str(self.snapshot_suffix_le.text()))
            #print 'check %s' % fn_full
            if os.path.exists(fn_full):
                #dbg('Snapshot %s already exists, skipping' % fn_full)
                continue
            # Omit base to make GUI easier to read
            self.snapshot_fn_le.setText(fn_base)
            break

    def take_snapshot(self):
        self.log('Requesting snapshot')
        # Disable until snapshot is completed
        self.snapshot_pb.setEnabled(False)
        def emitSnapshotCaptured(image_id):
            self.log('Image captured: %s' % image_id)
            self.snapshotCaptured.emit(image_id)
        self.capture_sink.request_image(emitSnapshotCaptured)

    def time_lapse(self):
        if self.time_lapse_pb.text() == 'Stop':
            self.time_lapse_timer.stop()
            self.time_lapse_pb.setText('Time lapse')
        else:
            self.time_lapse_pb.setText('Stop')
            self.time_lapse_timer = QTimer()
            def f():
                self.take_snapshot()
            self.time_lapse_timer.timeout.connect(f)
            # 5 seconds
            # Rather be more aggressive for now
            self.time_lapse_timer.start(5000)
            self.take_snapshot()

    def captureSnapshot(self, image_id):
        self.log('RX image for saving')
        def try_save():
            image = self.capture_sink.pop_image(image_id)
            txt = str(self.snapshot_fn_le.text()) + str(self.snapshot_suffix_le.text())

            fn_full = os.path.join(self.uconfig['imager']['snapshot_dir'], txt)
            if os.path.exists(fn_full):
                self.log('WARNING: refusing to overwrite %s' % fn_full)
                return
            factor = float(self.uconfig['imager']['scalar'])
            # Use a reasonably high quality filter
            try:
                get_scaled(image, factor, Image.ANTIALIAS).save(fn_full)
            # FIXME: refine
            except Exception:
                self.log('WARNING: failed to save %s' % fn_full)
        try_save()

        # That image is done, get read for the next
        self.snapshot_next_serial()
        self.snapshot_pb.setEnabled(True)

    def get_scan_layout(self):
        gb = QGroupBox('Scan')
        layout = QGridLayout()

        # TODO: add overlap widgets

        layout.addWidget(QLabel('Job name'), 0, 0)
        self.job_name_le = QLineEdit('default')
        layout.addWidget(self.job_name_le, 0, 1)
        self.go_pb = QPushButton("Go")
        self.go_pb.clicked.connect(self.run)
        layout.addWidget(self.go_pb, 1, 0)
        self.pb = QProgressBar()
        layout.addWidget(self.pb, 1, 1)
        layout.addWidget(QLabel('Dry?'), 2, 0)
        self.dry_cb = QCheckBox()
        self.dry_cb.setChecked(self.uconfig['cnc']['dry'])
        layout.addWidget(self.dry_cb, 2, 1)

        self.pause_pb = QPushButton("Pause")
        self.pause_pb.clicked.connect(self.pause)
        layout.addWidget(self.pause_pb, 3, 0)

        gb.setLayout(layout)
        return gb

    def get_bottom_layout(self):
        layout = QHBoxLayout()
        layout.addWidget(self.get_axes_layout())
        def get_lr_layout():
            layout = QVBoxLayout()
            layout.addWidget(self.get_snapshot_layout())
            layout.addWidget(self.get_scan_layout())
            return layout
        layout.addLayout(get_lr_layout())
        return layout

    def initUI(self):
        self.setGeometry(300, 300, 250, 150)
        self.setWindowTitle('pr0ncnc')

        # top layout
        layout = QVBoxLayout()

        layout.addLayout(self.get_config_layout())
        layout.addLayout(self.get_video_layout())
        layout.addLayout(self.get_bottom_layout())
        self.log_widget.setReadOnly(True)
        layout.addWidget(self.log_widget)

        w = QWidget()
        w.setLayout(layout)
        self.setCentralWidget(w)
        self.show()

    def keyPressEvent(self, event):
        '''
        Upper left hand coordinate system
        '''
        # Only control explicitly, don't move by typing accident in other element
        if not self.video_container.hasFocus():
            return
        k = event.key()
        # Ignore duplicates, want only real presses
        if event.isAutoRepeat():
            return

        # Focus is sensitive...should step slower?
        # worry sonce focus gets re-integrated

        axis = {
                # Upper left origin
                Qt.Key_Left:    ('x', -1),
                Qt.Key_Right:   ('x', 1),
                Qt.Key_Up:      ('y', -1),
                Qt.Key_Down:    ('y', 1),
                Qt.Key_PageUp:  ('z', 1),
                Qt.Key_PageDown:('z', -1),
                } .get(k, None)
        if axis:
            axis, sign = axis
            dbg('Key jogging %s%c' % (axis, {1: '+', -1: '-'}[sign]))

            # Cancel running jog (if any)
            if self.jog_run:
                self.jog_run.clear()
                self.jog_run = None
            # Add the new axis to jog
            self.jog_running[axis] = sign
            # Get jog running with new axis
            self.jog_run = threading.Event()
            self.jog_run.set()
            # Note: jog_running may be updated with additional axes
            # Thread must be careful not to iterate over it directly
            # and should be aware that it may change
            self.cnc_thread.cmd('forever', self.jog_running, self.jog_run, lambda pos: self.emit_pos(pos))
            return

        if k == Qt.Key_Escape:
            self.stop()

    def keyReleaseEvent(self, event):
        # Don't move around with moving around text boxes, etc
        if not self.video_container.hasFocus():
            return
        k = event.key()
        # Ignore duplicates, want only real presses
        if event.isAutoRepeat():
            return

        axis = {
                Qt.Key_Left:    'x',
                Qt.Key_Right:   'x',
                Qt.Key_Up:      'y',
                Qt.Key_Down:    'y',
                Qt.Key_PageDown: 'z',
                Qt.Key_PageUp:  'z',
                }.get(k, None)
        if axis:
            # No longer jogging this axis
            # Note: if the user pressed both directions could already be cleared
            # (bad user: stop mashing the keyboard!)
            if axis in self.jog_running:
                del self.jog_running[axis]
                if len(self.jog_running) == 0:
                    # Cancel running jog
                    self.jog_run.clear()
                    self.jog_run = None

def excepthook(excType, excValue, tracebackobj):
    print '%s: %s' % (excType, excValue)
    traceback.print_tb(tracebackobj)
    os._exit(1)

if __name__ == '__main__':
    '''
    We are controlling a robot
    '''
    sys.excepthook = excepthook
    # Exit on ^C instead of ignoring
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    gobject.threads_init()

    app = QApplication(sys.argv)
    gui = CNCGUI()
    # XXX: what about the gstreamer message bus?
    # Is it simply not running?
    # must be what pygst is doing
    try:
        sys.exit(app.exec_())
    finally:
        gui.shutdown()

