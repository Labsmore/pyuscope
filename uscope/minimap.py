"""
Low resolution stage "minimap" built by dead reckoning

Frames are downscaled and pasted into a sparse tiled canvas at the location
given by the stage position. No registration is done: accuracy is whatever
the stage and um/pixel calibration give

Coordinates
-stage: mm, (x, y) is the center of the field of view
-map: minimap pixels, u increases to the right, v increases down
    u = x / mm_per_pix
    v = -y / mm_per_pix for "ll" origin (stage +Y is image up)
    v = +y / mm_per_pix for "ul" origin (stage +Y is image down)
    This matches how the planner lays out images (see XYPlanner2P)

Pure numpy / PIL: no Qt
"""

from collections import OrderedDict
import math

import numpy as np
from PIL import Image

TILE_PIX = 256
# RGBA uint8 tiles
TILE_BYTES = TILE_PIX * TILE_PIX * 4
DEFAULT_MAX_BYTES = 256 * 1024 * 1024


def nice_mm_per_pix(fov_w_mm, target_pix=128):
    """
    Pick a round (1, 2, 5 * 10^n) resolution such that a field of view
    is roughly target_pix wide
    """
    assert fov_w_mm > 0
    raw = fov_w_mm / target_pix
    exp = math.floor(math.log10(raw))
    base = 10**exp
    for mult in (1, 2, 5, 10):
        if base * mult >= raw * 0.999:
            return base * mult
    return base * 10


class Tile:
    def __init__(self):
        self.rgba = np.zeros((TILE_PIX, TILE_PIX, 4), dtype=np.uint8)
        # Painted area in local tile pixels: (u0, v0, u1, v1), exclusive end
        self.bbox = None

    def add_bbox(self, u0, v0, u1, v1):
        if self.bbox is None:
            self.bbox = (u0, v0, u1, v1)
        else:
            b = self.bbox
            self.bbox = (min(b[0], u0), min(b[1], v0), max(b[2], u1),
                         max(b[3], v1))


