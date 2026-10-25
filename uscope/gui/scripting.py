"""
Argus front end for the scripting engine (see uscope/scripting.py)
"""

from uscope.gui.widgets import ArgusTab, ImageViewerDialog
from uscope.gui.input_widget import InputWidget
from uscope.scripting import ScriptHost, ScriptRunner
from uscope.util import readj, writej
from uscope.threads import ShutdownPhase
from uscope import app_log

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
import os
import threading
import traceback


class QHLine(QFrame):
    def __init__(self):
        super(QHLine, self).__init__()
        self.setFrameShape(QFrame.HLine)
        self.setFrameShadow(QFrame.Sunken)


class QtScriptHost(QObject, ScriptHost):
    """
    Forwards script thread callbacks to the GUI thread
    Must be created on the GUI thread
    """
    log_sig = pyqtSignal(str)
    # (PIL.Image, title)
    show_image_sig = pyqtSignal(object, str)
    # (title, text, buttons, (answer list, threading.Event))
    prompt_sig = pyqtSignal(str, str, list, object)
    done_sig = pyqtSignal(object)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)

    def log(self, msg):
        self.log_sig.emit(str(msg))

    def show_image(self, im, title):
        self.show_image_sig.emit(im, title)

    def prompt(self, title, text, buttons, check_running):
        box = []
        ev = threading.Event()
        self.prompt_sig.emit(title, text, list(buttons), (box, ev))
        while not ev.wait(0.15):
            check_running()
        return box[0] if box else buttons[-1]

    def done(self, plugin):
        self.done_sig.emit(plugin)


def exec_prompt(parent, title, text, buttons, box_ev):
    """
    Slot for QtScriptHost.prompt_sig
    """
    box, ev = box_ev
    mb = QMessageBox(parent)
    mb.setWindowTitle(title)
    mb.setText(text)
    mapping = {}
    for label in buttons:
        b = mb.addButton(label, QMessageBox.AcceptRole)
        mapping[b] = label
    mb.exec_()
    box.append(mapping.get(mb.clickedButton(), buttons[-1]))
    ev.set()


