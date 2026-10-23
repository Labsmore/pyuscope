#!/usr/bin/env -S uv run
"""
Stage minimap canvas / coordinate logic (no Qt)
"""

import unittest

import numpy as np
from PIL import Image

from uscope import minimap
from uscope.minimap import Minimap, MinimapStack, TILE_PIX, TILE_BYTES


def solid(w, h, rgb):
    ret = np.zeros((h, w, 3), dtype=np.uint8)
    ret[:, :] = rgb
    return ret


class TestMinimap(unittest.TestCase):
    def test_nice_mm_per_pix(self):
        self.assertAlmostEqual(minimap.nice_mm_per_pix(1.28, 128), 0.01)
        self.assertAlmostEqual(minimap.nice_mm_per_pix(1.5, 128), 0.02)
        self.assertAlmostEqual(minimap.nice_mm_per_pix(4.0, 128), 0.05)
        self.assertAlmostEqual(minimap.nice_mm_per_pix(10.0, 128), 0.1)
        # Never coarser than requested by more than one step
        for fov in (0.05, 0.3, 2.7, 33.0):
            mpp = minimap.nice_mm_per_pix(fov, 128)
            self.assertGreaterEqual(mpp, fov / 128 * 0.999)
            self.assertLess(mpp, fov / 128 * 2.5)

    def test_coordinate_roundtrip(self):
        for origin in ("ll", "ul"):
            m = Minimap(0.01, origin=origin)
            for x, y in ((0, 0), (1.5, -2.25), (-30.0, 12.0)):
                u, v = m.stage_to_map(x, y)
                x2, y2 = m.map_to_stage(u, v)
                self.assertAlmostEqual(x, x2)
                self.assertAlmostEqual(y, y2)

    def test_orientation(self):
        # ll: stage +Y is up on the map (smaller v)
        m = Minimap(0.01, origin="ll")
        self.assertLess(m.stage_to_map(0, 1)[1], m.stage_to_map(0, 0)[1])
        self.assertGreater(m.stage_to_map(1, 0)[0], m.stage_to_map(0, 0)[0])
        m = Minimap(0.01, origin="ul")
        self.assertGreater(m.stage_to_map(0, 1)[1], m.stage_to_map(0, 0)[1])

    def test_paste_location(self):
        m = Minimap(0.01, origin="ll")
        # 1 x 0.5 mm FoV => 100 x 50 pix
        small = m.downscale(solid(1000, 500, (10, 20, 30)), 1.0, 0.5)
        self.assertEqual(small.shape, (50, 100, 3))
        # Mark the image's top row so we can check it lands on the +Y side
        small = small.copy()
        small[0, :] = (255, 0, 0)
        m.paste(small, 5.0, 2.0)
        u0, v0, u1, v1 = m.bounds()
        self.assertEqual((u1 - u0, v1 - v0), (100, 50))
        self.assertEqual((u0, v0), (450, -225))
        out = m.render(u0, v0, 100, 50)
        self.assertEqual(tuple(out[0, 0]), (255, 0, 0, 255))
        self.assertEqual(tuple(out[1, 0]), (10, 20, 30, 255))
        # Top of image is the high Y edge of the FoV
        x, y = m.map_to_stage(u0, v0)
        self.assertAlmostEqual(x, 4.5)
        self.assertAlmostEqual(y, 2.25)
        # Map center back to stage center
        x, y = m.map_to_stage((u0 + u1) / 2, (v0 + v1) / 2)
        self.assertAlmostEqual(x, 5.0)
        self.assertAlmostEqual(y, 2.0)

    def test_fov_map_rect(self):
        m = Minimap(0.01, origin="ll")
        u0, v0, w, h = m.fov_map_rect(1.0, 1.0, 0.4, 0.2)
        self.assertAlmostEqual(u0, 80)
        self.assertAlmostEqual(v0, -110)
        self.assertAlmostEqual(w, 40)
        self.assertAlmostEqual(h, 20)

    def test_paste_spans_tiles(self):
        m = Minimap(0.01)
        # Straddles map origin => 4 tiles
        m.paste(solid(100, 100, (1, 2, 3)), 0.0, 0.0)
        self.assertEqual(set(m.tiles.keys()), {(-1, -1), (0, -1), (-1, 0),
                                               (0, 0)})
        self.assertEqual(m.bounds(), (-50, -50, 50, 50))
        out = m.render(-60, -60, 120, 120)
        alpha = out[:, :, 3]
        self.assertEqual(int((alpha == 255).sum()), 100 * 100)
        self.assertTrue((out[10:110, 10:110, 0:3] == (1, 2, 3)).all())
        dirty = m.pop_dirty()
        self.assertEqual(len(dirty), 4)
        self.assertEqual(m.pop_dirty(), {})

    def test_overwrite(self):
        m = Minimap(0.01)
        m.paste(solid(10, 10, (1, 1, 1)), 1.0, 1.0)
        m.paste(solid(10, 10, (9, 9, 9)), 1.05, 1.0)
        u, v = m.stage_to_map(1.0, 1.0)
        out = m.render(int(u) - 5, int(v) - 5, 15, 10)
        self.assertEqual(tuple(out[0, 0, 0:3]), (1, 1, 1))
        self.assertEqual(tuple(out[0, 14, 0:3]), (9, 9, 9))

    def test_memory_cap(self):
        m = Minimap(0.01, max_bytes=4 * TILE_BYTES)
        self.assertEqual(m.max_tiles, 4)
        # One tile each, far apart
        step = TILE_PIX * 0.01 * 3
        for i in range(10):
            m.paste(solid(8, 8, (i, i, i)), i * step + 1.28, -1.28)
            self.assertLessEqual(len(m.tiles), 4)
        self.assertEqual(m.memory_bytes(), 4 * TILE_BYTES)
        self.assertEqual(m.evictions, 6)
        # Most recent kept
        u, v = m.stage_to_map(9 * step + 1.28, -1.28)
        out = m.render(int(u) - 2, int(v) - 2, 4, 4)
        self.assertEqual(tuple(out[0, 0]), (9, 9, 9, 255))
        # Evicted tiles reported as None so views can drop them
        dirty = m.pop_dirty()
        self.assertEqual(sum([1 for t in dirty.values() if t is None]), 6)

    def test_clear(self):
        m = Minimap(0.01)
        m.paste(solid(10, 10, (1, 1, 1)), 0.0, 0.0)
        m.pop_dirty()
        self.assertFalse(m.empty())
        m.clear()
        self.assertTrue(m.empty())
        self.assertIsNone(m.bounds())
        self.assertEqual(m.memory_bytes(), 0)
        dirty = m.pop_dirty()
        self.assertTrue(dirty)
        self.assertTrue(all([t is None for t in dirty.values()]))

    def test_downscale_pil(self):
        m = Minimap(0.01)
        im = Image.new("RGB", (5440, 3648), (50, 60, 70))
        small = m.downscale(im, 2.72, 1.824)
        self.assertEqual(small.shape, (182, 272, 3))
        self.assertEqual(tuple(small[5, 5]), (50, 60, 70))
        # Tiny FoV still gives at least a pixel
        small = m.downscale(im, 0.001, 0.001)
        self.assertEqual(small.shape, (1, 1, 3))