class Minimap:
    def __init__(self,
                 mm_per_pix,
                 origin="ll",
                 max_bytes=DEFAULT_MAX_BYTES):
        assert mm_per_pix > 0
        assert origin in ("ll", "ul"), origin
        self.mm_per_pix = float(mm_per_pix)
        self.origin = origin
        self.max_tiles = max(1, int(max_bytes // TILE_BYTES))
        # (tu, tv) => Tile
        # Ordered least recently pasted first for eviction
        self.tiles = OrderedDict()
        # Tile keys changed (painted or evicted) since last pop_dirty()
        self.dirty = set()
        self.evictions = 0

    def stage_to_map(self, x, y):
        u = x / self.mm_per_pix
        v = y / self.mm_per_pix
        if self.origin == "ll":
            v = -v
        return u, v

    def map_to_stage(self, u, v):
        x = u * self.mm_per_pix
        y = v * self.mm_per_pix
        if self.origin == "ll":
            y = -y
        return x, y

    def fov_map_rect(self, x, y, fov_w_mm, fov_h_mm):
        """
        Return (u0, v0, w, h) in map pixels of a field of view centered at x, y
        """
        cu, cv = self.stage_to_map(x, y)
        w = fov_w_mm / self.mm_per_pix
        h = fov_h_mm / self.mm_per_pix
        return cu - w / 2, cv - h / 2, w, h

    def scaled_wh(self, fov_w_mm, fov_h_mm):
        return (max(1, int(round(fov_w_mm / self.mm_per_pix))),
                max(1, int(round(fov_h_mm / self.mm_per_pix))))

    def downscale(self, image, fov_w_mm, fov_h_mm):
        """
        image: PIL image or HxWx3 / HxWx4 uint8 array as displayed
        Return HxWx3 uint8 array at minimap resolution
        Safe to call from any thread (doesn't touch the map)
        """
        if isinstance(image, np.ndarray):
            image = Image.fromarray(image)
        if image.mode != "RGB":
            image = image.convert("RGB")
        wh = self.scaled_wh(fov_w_mm, fov_h_mm)
        if image.size != wh:
            # reduce() first: much faster than a big BOX resize alone
            factor = min(image.size[0] // wh[0], image.size[1] // wh[1])
            if factor >= 2:
                image = image.reduce(factor)
            image = image.resize(wh, Image.BILINEAR)
        return np.asarray(image, dtype=np.uint8)

    def paste(self, small, x, y):
        """
        small: HxWx3 uint8 array at minimap resolution (see downscale())
        x, y: stage position (mm) at the center of the image
        """
        h, w = small.shape[0:2]
        cu, cv = self.stage_to_map(x, y)
        u0 = int(round(cu - w / 2))
        v0 = int(round(cv - h / 2))
        u1 = u0 + w
        v1 = v0 + h
        for tv in range(v0 // TILE_PIX, (v1 - 1) // TILE_PIX + 1):
            for tu in range(u0 // TILE_PIX, (u1 - 1) // TILE_PIX + 1):
                # Intersection in map pixels
                iu0 = max(u0, tu * TILE_PIX)
                iu1 = min(u1, (tu + 1) * TILE_PIX)
                iv0 = max(v0, tv * TILE_PIX)
                iv1 = min(v1, (tv + 1) * TILE_PIX)
                tile = self._get_tile((tu, tv))
                lu0 = iu0 - tu * TILE_PIX
                lu1 = iu1 - tu * TILE_PIX
                lv0 = iv0 - tv * TILE_PIX
                lv1 = iv1 - tv * TILE_PIX
                tile.rgba[lv0:lv1, lu0:lu1, 0:3] = small[iv0 - v0:iv1 - v0,
                                                         iu0 - u0:iu1 - u0]
                tile.rgba[lv0:lv1, lu0:lu1, 3] = 255
                tile.add_bbox(lu0, lv0, lu1, lv1)
                self.dirty.add((tu, tv))
        self._evict()

    def _get_tile(self, key):
        tile = self.tiles.get(key)
        if tile is None:
            tile = Tile()
            self.tiles[key] = tile
        else:
            self.tiles.move_to_end(key)
        return tile

    def _evict(self):
        while len(self.tiles) > self.max_tiles:
            key, _tile = self.tiles.popitem(last=False)
            self.dirty.add(key)
            self.evictions += 1

    def pop_dirty(self):
        """
        Return {(tu, tv): Tile or None (evicted)} changed since the last call
        """
        ret = dict([(key, self.tiles.get(key)) for key in self.dirty])
        self.dirty = set()
        return ret

    def clear(self):
        self.dirty.update(self.tiles.keys())
        self.tiles = OrderedDict()

    def empty(self):
        return len(self.tiles) == 0

    def memory_bytes(self):
        return len(self.tiles) * TILE_BYTES

    def tile_origin(self, key):
        """Map pixel of the tile's upper left corner"""
        return key[0] * TILE_PIX, key[1] * TILE_PIX

    def bounds(self):
        """
        Painted area as map pixels (u0, v0, u1, v1), exclusive end
        None if nothing painted
        """
        ret = None
        for (tu, tv), tile in self.tiles.items():
            if tile.bbox is None:
                continue
            b = tile.bbox
            this = (tu * TILE_PIX + b[0], tv * TILE_PIX + b[1],
                    tu * TILE_PIX + b[2], tv * TILE_PIX + b[3])
            if ret is None:
                ret = this
            else:
                ret = (min(ret[0], this[0]), min(ret[1], this[1]),
                       max(ret[2], this[2]), max(ret[3], this[3]))
        return ret

    def render(self, u0, v0, w, h):
        """
        Return an HxWx4 RGBA array of the given map area
        Unpainted area is transparent (all zero)
        """
        ret = np.zeros((h, w, 4), dtype=np.uint8)
        u1 = u0 + w
        v1 = v0 + h
        for tv in range(v0 // TILE_PIX, (v1 - 1) // TILE_PIX + 1):
            for tu in range(u0 // TILE_PIX, (u1 - 1) // TILE_PIX + 1):
                tile = self.tiles.get((tu, tv))
                if tile is None:
                    continue
                iu0 = max(u0, tu * TILE_PIX)
                iu1 = min(u1, (tu + 1) * TILE_PIX)
                iv0 = max(v0, tv * TILE_PIX)
                iv1 = min(v1, (tv + 1) * TILE_PIX)
                ret[iv0 - v0:iv1 - v0, iu0 - u0:iu1 - u0] = tile.rgba[
                    iv0 - tv * TILE_PIX:iv1 - tv * TILE_PIX,
                    iu0 - tu * TILE_PIX:iu1 - tu * TILE_PIX]
        return ret
