#!/usr/bin/env python3

from uscope.gstwidget import GstVideoPipeline, gstwidget_main
from uscope.touptek_util import TTControlScroll

from uscope.config import get_config
from uscope.hal.img.imager import Imager
from uscope.img_util import get_scaled
from uscope.benchmark import Benchmark
from uscope.hal.img.imager import MockImager
from uscope.hal.cnc import hal as cnc_hal
from uscope.hal.cnc import lcnc_ar
from uscope.hal.cnc import lcnc as lcnc_hal
from uscope.lcnc.client import LCNCRPC
from uscope import gst_util
from uscope.gst_util import Gst, CaptureSink
from uscope.v4l2_util import ctrl_set

from main_gui.threads import CncThread, PlannerThread
from io import StringIO

from PyQt4 import Qt
from PyQt4.QtGui import *
from PyQt4.QtCore import *

import datetime
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

"""
gobject = None
gst = None
try:
    import gobject
    import gst
    gst_util.register()
except ImportError:
    if uconfig['imager']['engine'] == 'gstreamer' or uconfig['imager'][
            'engine'] == 'gstreamer-testrc':
        print(
            'Failed to import a gstreamer package when gstreamer is required')
        raise
"""

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


def get_cnc_hal(log):
    print('get_cnc_hal', log)
    try:
        lcnc_host = uconfig["cnc"]["lcnc"]["host"]
    except KeyError:
        lcnc_host = "mk"
    engine = uconfig['cnc']['engine']
    if engine == 'mock':
        return cnc_hal.MockHal(log=log)
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


"""
Placeholder class
These are disabled right now and movement must be done from X GUI
"""
class LCNCMovement:
    pass

class PropertiesWindow(QMainWindow):
    def __init__(self, vidpip, parent=None):
        super(PropertiesWindow, self).__init__(parent)
        layout = QHBoxLayout()

        self.default_pb = QPushButton("Default")
        layout.addWidget(self.default_pb)

        # Need to hide this when not needed
        self.control_scroll = TTControlScroll(vidpip)
        layout.addWidget(self.control_scroll)

        w = QWidget()
        w.setLayout(layout)
        self.setCentralWidget(w)
        self.show()

        self.default_pb.clicked.connect(self.control_scroll.defaultControls)

        dbg("initUI done")

