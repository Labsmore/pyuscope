from uscope import config
from uscope.gui.widgets import AWidget
from uscope.imager.gst_recorder import RecordError

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import os
import time


class VideoRecordWidget(AWidget):
    """
    Record / stop button for saving the live feed to a video file
    """
    # filename, error (None on success)
    recordDone = pyqtSignal(str, object)
    # True when a recording starts, False once it is finalized
    recordingChanged = pyqtSignal(bool)

    def __init__(self, ac, filename_base, aname=None, parent=None):
        """
        filename_base: callable(extension) returning the output file name
        """
        super().__init__(ac=ac, aname=aname, parent=parent)
        self.filename_base = filename_base
        self.argus_config = self.ac.usc.app("argus")
        self.recorder = None
        self.record_start = None
        self.recordDone.connect(self.record_done)
        self.elapsed_timer = QTimer()
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self.update_record_pb)

    def _initUI(self):
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.record_pb = QPushButton()
        self.record_pb.clicked.connect(self.record_clicked)
        layout.addWidget(self.record_pb)

        self.audio_cb = QCheckBox("Audio")
        self.audio_cb.setChecked(self.argus_config.video_record_audio())
        self.audio_cb.setSizePolicy(
            QSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum))
        layout.addWidget(self.audio_cb)

        self.setLayout(layout)
        self.update_record_pb()

    def _post_ui_init(self):
        # ex: picamera2 has no gstreamer pipeline
        if not hasattr(self.ac.vidpip, "video_recorder"):
            self.record_pb.setEnabled(False)
            self.audio_cb.setEnabled(False)
            self.record_pb.setToolTip("Not supported by this camera")
            return
        self.recorder = self.ac.vidpip.video_recorder()

    def is_recording(self):
        return self.record_start is not None

    def update_record_pb(self):
        if self.record_start is None:
            self.record_pb.setText("Record video")
            self.record_pb.setIcon(QIcon(config.GUI.icon_files["go"]))
        else:
            elapsed = int(time.time() - self.record_start)
            self.record_pb.setText("Stop recording (%u:%02u)" %
                                   (elapsed // 60, elapsed % 60))
            self.record_pb.setIcon(QIcon(config.GUI.icon_files["stop"]))

    def record_clicked(self):
        if self.is_recording():
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if self.recorder is None or self.recorder.is_recording():
            return
        try:
            self.recorder.start(
                self.filename_base,
                container=self.argus_config.video_record_container(),
                audio=self.audio_cb.isChecked(),
                audio_source=self.argus_config.video_record_audio_source(),
                max_wh=self.argus_config.video_record_max_wh(),
                done=lambda fn, err: self.recordDone.emit(fn, err))
        except RecordError as e:
            self.ac.log(f"Recording: failed to start: {e}")
            return
        self.record_start = time.time()
        self.audio_cb.setEnabled(False)
        self.update_record_pb()
        self.elapsed_timer.start()
        self.recordingChanged.emit(True)

    def stop_recording(self):
        if self.recorder is None or not self.recorder.is_recording():
            return
        # Wait for the file to be finalized before allowing a new recording
        self.record_pb.setEnabled(False)
        self.record_pb.setText("Saving...")
        self.elapsed_timer.stop()
        self.recorder.stop()

    def record_done(self, filename, error):
        # Recorder already logged the result
        self.elapsed_timer.stop()
        self.record_start = None
        self.record_pb.setEnabled(True)
        self.audio_cb.setEnabled(True)
        self.update_record_pb()
        self.recordingChanged.emit(False)
        if error and os.path.exists(filename):
            self.ac.log(f"Recording: {filename} may be incomplete")

    def _shutdown_request(self, phase):
        # Unfinalized mp4 files are unreadable
        if self.recorder and self.recorder.is_recording():
            self.recorder.stop(wait=True)
