"""
Stage navigation minimap tab
Pastes downscaled live frames at the current stage position (dead reckoning)
Click on the map to move the stage there
"""

from uscope.gui.widgets import ArgusTab
from uscope.minimap import MapCoords, MinimapStack, nice_mm_per_pix, DEFAULT_MAX_BYTES
from uscope.threads import ShutdownPhase
from uscope.scan_runner import ScanRunnerListener

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

import math
import threading
import time
import traceback

# Scene coordinates are stage um so that layers of any resolution line up
SCENE_MM_PER_UNIT = 0.001

# Map pixels across a field of view when resolution is "Auto"
AUTO_FOV_PIX = 128
# (label, mm per map pixel)
RESOLUTIONS = [
    ("Auto (per objective)", None),
    ("1 um/pix", 0.001),
    ("2 um/pix", 0.002),
    ("5 um/pix", 0.005),
    ("10 um/pix", 0.010),
    ("20 um/pix", 0.020),
    ("50 um/pix", 0.050),
    ("100 um/pix", 0.100),
]
# Re-capture an already painted position this often to pick up changes
# (ex: lighting, sample moved by hand)
# Focus and imager property changes re-capture as soon as they settle
REFRESH_PERIOD = 1.0
# Minimum time stopped before capturing (kinematics tsettle_motion if larger)
TSETTLE_MIN = 0.1
# Moving layer z offset: larger than the z spread of all resolutions
MOVING_Z = 100.0
# Mouse movement in view pixels below which a press / release is a click
CLICK_SLOP = 4


class MinimapView(QGraphicsView):
    # Scene coordinates
    clicked = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QBrush(QColor(40, 40, 40)))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.press_pos = None
        # (mm_per_pix, tu, tv) => QGraphicsPixmapItem
        self.tile_items = {}

        pen = QPen(QColor(255, 255, 0))
        pen.setCosmetic(True)
        pen.setWidth(2)
        self.fov_item = self.scene().addRect(QRectF(), pen)
        self.fov_item.setZValue(1000)
        self.fov_item.setVisible(False)
        pen = QPen(QColor(255, 255, 0))
        pen.setCosmetic(True)
        self.cross_items = [
            self.scene().addLine(QLineF(), pen),
            self.scene().addLine(QLineF(), pen)
        ]
        for item in self.cross_items:
            item.setZValue(1000)
            item.setVisible(False)

    def update_tiles(self, minimap, dirty):
        scale = minimap.mm_per_pix / SCENE_MM_PER_UNIT
        for key, tile in dirty.items():
            item_key = (minimap.mm_per_pix, minimap.moving) + key
            item = self.tile_items.get(item_key)
            if tile is None:
                if item is not None:
                    self.scene().removeItem(item)
                    del self.tile_items[item_key]
                continue
            h, w = tile.rgba.shape[0:2]
            # copy(): QImage doesn't own the buffer
            qim = QImage(tile.rgba.data, w, h, 4 * w,
                         QImage.Format_RGBA8888).copy()
            pixmap = QPixmap.fromImage(qim)
            if item is None:
                item = self.scene().addPixmap(pixmap)
                # Pixelated is more honest about the resolution
                item.setTransformationMode(Qt.FastTransformation)
                u, v = minimap.tile_origin(key)
                item.setPos(u * scale, v * scale)
                item.setScale(scale)
                # Higher resolution (higher magnification) on top
                # Still frames above all moving frames
                z = -math.log10(minimap.mm_per_pix)
                if minimap.moving:
                    z -= MOVING_Z
                item.setZValue(z)
                self.tile_items[item_key] = item
            else:
                item.setPixmap(pixmap)

    def set_fov(self, rect):
        """rect: (u0, v0, w, h) or None"""
        if rect is None:
            self.fov_item.setVisible(False)
            for item in self.cross_items:
                item.setVisible(False)
            return
        u0, v0, w, h = rect
        self.fov_item.setRect(QRectF(u0, v0, w, h))
        self.fov_item.setVisible(True)
        cu = u0 + w / 2
        cv = v0 + h / 2
        d = min(w, h) / 8
        self.cross_items[0].setLine(QLineF(cu - d, cv, cu + d, cv))
        self.cross_items[1].setLine(QLineF(cu, cv - d, cu, cv + d))
        for item in self.cross_items:
            item.setVisible(True)

    def fit(self, rect):
        """rect: QRectF in scene coordinates"""
        if rect is None or rect.isEmpty():
            return
        self.setSceneRect(rect.adjusted(-rect.width(), -rect.height(),
                                        rect.width(), rect.height()))
        self.fitInView(rect, Qt.KeepAspectRatio)

    def ensure_scene_rect(self, rect):
        # Grow the scrollable area as the map grows so panning isn't clamped
        cur = self.sceneRect()
        want = rect.adjusted(-rect.width(), -rect.height(), rect.width(),
                             rect.height())
        if not cur.contains(want):
            self.setSceneRect(cur.united(want))

    def center_on_rect(self, rect):
        self.ensure_scene_rect(rect)
        self.centerOn(rect.center())

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() != Qt.LeftButton or self.press_pos is None:
            return
        delta = event.pos() - self.press_pos
        self.press_pos = None
        if delta.manhattanLength() > CLICK_SLOP:
            return
        p = self.mapToScene(event.pos())
        self.clicked.emit(p.x(), p.y())