class MainWindow(QMainWindow):
    cncProgress = pyqtSignal(int, int, str, int)
    snapshotCaptured = pyqtSignal(int)

    def __init__(self, source=None):
        QMainWindow.__init__(self)
        self.showMaximized()

        # FIXME: pull from config file etc
        if source is None:
            pass
        self.vidpip = GstVideoPipeline(source=source, full=True, roi=True)
        # FIXME: review sizing
        self.vidpip.size_widgets(frac=0.5)
        # self.capture_sink = Gst.ElementFactory.make("capturesink")

        self.jpegenc = Gst.ElementFactory.make("jpegenc")
        self.vidpip.player.add(self.jpegenc)

        self.capture_sink = CaptureSink()
        assert self.capture_sink
        self.vidpip.player.add(self.capture_sink)
        self.vidpip.setupGst(raw_tees=[self.jpegenc])
        self.jpegenc.link(self.capture_sink)

        self.uconfig = uconfig

        # must be created early to accept early logging
        # not displayed until later though
        self.log_widget = QTextEdit()
        # Special case for logging that might occur out of thread
        self.connect(self, SIGNAL('log'), self.log)
        self.connect(self, SIGNAL('pos'), self.update_pos)
        self.snapshotCaptured.connect(self.captureSnapshot)

        self.pt = None
        self.log_fd = None
        hal = get_cnc_hal(log=self.emit_log)
        hal.progress = self.hal_progress
        self.cnc_thread = CncThread(hal=hal, cmd_done=self.cmd_done)
        self.connect(self.cnc_thread, SIGNAL('log'), self.log)
        self.initUI()

        self.propwin = None
        if 0:
            self.propwin = PropertiesWindow(self.vidpip, parent=self)
            self.activateWindow()

        self.vid_fd = None

        # Must not be initialized until after layout is set
        self.gstWindowId = None
        engine_config = self.uconfig['imager']['engine']

        self.cnc_thread.start()

        # Offload callback to GUI thread so it can do GUI ops
        self.cncProgress.connect(self.processCncProgress)

        self.vidpip.run()
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
        s = str(s)
        print("LOG: %s" % s)
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
        for axis, axis_pos in pos.items():
            self.axis_pos_label[axis].pos_value.setText('%0.3f' % axis_pos)

    def hal_progress(self, pos):
        self.emit(SIGNAL('pos'), pos)

    def emit_pos(self, pos):
        self.emit(SIGNAL('pos'), pos)

    def cmd_done(self, cmd, args, ret):
        print("FIXME: poll position instead of manually querying")

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
        for k, v in self.v4ls.items():
            try:
                val = int(str(v.text()))
            except ValueError:
                continue
            if k == 'E':
                val = min(val, 800)
            else:
                val = min(val, 1023)
            ctrl_set(self.vid_fd, k, val)

    def add_v4l_controls(self, cl, row):
        self.v4ls = {}
        # hacked driver to directly drive values
        for ki, (label, v4l_name) in enumerate(
            (("Red", "Red Balance"), ("Green", "Gain"),
             ("Blue", "Blue Balance"), ("Exp", "Exposure"))):
            cols = 4
            rowoff = ki / cols
            coloff = cols * (ki % cols)

            cl.addWidget(QLabel(label), row + rowoff, coloff)
            le = QLineEdit('')
            self.v4ls[v4l_name] = le
            cl.addWidget(le, row + rowoff, coloff + 1)
            le.textChanged.connect(self.v4l_updated)
            row += 2

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

        # FIXME: integrate gst controls instead
        # row = self.add_v4l_controls(cl, row)

        return cl

    def get_video_layout(self):
        # Overview
        def low_res_layout():
            layout = QVBoxLayout()
            layout.addWidget(QLabel("Overview"))
            layout.addWidget(self.vidpip.full_widget)

            return layout

        # Higher res in the center for focusing
        def high_res_layout():
            layout = QVBoxLayout()
            layout.addWidget(QLabel("Focus"))
            layout.addWidget(self.vidpip.roi_widget)

            return layout

        layout = QHBoxLayout()
        layout.addLayout(low_res_layout())
        layout.addLayout(high_res_layout())
        return layout

    def setupGst(self):
        pass

    def init_v4l_ctrl(self):
        """
        Was being called on
        self.source.get_property("device-fd")
        v4l is lower priority right now. Revisit later
        """
        print('Initializing V4L controls')
        vconfig = uconfig["imager"].get("v4l2", None)
        if vconfig:
            for configk, configv in vconfig.items():
                break
            print('Selected config %s' % configk)

            for k, v in configv.items():
                #ctrl_set(self.vid_fd, k, v)
                if k in self.v4ls:
                    self.v4ls[k].setText(str(v))

    def ret0(self):
        return
        pos = dict([(k, 0.0) for k in self.axis_pos_label])
        self.cnc_thread.cmd('mv_abs', pos)

    def home(self):
        return
        self.cnc_thread.cmd('home', [k for k in self.axis_pos_label])

    def mv_rel(self):
        return
        pos = dict([(k, float(str(axis.rel_pos_le.text())))
                    for k, axis in self.axis_pos_label.items()])
        self.cnc_thread.cmd('mv_rel', pos)

    def mv_abs(self):
        return
        pos = dict([(k, float(str(axis.abs_pos_le.text())))
                    for k, axis in self.axis_pos_label.items()])
        self.cnc_thread.cmd('mv_abs', pos)

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
        if self.stop_pb.text() == 'Pause':
            self.stop_pb.setText('Run')
            self.cnc_thread.setRunning(False)
            if self.pt:
                self.pt.setRunning(False)
            self.log('Pause requested')
        else:
            self.stop_pb.setText('Pause')
            self.cnc_thread.setRunning(True)
            if self.pt:
                self.pt.setRunning(True)
            self.log('Resume requested')

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
            self.cncProgress.emit(pictures_to_take, pictures_taken, image,
                                  first)

        if not dry and not os.path.exists(self.uconfig['out_dir']):
            os.mkdir(self.uconfig['out_dir'])

        out_dir = os.path.join(self.uconfig['out_dir'],
                               str(self.job_name_le.text()))
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
        imagerj["microscope.json"] = uconfig

        # not sure if this is the right place to add this
        # imagerj['copyright'] = "&copy; %s John McMaster, CC-BY" % datetime.datetime.today().year
        imagerj['objective'] = rconfig['obj']

        # TODO: instead dump from actual v4l
        # safer and more comprehensive
        v4lj = {}
        for k, v in self.v4ls.iteritems():
            v4lj[k] = int(str(v.text()))
        imagerj["v4l"] = v4lj

        self.pt = PlannerThread(self, rconfig, imagerj)
        self.connect(self.pt, SIGNAL('log'), self.log)
        self.pt.plannerDone.connect(self.plannerDone)
        self.setControlsEnabled(False)
        if dry:
            self.log_fd = StringIO()
        else:
            self.log_fd = open(os.path.join(out_dir, 'log.txt'), 'w')

        self.pt.start()

    def setControlsEnabled(self, yes):
        self.go_pause_pb.setEnabled(yes)
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
            print('Planner debug break on completion')
            os._exit(1)
        # Prevent accidental start after done
        self.dry_cb.setChecked(True)

    def stop(self):
        '''Stop operations after the next operation'''
        self.cnc_thread.stop()

    def estop(self):
        '''Stop operations immediately.  Position state may become corrupted'''
        self.cnc_thread.estop()

    def clear_estop(self):
        '''Stop operations immediately.  Position state may become corrupted'''
        self.cnc_thread.unestop()

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
        pos = self.cnc_thread.pos()
        #self.log("Updating start pos w/ %s" % (str(pos)))
        self.plan_x0_le.setText('%0.3f' % pos['x'])
        self.plan_y0_le.setText('%0.3f' % pos['y'])

    def set_end_pos(self):
        # take as lower right corner of view area
        # this is the current XY position + view size
        pos = self.cnc_thread.pos()
        #self.log("Updating end pos from %s" % (str(pos)))
        x_view = self.obj_config["x_view"]
        y_view = 1.0 * x_view * self.uconfig['imager'][
            'height'] / self.uconfig['imager']['width']
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
        
        gb = QGroupBox('Axes')
        gb.setLayout(gl)
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

        self.snapshot_pb = QPushButton("Snap")
        self.snapshot_pb.clicked.connect(self.take_snapshot)
        layout.addWidget(self.snapshot_pb, 0, 0)

        self.snapshot_fn_le = QLineEdit('snapshot')
        self.snapshot_suffix_le = QLineEdit('.jpg')
        # XXX: since we already have jpegenc this is questionable
        self.snapshot_suffix_le.setEnabled(False)
        self.snapshot_suffix_le.setSizePolicy(QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
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
            prefix = datetime.datetime.utcnow().isoformat().replace('T', '_').replace(':', '-').split('.')[0] + "_"

        extension = str(self.snapshot_suffix_le.text())

        mod = None
        while True:
            mod_str = ''
            if mod:
                mod_str = '_%u' % mod
            fn_full = os.path.join(self.uconfig['imager']['snapshot_dir'], prefix + user + mod_str + extension)
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
            factor = float(self.uconfig['imager']['scalar'])
            # Use a reasonably high quality filter
            try:
                get_scaled(image, factor, Image.ANTIALIAS).save(fn_full)
            # FIXME: refine
            except Exception:
                self.log('WARNING: failed to save %s' % fn_full)
        try_save()

        self.snapshot_pb.setEnabled(True)

    def get_scan_layout(self):
        """
        Line up Go/Stop w/ "Job name" to make visually appealing
        """

        gb = QGroupBox('Scan')
        layout = QHBoxLayout()

        def leftLayout():
            layout = QGridLayout()

            layout.addWidget(QLabel('Job name'), 0, 0, 1, 2)

            self.go_pause_pb = QPushButton("Go")
            self.go_pause_pb.clicked.connect(self.run)
            layout.addWidget(self.go_pause_pb, 1, 0)

            self.stop_pb = QPushButton("Stop")
            self.stop_pb.clicked.connect(self.stop)
            layout.addWidget(self.stop_pb, 1, 1)

            return layout

        def rightLayout():
            layout = QVBoxLayout()

            def topLayout():
                layout = QHBoxLayout()

                self.job_name_le = QLineEdit('default')
                layout.addWidget(self.job_name_le)

                layout.addWidget(QLabel('Dry?'))
                self.dry_cb = QCheckBox()
                self.dry_cb.setChecked(self.uconfig['cnc']['dry'])
                layout.addWidget(self.dry_cb)

                return layout

            layout.addLayout(topLayout())
            self.pb = QProgressBar()
            layout.addWidget(self.pb)

            return layout


        layout.addLayout(leftLayout())
        layout.addLayout(rightLayout())
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
        self.setWindowTitle('pr0ncnc')

        def rightLayout():
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

        layout = QHBoxLayout()

        # Need to hide this when not needed
        if 0:
            self.control_scroll = TTControlScroll(self.vidpip)
            layout.addWidget(self.control_scroll)

        layout.addLayout(rightLayout())

        w = QWidget()
        w.setLayout(layout)
        self.setCentralWidget(w)
        self.show()
        dbg("initUI done")

    def keyPressEvent(self, event):
        k = event.key()
        if k == Qt.Key_Escape:
            self.stop()


if __name__ == '__main__':
    gstwidget_main(MainWindow)
