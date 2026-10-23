"""
Browse snapshots saved in the Argus snapshot directory
"""

from uscope.gui.widgets import ArgusTab

from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *

import numpy as np
import os
import time

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".tif", ".tiff", ".png")
THUMB_SIZE = 160
# request_id values; previews count up from 1
THUMB_REQUEST = 0
THUMB_SKIPPED = -1
# A file this new that fails to load may still be being written
RETRY_WINDOW = 10.0


def list_images(directory):
    """
    Return [(path, mtime, size)] for images directly in directory, newest first
    """
    ret = []
    try:
        it = os.scandir(directory)
    except OSError:
        return ret
    with it:
        for entry in it:
            if not entry.name.lower().endswith(IMAGE_EXTENSIONS):
                continue
            try:
                if not entry.is_file():
                    continue
                st = entry.stat()
            except OSError:
                continue
            ret.append((entry.path, st.st_mtime, st.st_size))
    ret.sort(key=lambda x: (x[1], x[0]), reverse=True)
    return ret


def to_8bit(data):
    """
    Downconvert image data to uint8 for display

    Integer data is scaled by its apparent bit depth (max value rounded up to
    a power of two) so 10/12 bit data in a 16 bit container isn't nearly black
    """
    if data.dtype == np.uint8:
        return data
    if data.dtype == np.bool_:
        return data.astype(np.uint8) * 255
    if np.issubdtype(data.dtype, np.floating):
        data = np.nan_to_num(data.astype(np.float64))
        lo = float(data.min()) if data.size else 0.0
        hi = float(data.max()) if data.size else 0.0
        if 0.0 <= lo and hi <= 1.0:
            scaled = data * 255.0
        elif hi > lo:
            scaled = (data - lo) * (255.0 / (hi - lo))
        else:
            scaled = np.zeros_like(data)
        return np.clip(scaled + 0.5, 0, 255).astype(np.uint8)
    if np.issubdtype(data.dtype, np.signedinteger):
        data = np.clip(data, 0, None)
    maxval = int(data.max()) if data.size else 0
    bits = max(8, maxval.bit_length())
    shift = bits - 8
    return (data >> shift).astype(np.uint8)


def _load_pil(fn, max_dim=None):
    from PIL import Image
    with Image.open(fn) as im:
        if max_dim:
            # Let the JPEG decoder downscale: much faster for large thumbnails
            im.draft("RGB", (max_dim, max_dim))
        if im.mode not in ("L", "RGB", "I;16", "I", "F"):
            im = im.convert("RGB")
        return np.array(im)


def _load_cv2(fn):
    import cv2
    data = cv2.imread(fn, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
    if data is None:
        return None
    if data.ndim == 3:
        if data.shape[2] == 4:
            data = cv2.cvtColor(data, cv2.COLOR_BGRA2RGB)
        elif data.shape[2] == 3:
            data = cv2.cvtColor(data, cv2.COLOR_BGR2RGB)
        else:
            data = data[:, :, 0]
    return data


def load_rgb8(fn, max_dim=None):
    """
    Load an image file as a uint8 array: (h, w) gray or (h, w, 3) RGB
    max_dim: if given, shrink so neither dimension exceeds this
    """
    import cv2

    if max_dim and fn.lower().endswith((".jpg", ".jpeg")):
        data = _load_pil(fn, max_dim=max_dim)
    else:
        data = _load_cv2(fn)
        if data is None:
            # Fall back to PIL for anything OpenCV can't parse
            data = _load_pil(fn)
    if data.ndim == 3 and data.shape[2] == 1:
        data = data[:, :, 0]

    if max_dim:
        h, w = data.shape[0:2]
        scale = max_dim / max(h, w)
        if scale < 1.0:
            size = (max(1, round(w * scale)), max(1, round(h * scale)))
            data = cv2.resize(data, size, interpolation=cv2.INTER_AREA)
    return to_8bit(data)


def array_to_qimage(data):
    data = np.ascontiguousarray(data)
    h, w = data.shape[0:2]
    if data.ndim == 2:
        fmt = QImage.Format_Grayscale8
    else:
        fmt = QImage.Format_RGB888
    qim = QImage(data.data, w, h, data.strides[0], fmt)
    # QImage doesn't own the numpy buffer
    return qim.copy()


def open_with_system(path):
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class LoaderSignals(QObject):
    # request id, path, mtime, QImage or None, error string or None
    loaded = pyqtSignal(int, str, float, object, object)


class ImageLoadJob(QRunnable):
    def __init__(self, signals, request_id, path, mtime, max_dim, wanted=None):
        super().__init__()
        self.signals = signals
        self.request_id = request_id
        self.path = path
        self.mtime = mtime
        self.max_dim = max_dim
        # Called from the worker thread: skip the load if this returns False
        self.wanted = wanted

    def run(self):
        qim = None
        error = None
        request_id = self.request_id
        if self.wanted is not None and not self.wanted(self.path):
            request_id = THUMB_SKIPPED
        else:
            try:
                qim = array_to_qimage(
                    load_rgb8(self.path, max_dim=self.max_dim))
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
        try:
            self.signals.loaded.emit(request_id, self.path, self.mtime, qim,
                                     error)
        except RuntimeError:
            # Receiver deleted during shutdown
            pass


class ImagePreview(QGraphicsView):
    """
    Zoomable / pannable image display
    Fit to window by default; mouse wheel or zoom buttons switch to manual zoom
    """
    zoomChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.pixmap_item = QGraphicsPixmapItem()
        self.pixmap_item.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self.pixmap_item)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(QColor(48, 48, 48)))
        self.fit = True

    def set_image(self, qim):
        if qim is None:
            self.pixmap_item.setPixmap(QPixmap())
            self.scene().setSceneRect(QRectF())
        else:
            self.pixmap_item.setPixmap(QPixmap.fromImage(qim))
            self.scene().setSceneRect(self.pixmap_item.boundingRect())
        if self.fit:
            self.fit_to_window()

    def has_image(self):
        return not self.pixmap_item.pixmap().isNull()

    def zoom_factor(self):
        return self.transform().m11()

    def fit_to_window(self):
        self.fit = True
        if self.has_image():
            self.fitInView(self.pixmap_item, Qt.KeepAspectRatio)
        self.zoomChanged.emit()

    def set_zoom(self, factor):
        self.fit = False
        factor = min(max(factor, 0.01), 64.0)
        self.setTransform(QTransform.fromScale(factor, factor))
        self.zoomChanged.emit()

    def zoom_by(self, factor):
        self.set_zoom(self.zoom_factor() * factor)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0 or not self.has_image():
            return
        self.zoom_by(1.25**(delta / 120.0))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit:
            self.fit_to_window()


