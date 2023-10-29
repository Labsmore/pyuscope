from uscope.app.argus.widgets import ArgusTab
from uscope.app.argus.input_widget import InputWidget
from uscope.config import get_data_dir, get_bc
from uscope.motion import motion_util
from uscope.microscope import StopEvent, MicroscopeStop

from PyQt5 import Qt
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
import importlib.util
import sys
import imp
import os
import ctypes
import threading
import time
import traceback


class TestFailed(Exception):
    pass


class TestAborted(Exception):
    pass


class TestKilled(SystemExit):
    pass


# class ArgusScriptingPlugin(threading.Thread):
# needed to do signals
class ArgusScriptingPlugin(QThread):
    log_msg = pyqtSignal(str)
    done = pyqtSignal()

    def __init__(self, ac):
        super().__init__()
        self._ac = ac
        self._input = None
        self.se = None
        # Graceful shutdown request
        self._running = threading.Event()
        self.reset()

    def reset(self):
        self._succeeded = None
        self.result_message = None
        self.new_defaults = {}
        self._running.set()

    def log(self, s):
        """
        Log a message to the script window
        (not the main window)
        """
        self.log_msg.emit(s)

    def shutdown(self):
        """
        Request graceful termination
        """
        self._running.clear()

    def get_input(self):
        """
        Return a dictionary containing applicable fields
        """
        return self._input

    def set_input_default(self, label, value):
        """
        Allows a script to have modes that setup various parameters
        """
        self.new_defaults[label] = value

    def fail(self, message):
        """
        Thread should call this to abort a run
        Indicates the operation failed
        """
        self._succeeded = False
        self.result_message = message
        raise TestFailed(message)

    def check_running(self):
        """
        This thread should periodically check for graceful shutdown
        """
        self.se.poll()
        if not self._running.is_set():
            raise TestAborted()

    def succeeded(self):
        return bool(self._succeeded)

    def run(self):
        self.ident = threading.current_thread().ident
        try:
            with StopEvent(self._ac.microscope) as self.se:
                self.run_test()
            self._succeeded = True
        except TestAborted:
            self._succeeded = False
            self.result_message = "Aborted"
        except MicroscopeStop:
            self._succeeded = False
            self.result_message = "Aborted"
        except TestFailed:
            self._succeeded = False
            self.result_message = "Failed"
        except TestKilled:
            self._succeeded = False
            self.result_message = "killed"
        except Exception as e:
            self._succeeded = False
            self.result_message = f"Exception: {e}"
            print("")
            print("Script generated unhandled exception")
            traceback.print_exc()
        finally:
            self._running.clear()
            self.done.emit()

    """
    Main API
    """

    def run_scan(self, scanj):
        assert 0, "fixme"

    def snap_image(self, filename=None):
        assert 0, "fixme"

    def autofocus(self):
        """
        Autofocus at the current location
        """
        self._ac.image_processing_thread.auto_focus(
            objective_config=self._ac.objective_config(), block=True)

    def pos(self):
        """
        Get current stage position
        Returns a dictionary like:
        {"x": 12.345, "y": 2.356, "z": 4.5}
        """
        self.check_running()
        return self._ac.motion_thread.pos_cache

    def move_absolute(self, pos, block=True):
        """
        Set current position
        Pos can include one or more positions like:
        {"z": 4.5}
        {"x": 12.345, "y": 2.356, "z": 4.5}
        """
        self.check_running()
        self._ac.motion_thread.move_absolute(pos, block=block)
        self.check_running()

    def move_relative(self, pos, block=True):
        self.check_running()
        self._ac.motion_thread.move_relative(pos, block=block)
        self.check_running()

    def position_format(self, axes):
        """
        Convert a dictionary of axis positions to a string
        Ex: {"x" 1, "y": 2} => "X1 Y2"
        """
        return self._ac.usc.motion.format_positions(axes)

    def position_parse(self, s):
        """
        Convert a axis position string to a dictionary of positions
        Ex: "X1 Y2" => {"x" 1, "y": 2}
        """
        return motion_util.parse_move(s)

    def sleep(self, t):
        """
        Sleep for given number of seconds, watching for abort requests
        """

        delta = 0.1
        tstart = time.time()
        while True:
            dt = time.time() - tstart
            remain = t - dt
            if remain < 0:
                break
            self.check_running()
            time.sleep(min(delta, remain))
        self.check_running()

    def image(self, wait_imaging_ok=True):
        """
        Request and return a snapshot as PIL image
        """
        if wait_imaging_ok:
            self.wait_imaging_ok()
        imager = self.imager()
        return imager.get()["0"]

    def wait_imaging_ok(self):
        """
        Wait for camera / stage to settle
        After this a picture can be snapped with acceptable quality
        """

        # FIXME: this is really hacky
        # we should actually do wait_imaging_ok() w/ frame sync
        # need to document thread safety better, flush_image might be thread safe
        self._ac.microscope.kinematics.wait_imaging_ok(flush_image=False)
        # Frame sync the last image, which might be bad
        self.imager().get()

    def message_box_yes_cancel(self, title, message):
        # quick hack: run as subprocess?
        assert 0, "FIXME: not thread safe"
        ret = QMessageBox.question(None, title, message,
                                   QMessageBox.Yes | QMessageBox.Cancel,
                                   QMessageBox.Cancel)
        return ret == QMessageBox.Yes

    def get_objectives_config(self):
        """
        Returns the entire objective DB structure
        """
        return self._ac.microscope.objectives.get_full_config()

    def get_active_objective(self):
        """
        Returns the name of the active objective
        """
        return self._ac.scriptingTab.active_objective["name"]

    def set_active_objective(self, objective):
        """
        Check if name is in cache
        """
        self._ac.mainTab.objective_widget.setObjective.emit(objective)

    """
    Advanced API
    Try to use the higher level functions first if possible
    """

    def run_planner(self, pconfig):
        assert 0, "FIXME"

    def motion(self):
        """
        Get a (thread safe) motion object
        Access to the more powerful but less stable stage API
        """
        self._ac.motion_thread.get_planner_motion()

    def imager(self):
        """
        Get a (thread safe) imager object
        Access to the more powerful but less stable camera API
        """
        # Planner uses this directly / is already thread safe
        return self._ac.imager

    def kinematics(self):
        """
        Get a (thread safe) kinematics object
        Access to the more powerful but less stable system synchronization API
        """
        assert 0, "fixme"

    def backlash_disable(self, block=True):
        """
        Disable backlash compensation
        """
        self._ac.motion_thread.backlash_disable(block=block)

    def backlash_enable(self, block=True):
        """
        Enable backlash compensation
        """
        self._ac.motion_thread.backlash_enable(block=block)

    """
    Plugin defined functions
    """

    def run_test(self):
        """
        The script entry point
        The most important user function
        """
        pass

    def input_config(self):
        """
        Return a dictionary to configure InputWidget
        """
        return {}