class ScriptingTab(ArgusTab):
    def __init__(self, *args, instrument_config=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instrument_config = instrument_config
        # Called once from plugin_done(), ex: to remove an instrument after stopping
        self._after_done = None

        # One file per tab per launch
        if instrument_config is None:
            tag = "script"
        else:
            tag = "script_%s" % instrument_config["name"]
        fn = app_log.log_fn(self.ac.microscope.usc.bc.get_data_dir(),
                            self.ac.microscope.name,
                            tag=tag)
        self.log_all_fd = open(fn, "w+")

        self.host = QtScriptHost(parent=self)
        self.host.log_sig.connect(self.log_local)
        self.host.show_image_sig.connect(self.show_plugin_image)
        self.host.prompt_sig.connect(self.show_prompt)
        self.host.done_sig.connect(self.plugin_done)
        self.runner = ScriptRunner(session=self.ac,
                                   host=self.host,
                                   instrument_config=instrument_config)
        self.running = False

    @property
    def plugin(self):
        return self.runner.plugin

    @property
    def filename(self):
        return self.runner.filename

    def _initUI(self):
        layout = QGridLayout()
        row = 0

        self.script_dirs = {'uscope': './uscope/script'}
        for name, directory in self.ac.microscope.bc.script_dirs().items():
            self.script_dirs[name] = directory

        self.select_pbs = {}
        for name in self.script_dirs.keys():
            pb = QPushButton(f"Select script ({name})")
            self.select_pbs[name] = pb

            def connect(name):
                def select_script_clicked():
                    self.browse_for_script(name)

                pb.clicked.connect(select_script_clicked)

            connect(name)
            layout.addWidget(pb, row, 0)
            row += 1

        layout.addWidget(QHLine(), row, 0)
        row += 1

        self.fn_le = QLineEdit("No file selected")
        layout.addWidget(self.fn_le, row, 0)
        row += 1
        self.fn_le.setReadOnly(True)

        self.run_pb = QPushButton("Run")
        self.run_pb.setEnabled(False)
        self.run_pb.clicked.connect(self.run_pb_clicked)
        layout.addWidget(self.run_pb, row, 0)
        row += 1

        layout.addWidget(QHLine(), row, 0)
        row += 1

        # Less commonly used functions below

        self.reload_pb = QPushButton("Reload")
        self.reload_pb.clicked.connect(self.reload_pb_clicked)
        self.reload_pb.setEnabled(False)
        layout.addWidget(self.reload_pb, row, 0)
        row += 1

        # Could we combine these into one button?
        # Require the user to attempt a graceful stop first

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

        def load_save_layout():
            layout = QHBoxLayout()

            self.load_config_pb = QPushButton("Load config")
            self.load_config_pb.setEnabled(False)
            self.load_config_pb.clicked.connect(self.load_config_pb_clicked)
            layout.addWidget(self.load_config_pb)

            self.save_config_pb = QPushButton("Save config")
            self.save_config_pb.setEnabled(False)
            self.save_config_pb.clicked.connect(self.save_config_pb_clicked)
            layout.addWidget(self.save_config_pb)

            return layout

        layout.addLayout(load_save_layout(), row, 0)
        row += 1

        self.input = InputWidget(clicked=self.inputWidgetClicked)
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

        # Most users don't need this
        # TODO: make this a menu item
        self.enable_advanced_scripting(self.ac.microscope.usc.bc.dev_mode())

    def enable_advanced_scripting(self, enabled):
        self.reload_pb.setVisible(enabled)
        self.kill_pb.setVisible(enabled)

    def browse_for_script(self, name):
        directory = self.script_dirs[name]
        filename = QFileDialog.getOpenFileName(None, "Select script",
                                               directory, "Script (*.py)")
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
            self.fn_le.setText(filename)
            plugin = self.runner.load(filename)
            self.input.configure(plugin.input_config())

            self.status_le.setText("Status: idle")
            self.run_pb.setEnabled(True)
            self.save_config_pb.setEnabled(True)
            self.load_config_pb.setEnabled(True)
            self.run_pb.setVisible(plugin.show_run_button())
            self.reload_pb.setEnabled(True)
            self.log_local(f"Script selected: {filename}")
        except Exception as e:
            self.unload_script()
            self.log_local(f"Plugin failed to load: {e}")
            print("")
            print("Script generated unhandled exception")
            traceback.print_exc()
            return

    def unload_script(self):
        self.runner.unload()
        self.fn_le.setText("")
        self.status_le.setText("Status: idle")
        self.input.configure({})
        self.log_widget.clear()
        self.run_pb.setEnabled(False)

    def set_filename(self, filename):
        self.fn_le.setText(filename)
        if filename:
            self.select_script(filename)

    def reload_pb_clicked(self):
        self.select_script(str(self.fn_le.text()))
        if self.instrument_config:
            self.instrument_start()

    def run_pb_clicked(self, _checked=None, input_val=None):
        if self.running:
            self.log_local("Can't run while already running")
            return
        # This can happen if plugin fails to load
        if self.plugin is None:
            self.log_local("Can't run without plugin")
            return

        if input_val is None:
            input_val = self.input.getValues()
        for pb in self.select_pbs.values():
            pb.setEnabled(False)
        self.run_pb.setEnabled(False)
        self.reload_pb.setEnabled(False)
        self.stop_pb.setEnabled(True)
        self.kill_pb.setEnabled(True)
        self.save_config_pb.setEnabled(False)
        self.load_config_pb.setEnabled(False)
        self.log_local("Plugin loading")
        self.runner.run(input_val)
        self.status_le.setText("Status: running")
        self.running = True

    # An alternate way to launch using custom buttons
    def inputWidgetClicked(self, j):
        input_val = self.input.getValues()
        input_val["button"] = j
        self.run_pb_clicked(input_val=input_val)

    def stop_pb_clicked(self):
        if not self.running:
            self.log_local("Plugin isn't running")
            return
        self.runner.stop()

    def kill_pb_clicked(self):
        if not self.running or not self.plugin.ident:
            self.log_local("Plugin isn't running")
            return
        self.log_local(f"Killing thread {self.plugin.ident}")
        try:
            self.runner.kill()
        except RuntimeError as e:
            self.log_local(str(e))

    def default_config_file_name(self):
        # /home/mcmaster/script/my_script.py => my_script.json
        return os.path.basename(str(
            self.fn_le.text())).split(".")[0] + ".script.json"

    def load_config_pb_clicked(self):
        directory = self.ac.bc.script_data_dir()
        directory = os.path.join(directory, self.default_config_file_name())
        filename = QFileDialog.getOpenFileName(None,
                                               "Select input script config",
                                               directory,
                                               "Script config (*.json *.j5)")
        if not filename:
            return
        filename = str(filename[0])
        if not filename:
            return
        try:
            j = readj(filename)
            self.input.setValues(j)
        except Exception as e:
            self.log_local(f"Failed to load script config: {type(e)}: {e}")
            traceback.print_exc()

    def set_config(self, j):
        try:
            self.input.setValues(j)
        except Exception as e:
            self.log_local(f"Failed to load script config: {type(e)}: {e}")
            traceback.print_exc()

    def save_config_pb_clicked(self):
        directory = self.ac.bc.script_data_dir()
        directory = os.path.join(directory, self.default_config_file_name())
        filename = QFileDialog.getSaveFileName(None,
                                               "Select output script config",
                                               directory,
                                               "Script config (*.json *.j5)")
        if not filename:
            return
        filename = str(filename[0])

        j = self.input.getValues()
        writej(filename, j)

    def show_plugin_image(self, im, title):
        dialog = ImageViewerDialog(im, title=title, parent=self)
        dialog.show()

    def show_prompt(self, title, text, buttons, box_ev):
        exec_prompt(self, title, text, buttons, box_ev)

    def plugin_done(self, plugin):
        if plugin.succeeded():
            status = "Status: finished ok"
            try:
                self.input.setValues(plugin.new_defaults)
            except KeyError as e:
                self.log_local(f"Failed to update defaults: bad label: {e}")
            except Exception as e:
                self.log_local(f"Failed to update defaults: {type(e)}: {e}")
        else:
            status = "Status: failed :("
        self.status_le.setText(status)
        self.stop_pb.setEnabled(False)
        self.kill_pb.setEnabled(False)
        self.reload_pb.setEnabled(True)
        self.save_config_pb.setEnabled(True)
        self.load_config_pb.setEnabled(True)
        self.run_pb.setEnabled(True)
        if plugin.succeeded():
            self.log_local("Plugin completed ok")
        else:
            self.log_local("Plugin completed w/ issue")
            self.log_local(plugin.result_message)
        for pb in self.select_pbs.values():
            pb.setEnabled(True)
        self.running = False
        after_done, self._after_done = self._after_done, None
        if after_done:
            after_done()

    def _post_ui_init(self):
        # Automatically start instruments at load time
        if self.instrument_config:
            self.select_script(self.instrument_config["plugin_path"])
            self.instrument_start()

    def instrument_start(self):
        if self.plugin is None:
            self.log_local("Plugin failed to load :(")
            return
        self.run_pb_clicked(input_val={"init": True})

    def teardown(self):
        """
        Release resources when the tab is removed at runtime
        The plugin should already be stopped
        """
        self.runner.unload()
        self.log_all_fd.close()

    def _shutdown_request(self, phase):
        # Shutdown plugin before anything else
        if phase == ShutdownPhase.INITIAL:
            self.runner.stop()

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

        # Late log signals can arrive after teardown()
        if not self.log_all_fd.closed:
            self.log_all_fd.write(s)
            self.log_all_fd.flush()

    def _cache_save(self, cachej):
        if self.instrument_config is None:
            j = {}
            j["filename"] = str(self.fn_le.text())
            j["config"] = self.input.getValues()
            cachej["scripting"] = j

    def _cache_load(self, cachej):
        if self.instrument_config is None:
            j = cachej.get("scripting", {})
            self.set_filename(j.get("filename", ""))
            config = j.get("config", None)
            if config is not None:
                self.set_config(config)

    def is_running(self):
        return self.running