class SnapshotViewerTab(ArgusTab):
    def __init__(self, ac, aname=None, parent=None):
        super().__init__(ac=ac, aname=aname, parent=parent)
        self.directory = None
        # path => (mtime, QIcon)
        self.thumb_cache = {}
        # paths with a thumbnail load in flight
        self.thumb_pending = set()
        # Paths currently scrolled into view. Replaced, not mutated, as
        # worker threads read it
        self.thumb_wanted = frozenset()
        # path => QListWidgetItem
        self.items = {}
        self.preview_request_id = 0
        self.preview_path = None
        self.select_after_refresh = None
        # Directory changed while the tab was hidden
        self.stale = True
        self.shutting_down = False

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.loader_signals = LoaderSignals()
        self.loader_signals.loaded.connect(self.image_loaded)

        # Coalesce bursts of directory change notifications
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(300)
        self.refresh_timer.timeout.connect(self.refresh)

        self.thumb_timer = QTimer(self)
        self.thumb_timer.setSingleShot(True)
        self.thumb_timer.setInterval(50)
        self.thumb_timer.timeout.connect(self.request_visible_thumbnails)

        self.watcher = QFileSystemWatcher(self)
        # Lambdas: signal arguments would be taken as QTimer.start(msec)
        self.watcher.directoryChanged.connect(
            lambda _path: self.refresh_timer.start())

    def _initUI(self):
        self.placeholder_icon = self.make_placeholder_icon()

        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.list.setGridSize(QSize(THUMB_SIZE + 24, THUMB_SIZE + 40))
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.ElideMiddle)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.currentItemChanged.connect(self.current_item_changed)
        self.list.itemDoubleClicked.connect(self.open_selected)
        self.list.verticalScrollBar().valueChanged.connect(
            lambda _value: self.thumb_timer.start())

        self.preview = ImagePreview()
        self.preview.zoomChanged.connect(self.update_zoom_label)

        self.dir_label = QLabel()
        self.dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info_label = QLabel()
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.zoom_label = QLabel()

        def button(text, slot, tooltip=None):
            pb = QPushButton(text)
            pb.clicked.connect(slot)
            if tooltip:
                pb.setToolTip(tooltip)
            return pb

        self.refresh_pb = button("Refresh", self.refresh)
        self.open_dir_pb = button("Open folder", self.open_folder,
                                  "Open snapshot directory in file manager")
        self.open_pb = button("Open", self.open_selected,
                              "Open selected image in the system viewer")
        self.delete_pb = button("Delete", self.delete_selected,
                                "Delete selected image from disk")
        self.fit_pb = button("Fit", self.preview.fit_to_window,
                             "Fit image to window")
        self.zoom_1_pb = button("1:1", lambda: self.preview.set_zoom(1.0),
                                "Actual size")
        self.zoom_in_pb = button("+", lambda: self.preview.zoom_by(1.25),
                                 "Zoom in (or mouse wheel)")
        self.zoom_out_pb = button("-", lambda: self.preview.zoom_by(0.8),
                                  "Zoom out (or mouse wheel)")

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.dir_label)
        left_layout.addWidget(self.list)
        hl = QHBoxLayout()
        for pb in (self.refresh_pb, self.open_dir_pb, self.open_pb,
                   self.delete_pb):
            hl.addWidget(pb)
        left_layout.addLayout(hl)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.info_label)
        right_layout.addWidget(self.preview)
        hl = QHBoxLayout()
        for pb in (self.fit_pb, self.zoom_1_pb, self.zoom_out_pb,
                   self.zoom_in_pb):
            hl.addWidget(pb)
        hl.addWidget(self.zoom_label)
        hl.addStretch()
        right_layout.addLayout(hl)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([400, 800])

        layout = QVBoxLayout()
        layout.addWidget(splitter)
        self.setLayout(layout)

        self.shortcut_delete = QShortcut(QKeySequence.Delete, self.list)
        self.shortcut_delete.setContext(Qt.WidgetShortcut)
        self.shortcut_delete.activated.connect(self.delete_selected)

        self.update_buttons()

    def _post_ui_init(self):
        self.set_directory(self.ac.aconfig.snapshot_dir())
        self.ac.snapshotCaptured.connect(self.snapshot_captured)

    def _shutdown_request(self, phase):
        self.shutting_down = True
        self.refresh_timer.stop()
        self.thumb_timer.stop()
        self.pool.clear()

    def _shutdown_join(self):
        self.pool.waitForDone(3000)

    def make_placeholder_icon(self):
        pm = QPixmap(THUMB_SIZE, THUMB_SIZE)
        pm.fill(QColor(200, 200, 200))
        return QIcon(pm)

    def set_directory(self, directory):
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        directory = os.path.abspath(directory)
        self.directory = directory
        self.dir_label.setText(directory)
        if os.path.isdir(directory):
            self.watcher.addPath(directory)
        self.refresh()

    def snapshot_captured(self, data):
        capim = data.get("captured_image")
        if capim is None:
            return
        fn = capim.meta.get("save_filename")
        if not fn:
            return
        self.select_after_refresh = os.path.abspath(fn)
        self.refresh_timer.start()

    def refresh(self):
        if self.shutting_down or self.directory is None:
            return
        # Don't decode images in the background while the user is imaging
        if not self.isVisible():
            self.stale = True
            return
        self.stale = False
        # Directory may have been created after we started
        if not self.watcher.directories() and os.path.isdir(self.directory):
            self.watcher.addPath(self.directory)

        images = list_images(self.directory)
        cur = self.selected_path()
        scroll = self.list.verticalScrollBar().value()
        jump = bool(self.select_after_refresh)
        if jump:
            cur = self.select_after_refresh
            self.select_after_refresh = None
        present = set(path for path, _mtime, _size in images)
        for path in list(self.thumb_cache):
            if path not in present:
                del self.thumb_cache[path]

        self.list.blockSignals(True)
        try:
            self.list.clear()
            self.items = {}
            select_item = None
            for path, mtime, size in images:
                item = QListWidgetItem(os.path.basename(path))
                item.setData(Qt.UserRole, path)
                item.setData(Qt.UserRole + 1, mtime)
                item.setData(Qt.UserRole + 2, size)
                item.setToolTip(path)
                cached = self.thumb_cache.get(path)
                if cached and cached[0] == mtime:
                    item.setIcon(cached[1])
                else:
                    item.setIcon(self.placeholder_icon)
                self.list.addItem(item)
                self.items[path] = item
                if path == cur:
                    select_item = item
            if select_item is None and self.list.count():
                select_item = self.list.item(0)
            if select_item is not None:
                self.list.setCurrentItem(select_item)
        finally:
            self.list.blockSignals(False)
        if jump and select_item is not None:
            self.list.scrollToItem(select_item)
        else:
            self.list.verticalScrollBar().setValue(scroll)
        self.current_item_changed(self.list.currentItem(), None)
        self.thumb_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        if self.stale:
            QTimer.singleShot(0, self.refresh)
        self.thumb_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.thumb_timer.start()

    def visible_items(self):
        viewport = self.list.viewport().rect()
        ret = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if self.list.visualItemRect(item).intersects(viewport):
                ret.append(item)
        return ret

    def request_visible_thumbnails(self):
        if self.shutting_down or not self.list.isVisible():
            return
        visible = self.visible_items()
        self.thumb_wanted = frozenset(
            item.data(Qt.UserRole) for item in visible)
        for item in visible:
            path = item.data(Qt.UserRole)
            mtime = item.data(Qt.UserRole + 1)
            cached = self.thumb_cache.get(path)
            if cached and cached[0] == mtime:
                continue
            if path in self.thumb_pending:
                continue
            self.thumb_pending.add(path)
            self.pool.start(
                ImageLoadJob(self.loader_signals,
                             THUMB_REQUEST,
                             path,
                             mtime,
                             THUMB_SIZE,
                             wanted=self.thumb_is_wanted))

    def thumb_is_wanted(self, path):
        return path in self.thumb_wanted

    def retry_if_recent(self, mtime):
        """
        Load failed: if the file is new it may be partially written
        The directory watcher doesn't report content changes, so poll
        """
        if time.time() - mtime < RETRY_WINDOW:
            QTimer.singleShot(1000, self.refresh)
            return True
        return False

    def image_loaded(self, request_id, path, mtime, qim, error):
        if self.shutting_down:
            return
        if request_id == THUMB_SKIPPED:
            self.thumb_pending.discard(path)
            # May have scrolled back into view while queued
            self.thumb_timer.start()
            return
        if request_id == THUMB_REQUEST:
            self.thumb_pending.discard(path)
            item = self.items.get(path)
            if item is None:
                return
            if qim is None and self.retry_if_recent(mtime):
                return
            if qim is None:
                icon = self.style().standardIcon(QStyle.SP_MessageBoxWarning)
            else:
                icon = QIcon(QPixmap.fromImage(qim))
            self.thumb_cache[path] = (mtime, icon)
            if item.data(Qt.UserRole + 1) == mtime:
                item.setIcon(icon)
            return
        # Preview: ignore stale results from a previous selection
        if request_id != self.preview_request_id:
            return
        if qim is None:
            self.preview.set_image(None)
            self.info_label.setText(
                f"{os.path.basename(path)}: failed to load ({error})")
            if self.retry_if_recent(mtime):
                # Force a reload even if mtime ends up unchanged
                self.preview_path = None
            return
        self.preview.set_image(qim)
        size = self.list.currentItem().data(
            Qt.UserRole + 2) if self.list.currentItem() else None
        msg = f"{os.path.basename(path)}  {qim.width()} x {qim.height()}"
        if size is not None:
            msg += f"  {size / 1e6:.1f} MB"
        self.info_label.setText(msg)

    def selected_path(self):
        item = self.list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def current_item_changed(self, current, _previous):
        self.update_buttons()
        if current is None:
            self.preview_path = None
            self.preview_request_id += 1
            self.preview.set_image(None)
            self.info_label.setText("No snapshots")
            return
        path = current.data(Qt.UserRole)
        mtime = current.data(Qt.UserRole + 1)
        if (path, mtime) == self.preview_path:
            return
        self.preview_path = (path, mtime)
        self.preview_request_id += 1
        self.info_label.setText(f"{os.path.basename(path)}: loading...")
        # Ahead of any queued thumbnails
        self.pool.start(ImageLoadJob(self.loader_signals,
                                     self.preview_request_id, path, mtime,
                                     None),
                        priority=1)

    def update_buttons(self):
        have = self.list.currentItem() is not None
        self.open_pb.setEnabled(have)
        self.delete_pb.setEnabled(have)

    def update_zoom_label(self):
        if self.preview.fit:
            self.zoom_label.setText(
                f"Fit ({self.preview.zoom_factor() * 100:.0f}%)")
        else:
            self.zoom_label.setText(f"{self.preview.zoom_factor() * 100:.0f}%")

    def open_folder(self):
        if self.directory and os.path.isdir(self.directory):
            open_with_system(self.directory)

    def open_selected(self, *_args):
        path = self.selected_path()
        if path:
            open_with_system(path)

    def delete_selected(self):
        path = self.selected_path()
        if not path:
            return
        ret = QMessageBox.question(self, "Delete snapshot",
                                   f"Permanently delete {path}?",
                                   QMessageBox.Yes | QMessageBox.No,
                                   QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        row = self.list.currentRow()
        try:
            os.remove(path)
        except OSError as e:
            QMessageBox.warning(self, "Delete snapshot",
                                f"Failed to delete {path}: {e}")
            return
        self.ac.log(f"Snapshot viewer: deleted {path}")
        # Keep the selection at the same position for quick culling
        if row + 1 < self.list.count():
            self.select_after_refresh = self.list.item(row + 1).data(
                Qt.UserRole)
        elif row > 0:
            self.select_after_refresh = self.list.item(row - 1).data(
                Qt.UserRole)
        self.refresh()