class ScriptingTab(ArgusTab):
    def __init__(self, ac, parent=None):
        super().__init__(ac=ac, parent=parent)
        self.stitcher_thread = None
        self.last_cs_upload = None
        self.filename = None

        fn = os.path.join(get_data_dir(), "script_log.txt")
        existed = os.path.exists(fn)
        self.log_all_fd = open(fn, "w+")
        if existed:
            self.log_all_fd.write("\n\n\n")
            self.log_all_fd.flush()

        self.plugin = None
        self.running = False
        self.active_objective = None
        self.ac.objectiveChanged.connect(self.active_objective_updated)

    def initUI(self):
        layout = QGridLayout()
        row = 0

        self.select_pb_rhodium_path = get_bc().script_rhodium_dir()

        if self.select_pb_rhodium_path:
            self.select_pb1 = QPushButton("Select script (uscope)")
        else:
            self.select_pb1 = QPushButton("Select script")
        self.select_pb1.clicked.connect(self.select_pb1_clicked)
        layout.addWidget(self.select_pb1, row, 0)
        row += 1

        if self.select_pb_rhodium_path:
            self.select_pb_rhodium = QPushButton("Select script (rhodium)")
            self.select_pb_rhodium.clicked.connect(
                self.select_pb_rhodium_clicked)
            layout.addWidget(self.select_pb_rhodium, row, 0)
            row += 1

        # self.test_name_cb = QComboBox()

        self.reload_pb = QPushButton("Reload")
        self.reload_pb.clicked.connect(self.reload_pb_clicked)
        layout.addWidget(self.reload_pb, row, 0)
        row += 1

        self.run_pb = QPushButton("Run")
        self.run_pb.setEnabled(False)
        self.run_pb.clicked.connect(self.run_pb_clicked)
        layout.addWidget(self.run_pb, row, 0)
        row += 1

        self.stop_pb = QPushButton("Stop gracefully")
        self.stop_pb.setEnabled(False)
        self.stop_pb.clicked.connect(self.stop_pb_clicked)
        layout.addWidget(self.stop_pb, row, 0)
        row += 1

        self.kill_pb = QPushButton("Kill")
        self.kill_pb.setEnabled(False)
        self.kill_pb.clicked.connect(self.kill_pb_clicked)
        layout.addWidget(self.kill_pb, row, 0)
        row += 1

        self.input = InputWidget()
        layout.addWidget(self.input, row, 0)
        row += 1

        self.status_le = QLineEdit("Status: idle")
        layout.addWidget(self.status_le, row, 0)
        row += 1
        self.status_le.setReadOnly(True)

        # TODO: save button
        # Should always log to filesystem?
        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        layout.addWidget(self.log_widget, row, 0)
        row += 1

        self.setLayout(layout)

    def select_pb1_clicked(self):
        filename = QFileDialog.getOpenFileName(None, "Select script",
                                               './uscope/script',
                                               "Script (*.py)")
        if not filename:
            return
        filename = str(filename[0])
        self.select_script(filename)

    def select_pb_rhodium_clicked(self):
        filename = QFileDialog.getOpenFileName(None, "Select script",
                                               self.select_pb_rhodium_path,
                                               "Script (*.py)")
        if not filename:
            return
        filename = str(filename[0])
        self.select_script(filename)

    def select_script(self, filename):
        if not filename:
            self.log_local("No file selected")
            return
        if not os.path.exists(filename):
            self.log_local("File does not exist")
            return

        self.unload_script()
        try:
            spec = importlib.util.spec_from_file_location(
                "pyuscope_plugin", filename)
            plugin_module = importlib.util.module_from_spec(spec)
            sys.modules["pyuscope_plugin"] = plugin_module
            spec.loader.exec_module(plugin_module)
            # Entry point: construct the ArgusScriptingPlugin class named Plugin
            self.plugin = plugin_module.Plugin(ac=self.ac)

            self.input.configure(self.plugin.input_config())

            self.plugin.log_msg.connect(self.log_local)
            self.plugin.done.connect(self.plugin_done)

            self.status_le.setText("Status: idle")
            self.run_pb.setEnabled(True)

            # self.test_name_cb.clear()
            # for now just support one function
            # self.test_name_cb.addItem("run")
            # self.pconfig_sources[self.pconfig_source_cb.currentIndex()]
            self.filename = filename
            self.log_local(f"Script selected: {filename}")
        except Exception as e:
            self.unload_script()
            self.log_local(f"Plugin failed to load: {e}")
            print("")
            print("Script generated unhandled exception")
            traceback.print_exc()
            return

    def unload_script(self):
        self.plugin = None
        self.status_le.setText("Status: idle")
        self.input.configure({})
        self.log_widget.clear()

    def reload_pb_clicked(self):
        self.select_script(self.filename)

    def run_pb_clicked(self):
        if self.running:
            self.log_local("Can't run while already running")
            return

        self.plugin._input = self.input.getValue()
        self.stop_pb.setEnabled(True)
        self.kill_pb.setEnabled(True)
        self.log_local("Plugin loading")
        self.plugin.reset()
        self.plugin.start()
        # pool = QThreadPool.globalInstance()
        # pool.start(self.plugin)
        self.status_le.setText("Status: running")
        self.running = True

    def stop_pb_clicked(self):
        if not self.running:
            self.log_local("Plugin isn't running")
            return
        self.plugin.shutdown()

    def kill_pb_clicked(self):
        thread_id = self.plugin.ident
        if not self.running or not thread_id:
            self.log_local("Plugin isn't running")
            return

        self.log_local(f"Killing thread {thread_id}")
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_long(thread_id), ctypes.py_object(TestKilled))
        if res > 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_long(thread_id), 0)
            self.log_local("Exception raise failure")

    def plugin_done(self):
        if self.plugin.succeeded():
            status = "Status: finished ok"
            try:
                self.input.update_defaults(self.plugin.new_defaults)
            except KeyError as e:
                self.log_local(f"Failed to update defaults: bad label: {e}")
            except Exception as e:
                self.log_local(f"Failed to update defaults: {type(e)}: {e}")
        else:
            status = "Status: failed :("
        self.status_le.setText(status)
        self.stop_pb.setEnabled(False)
        self.kill_pb.setEnabled(False)
        if self.plugin.succeeded():
            self.log_local("Plugin completed ok")
        else:
            self.log_local("Plugin completed w/ issue")
            self.log_local(self.plugin.result_message)
        self.running = False

    def post_ui_init(self):
        pass

    def shutdown(self):
        if self.plugin:
            self.plugin.shutdown()
            self.plugin = None

    def log_local(self, s='', newline=True):
        s = str(s)
        # print("LOG: %s" % s)
        if newline:
            s += '\n'

        c = self.log_widget.textCursor()
        c.clearSelection()
        c.movePosition(QTextCursor.End)
        c.insertText(s)
        self.log_widget.setTextCursor(c)

        self.log_all_fd.write(s)
        self.log_all_fd.flush()
        """
        if self.log_plugin_fd is not None:
            self.log_plugin_fd.write(s)
            self.log_plugin_fd.flush()
        """

    def active_objective_updated(self, data):
        """
        Cache the active objective
        """
        self.active_objective = data