class TestMinimapStack(unittest.TestCase):
    def test_layer_per_resolution(self):
        s = MinimapStack()
        lo = s.layer(0.02)
        hi = s.layer(0.001)
        self.assertIs(s.layer(0.02), lo)
        self.assertIsNot(lo, hi)
        # Same 1 mm FoV at both resolutions
        s.paste(lo, solid(50, 50, (1, 1, 1)), 0.0, 0.0)
        s.paste(hi, solid(1000, 1000, (2, 2, 2)), 0.0, 0.0)
        # Centered on the origin => straddles tile boundaries
        self.assertEqual(len(lo.tiles), 4)
        self.assertEqual(len(hi.tiles), 16)
        x0, y0, x1, y1 = s.bounds_mm()
        for got, want in zip((x0, y0, x1, y1), (-0.5, -0.5, 0.5, 0.5)):
            self.assertAlmostEqual(got, want, delta=0.02)
        s.clear()
        self.assertTrue(s.empty())
        self.assertIsNone(s.bounds_mm())

    def test_shared_memory_cap(self):
        s = MinimapStack(max_bytes=4 * TILE_BYTES)
        a = s.layer(0.01)
        b = s.layer(0.002)
        step = TILE_PIX * 0.01 * 3
        s.paste(a, solid(8, 8, (1, 1, 1)), 1.28, -1.28)
        s.paste(b, solid(8, 8, (2, 2, 2)), 0.256, -0.256)
        s.paste(a, solid(8, 8, (3, 3, 3)), step + 1.28, -1.28)
        s.paste(b, solid(8, 8, (4, 4, 4)), 0.256 + step, -0.256)
        self.assertEqual(s.num_tiles(), 4)
        # Oldest overall (first paste, layer a) goes first
        s.paste(b, solid(8, 8, (5, 5, 5)), 0.256 + 2 * step, -0.256)
        self.assertEqual(s.num_tiles(), 4)
        self.assertEqual((len(a.tiles), len(b.tiles)), (1, 3))
        self.assertEqual(a.evictions, 1)
        self.assertEqual(s.memory_bytes(), 4 * TILE_BYTES)


if __name__ == "__main__":
    unittest.main()