class MinimapScanListener(QObject, ScanRunnerListener):
    """
    Forwards images the scan took to the GUI thread
    The scan owns the camera so the minimap can't grab its own frames
    """
    scanImage = pyqtSignal(dict)

    def __init__(self, ac):
        super().__init__()
        self.ac = ac

    def scan_progress(self, state):
        # Scan thread: keep it cheap, downscaling happens elsewhere
        if state.get("type") != "image":
            return
        capim = state.get("captured_image")
        if capim is None or capim.image is None:
            return
        # Planner just finished this tile and hasn't moved on yet
        pos = self.ac.motion_thread.get_pos_cache()
        if not pos or "x" not in pos or "y" not in pos:
            return
        self.scanImage.emit({"image": capim.image, "pos": dict(pos)})


class MinimapTab(ArgusTab):
    # dict from capture thread
    frameReady = pyqtSignal(dict)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stack = None
        # Latest scan image waiting for the capture thread
        self.scan_pending = None
        self.scan_listener = MinimapScanListener(self.ac)
        self.scan_listener.scanImage.connect(self.scan_image)
        self.ac.scan_runner.add_listener(self.scan_listener)
        # Stage mm <=> scene
        self.coords = None
        # Bumped when the map is reset so in flight captures are dropped
        self.generation = 0
        self.capture_thread = None
        # Last pos seen and when it last changed
        self.last_pos = None
        self.last_z = None
        self.tpos_changed = 0.0
        # Capture failed: don't retry until then
        self.tbackoff = 0.0
        # (xy, z, mm_per_pix) / when we last successfully pasted
        self.pasted_pos = None
        self.tpasted = 0.0
        self.last_fov = None
        self.last_mm_per_pix = None
        self.fit_pending = False
        self.frameReady.connect(self.frame_ready)

        layout = QVBoxLayout()

        hl = QHBoxLayout()
        self.enable_cb = QCheckBox("Enable")
        self.enable_cb.setToolTip(
            "Paste live frames at the current stage position. Uses memory while enabled"
        )
        self.enable_cb.stateChanged.connect(self.enable_changed)
        hl.addWidget(self.enable_cb)
        self.follow_cb = QCheckBox("Follow")
        self.follow_cb.setToolTip("Keep the current position in view")
        self.follow_cb.setChecked(True)
        hl.addWidget(self.follow_cb)
        hl.addWidget(QLabel("Resolution"))
        self.resolution_cb = QComboBox()
        for label, _mm_per_pix in RESOLUTIONS:
            self.resolution_cb.addItem(label)
        self.resolution_cb.currentIndexChanged.connect(
            self.resolution_changed)
        hl.addWidget(self.resolution_cb)
        hl.addStretch()
        layout.addLayout(hl)

        hl = QHBoxLayout()
        self.fit_pb = QPushButton("Fit")
        self.fit_pb.setToolTip("Zoom to show the whole map")
        self.fit_pb.clicked.connect(self.fit)
        hl.addWidget(self.fit_pb)
        self.zoom_fov_pb = QPushButton("Zoom FoV")
        self.zoom_fov_pb.setToolTip("Zoom to the current field of view")
        self.zoom_fov_pb.clicked.connect(self.zoom_fov)
        hl.addWidget(self.zoom_fov_pb)
        self.clear_pb = QPushButton("Clear")
        self.clear_pb.clicked.connect(self.clear)
        hl.addWidget(self.clear_pb)
        hl.addStretch()
        layout.addLayout(hl)

        self.view = MinimapView()
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.view.clicked.connect(self.map_clicked)
        layout.addWidget(self.view)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        layout.addWidget(
            QLabel("Click to move there. Drag to pan, wheel to zoom"))

        self.setLayout(layout)
        self.update_status()

    def _cache_save(self, cachej):
        cachej["minimap"] = {
            "enable": self.enable_cb.isChecked(),
        }

    def _cache_load(self, cachej):
        j = cachej.get("minimap", {})
        self.enable_cb.setChecked(j.get("enable", True))

    def enabled(self):
        return self.stack is not None

    def enable_changed(self):
        if self.enable_cb.isChecked():
            self.reset_map()
        else:
            self.free_map()

    def resolution_changed(self):
        # Existing layers stay: new frames go to a layer at the new resolution
        self.pasted_pos = None
        self.update_status()

    def fov_wh(self, image_wh=None):
        """Field of view (mm) for an image of the given size"""
        x_view = self.ac.objective_config()["x_view"]
        if image_wh is None:
            image_wh = self.ac.usc.imager.cropped_wh()
        return x_view, x_view * image_wh[1] / image_wh[0]

    def mm_per_pix(self):
        """Resolution of the layer frames are currently pasted into"""
        mm_per_pix = RESOLUTIONS[self.resolution_cb.currentIndex()][1]
        if mm_per_pix is None:
            # Per objective
            mm_per_pix = nice_mm_per_pix(self.fov_wh()[0], AUTO_FOV_PIX)
        return mm_per_pix

    def reset_map(self):
        self.free_map()
        origin = self.ac.usc.motion.origin()
        self.stack = MinimapStack(origin=origin, max_bytes=DEFAULT_MAX_BYTES)
        self.coords = MapCoords(SCENE_MM_PER_UNIT, origin=origin)
        self.fit_pending = True
        self.update_status()

    def update_view_tiles(self):
        for layer in self.stack.layers.values():
            self.view.update_tiles(layer, layer.pop_dirty())

    def free_map(self):
        self.generation += 1
        self.pasted_pos = None
        if self.stack is not None:
            self.stack.clear()
            self.update_view_tiles()
        self.stack = None
        self.last_fov = None
        self.view.set_fov(None)
        self.update_status()

    def clear(self):
        if not self.enabled():
            return
        self.generation += 1
        self.pasted_pos = None
        self.stack.clear()
        self.update_view_tiles()
        self.update_status()

    def update_status(self):
        if self.stack is None:
            self.status_label.setText("Disabled")
            return
        try:
            res = "%0.1f um/pix" % (self.mm_per_pix() * 1000, )
        except Exception:
            res = "? um/pix"
        # Resolutions in use (each has a still + moving layer)
        layers = len(
            set(l.mm_per_pix for l in self.stack.layers.values() if l.tiles))
        self.status_label.setText(
            "%s, %u layers, %u tiles, %0.1f / %0.0f MB" %
            (res, layers, self.stack.num_tiles(),
             self.stack.memory_bytes() / 1e6, DEFAULT_MAX_BYTES / 1e6))

    def scene_bounds(self):
        """Painted area as a scene QRectF, or None"""
        bounds = self.stack.bounds_mm()
        if bounds is None:
            return None
        ua, va = self.coords.stage_to_map(bounds[0], bounds[1])
        ub, vb = self.coords.stage_to_map(bounds[2], bounds[3])
        return QRectF(QPointF(min(ua, ub), min(va, vb)),
                      QPointF(max(ua, ub), max(va, vb)))

    def fit(self):
        if not self.enabled():
            return
        rect = self.scene_bounds()
        if rect is None:
            self.zoom_fov()
            return
        if self.last_fov:
            rect = rect.united(QRectF(*self.last_fov))
        self.view.fit(rect)

    def zoom_fov(self):
        if not self.last_fov:
            return
        u0, v0, w, h = self.last_fov
        # Some context around it
        self.view.fit(QRectF(u0 - w, v0 - h, 3 * w, 3 * h))

    def map_clicked(self, u, v):
        if not self.enabled():
            return
        if self.ac.is_planner_running():
            self.ac.log("Minimap: can't move while scan is running")
            return
        if not self.ac.is_idle():
            return
        # Don't move off into the unknown: stay near what we've seen
        bounds = self.scene_bounds()
        if bounds is None or self.last_fov is None:
            return
        _fu0, _fv0, fw, fh = self.last_fov
        if not bounds.adjusted(-fw, -fh, fw, fh).contains(QPointF(u, v)):
            self.ac.log("Minimap: click is too far outside of mapped area")
            return
        x, y = self.coords.map_to_stage(u, v)
        pos = {"x": x, "y": y}
        self.ac.log("Minimap: move to %s" %
                    self.ac.usc.motion.format_positions(pos))
        # backlash compensation is applied by the motion HAL
        self.ac.motion_thread.move_absolute(pos)

    def tsettle(self):
        kinematics = self.ac.microscope.kinematics
        tsettle = getattr(kinematics, "tsettle_motion", None) or 0.0
        return max(TSETTLE_MIN, tsettle)

    def needs_capture(self, xy, z, mm_per_pix, now):
        if (xy, z, mm_per_pix) != self.pasted_pos:
            return True
        if now - self.tpasted >= REFRESH_PERIOD:
            return True
        # ex: exposure changed since the frame on the map
        return self.ac.imager.since_properties_change() < now - self.tpasted

    def _poll_misc(self):
        if not self.enabled():
            return
        pos = self.ac.motion_thread.get_pos_cache()
        if not pos or "x" not in pos or "y" not in pos:
            return
        try:
            fov_wh = self.fov_wh()
            mm_per_pix = self.mm_per_pix()
        except Exception:
            return
        if mm_per_pix != self.last_mm_per_pix:
            # ex: objective changed
            self.last_mm_per_pix = mm_per_pix
            self.update_status()
        self.last_fov = self.coords.fov_map_rect(pos["x"], pos["y"], *fov_wh)
        self.view.set_fov(self.last_fov)
        xy = (pos["x"], pos["y"])
        if self.fit_pending:
            self.fit_pending = False
            self.zoom_fov()
        # Only when moving so the user can still pan away
        elif self.follow_cb.isChecked() and xy != self.last_pos:
            rect = QRectF(*self.last_fov)
            visible = self.view.mapToScene(
                self.view.viewport().rect()).boundingRect()
            if not visible.contains(rect):
                self.view.center_on_rect(rect)

        now = time.time()
        z = pos.get("z")
        if xy != self.last_pos or z != self.last_z:
            self.last_pos = xy
            self.last_z = z
            self.tpos_changed = now
        if self.capture_thread is not None:
            return
        if now < self.tbackoff:
            return
        if self.ac.imager is None:
            return
        # Moving frames go to a layer shown under the still frames
        settled = self.ac.motion_thread.idle.is_set(
        ) and now - self.tpos_changed >= self.tsettle()
        if settled and not self.needs_capture(xy, z, mm_per_pix, now):
            return
        # Don't compete with scans / snapshots for the camera
        if self.ac.is_planner_running():
            return
        if self.ac.mainTab.imaging_widget.taking_snapshot:
            return
        if self.ac.image_processing_thread.autofocus_running:
            return
        self.start_capture(xy, z, mm_per_pix, settled)

    def start_capture(self,
                      xy,
                      z,
                      mm_per_pix,
                      settled,
                      image=None,
                      check_pos=True):
        """
        image: already captured (ex: by a scan) instead of grabbing one
        check_pos: demote a settled frame to moving if the stage moved during capture
        """
        args = {
            "xy": xy,
            "z": z,
            "settled": settled,
            "check_pos": check_pos,
            "image": image,
            "generation": self.generation,
            "mm_per_pix": mm_per_pix,
            # Downscaling only depends on resolution: either layer will do
            "downscaler": self.stack.layer(mm_per_pix),
            "x_view": self.ac.objective_config()["x_view"],
        }
        self.capture_thread = threading.Thread(target=self.capture_run,
                                               args=(args, ),
                                               name="minimap-capture",
                                               daemon=True)
        self.capture_thread.start()

    def scan_image(self, j):
        if not self.enabled():
            return
        self.scan_pending = j
        self.start_scan_pending()

    def start_scan_pending(self):
        if self.scan_pending is None or self.capture_thread is not None:
            return
        j = self.scan_pending
        self.scan_pending = None
        try:
            mm_per_pix = self.mm_per_pix()
        except Exception:
            return
        pos = j["pos"]
        # Scan already waited for the stage to settle
        # It may have moved on by the time this is pasted
        self.start_capture((pos["x"], pos["y"]),
                           pos.get("z"),
                           mm_per_pix,
                           settled=True,
                           image=j["image"],
                           check_pos=False)

    def capture_run(self, args):
        """Capture thread: grab a frame and shrink it to map resolution"""
        try:
            image = args.pop("image")
            if image is None:
                capim = self.ac.imager.get(recover_errors=False, timeout=5.0)
                image = capim.image
            w, h = image.size
            fov_w = args["x_view"]
            fov_h = fov_w * h / w
            args["small"] = args["downscaler"].downscale(image, fov_w, fov_h)
        except Exception as e:
            print("minimap: capture failed")
            traceback.print_exc()
            args["exception"] = e
        try:
            self.frameReady.emit(args)
        except RuntimeError:
            # GUI already torn down
            pass

    def frame_ready(self, args):
        self.capture_thread = None
        try:
            self._frame_ready(args)
        finally:
            self.start_scan_pending()

    def _frame_ready(self, args):
        if "exception" in args:
            # Don't hammer a broken camera
            self.tbackoff = time.time() + 5.0
            return
        if args["generation"] != self.generation or not self.enabled():
            return
        pos = self.ac.motion_thread.get_pos_cache()
        if not pos:
            return
        settled = args["settled"]
        # Stage moved during capture: frame may be blurred / misplaced
        if settled and args["check_pos"] and (
            (pos["x"], pos["y"]) != args["xy"] or pos.get("z") != args["z"]):
            settled = False
        # Objective changed during capture: frame may not match x_view
        if self.ac.objective_config()["x_view"] != args["x_view"]:
            return
        layer = self.stack.layer(args["mm_per_pix"], moving=not settled)
        self.stack.paste(layer, args["small"], *args["xy"])
        # Eviction can hit any layer
        self.update_view_tiles()
        bounds = self.scene_bounds()
        if bounds:
            self.view.ensure_scene_rect(bounds)
        if settled:
            self.pasted_pos = (args["xy"], args["z"], layer.mm_per_pix)
            self.tpasted = time.time()
        self.update_status()

    def _shutdown_request(self, phase):
        if phase == ShutdownPhase.FINAL:
            self.generation += 1

    def _shutdown_join(self):
        thread = self.capture_thread
        if thread is not None:
            thread.join(timeout=2.0)
