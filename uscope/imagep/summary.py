from uscope.util import add_bool_arg
from uscope.scan_util import index_scan_images, is_tif_scan
import os
from PIL import Image
import struct
from uscope.util import readj
import math

# /usr/local/lib/python2.7/dist-packages/PIL/Image.py:2210: DecompressionBombWarning: Image size (941782785 pixels) exceeds limit of 89478485 pixels, could be decompression bomb DOS attack.
#   DecompressionBombWarning)
Image.MAX_IMAGE_PIXELS = None


class HugeImage(Exception):
    pass


class HugeJPEG(HugeImage):
    pass


class HugeTIF(HugeImage):
    pass


# FIXME: for some reason this doesn't work on .tif images
def write_html_viewer(iindex, output_filename=None):
    if output_filename is None:
        output_filename = os.path.join(iindex["dir"], "index.html")

    assert iindex[
        "flat"], "HTML viewer only supported on final level image set"

    out = """\
<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Labsmore Capture Grid View</title>
    <style>
        body {
            font-family: Arial, Helvetica, sans-serif;
            color: gray;
            background-color: #000000;
        }
        table {
            border-collapse: collapse;
        }

        table,
        th,
        td {
            border: 5px solid #000000;
        }

        th,
        td {
            padding: 0px;
            text-align: center;
        }

        img {
            width: 110px;
        }
    </style>
</head>

<body>
    <h2><center>Labsmore Grid Viewer</center></h2>
"""
    if is_tif_scan(iindex["dir"]):
        out += "WARNING: HTML viewer only works reliably with .jpg (have .tif)<br>\n"
    out += """
    <table>
        <tbody>
        """

    for row in range(iindex["rows"]):
        out += """\
            <tr>
"""
        for col in range(iindex["cols"]):
            fn_rel = iindex["crs"][(col, row)]["basename"]
            out += f"""\
            <td><img src="{fn_rel}"></td>
"""
        out += """\
            </tr>
"""
    out += """\
        </tbody>
    </table>
</body>

</html>
"""
    with open(output_filename, "w") as f:
        f.write(out)


def write_snapshot_grid(iindex, output_filename=None):
    if output_filename is None:
        d = os.path.join(iindex["dir"], "summary")
        if not os.path.exists(d):
            os.mkdir(d)
        output_filename = os.path.join(iindex["dir"], "summary", "tiles.jpg")

    assert iindex[
        "flat"], "Single image only supported on final level image set"

    print('Calculating dimensions...')

    #with Image.open(fns_in[0]) as im0:
    this0 = iindex["crs"][(0, 0)]
    im0 = Image.open(os.path.join(iindex["dir"], this0["basename"]))
    spacing = int(im0.height * 0.05)
    w = im0.size[0] * iindex["cols"] + spacing * (iindex["cols"] - 1)
    h = im0.size[1] * iindex["rows"] + spacing * (iindex["rows"] - 1)
    dst = Image.new(im0.mode, (w, h))

    if output_filename.find('.jpg') >= 0:
        if w >= 2**16 or h >= 2**16:
            raise HugeJPEG('Image exceeds maximum JPEG w/h')
        # think this was tiff, not jpg...?
        if w * h >= 2**32:
            raise HugeJPEG('Image exceeds maximum JPEG size')

    for this in iindex["images"].values():
        x = im0.width * this["col"] + spacing * this["col"]
        # lower left vs uppper left coordinate systems
        row0 = iindex["rows"] - this["row"] - 1
        y = im0.height * row0 + spacing * row0
        im = Image.open(os.path.join(iindex["dir"], this["basename"]))
        dst.paste(im, (x, y))

    print(('Saving %s...' % (output_filename, )))
    try:
        dst.save(output_filename, quality=95)
    # File "/usr/lib/python2.7/dist-packages/PIL/TiffImagePlugin.py", line 550, in _pack
    #   return struct.pack(self._endian + fmt, *values)
    # struct.error: 'L' format requires 0 <= number <= 4294967295
    except struct.error:
        try:
            os.remove(output_filename)
        except OSError:
            pass
        raise HugeTIF("Failed to save image of size %uw x %uh" % (w, h))
    print('Done!')


class QuickPano:
    def __init__(self, iindex, output_filename=None):
        self.iindex = iindex
        self.verbose = False

        if output_filename is None:
            d = os.path.join(iindex["dir"], "summary")
            if not os.path.exists(d):
                os.mkdir(d)
            output_filename = os.path.join(iindex["dir"], "summary",
                                           "quick_pano.jpg")
        self.output_filename = output_filename

    def load_scan_json(self):
        """
        Look for scan JSON in parent directory
        This might be processed data
        """
        d = self.iindex["dir"]
        while True:
            scan_fn = os.path.join(d, "uscan.json")
            if os.path.exists(scan_fn):
                break
            # assert 0, "FIXME: only support simple scans right now"
            d = os.path.dirname(d)
            if d == "/":
                raise Exception("Failed to find uscan.json")
        self.uscan = readj(scan_fn)

        self.cr2info = {}
        # assuming files keep this naming scheme should work for all files
        for info in self.uscan["files"].values():
            col = info["col"]
            row = info["row"]
            # copy x, y coordinate + other info
            thisj = dict(info)
            # FIXME: usually but not always correct
            thisj["filename"] = os.path.join(
                self.iindex["dir"], "c%03u_r%03u%s" %
                (col, row, self.uscan["image-save"]["extension"]))
            assert os.path.exists(thisj["filename"]), thisj["filename"]
            self.cr2info[(col, row)] = thisj

    def new_dst(self):
        self.verbose and print('Calculating dimensions...')

        #with Image.open(fns_in[0]) as im0:
        this0 = self.iindex["crs"][(0, 0)]
        self.im0 = Image.open(
            os.path.join(self.iindex["dir"], this0["basename"]))
        im0 = self.im0

        # Calculate max width, height
        # Note: there are some coordinate flips here, but since relative doesn't matter
        self.x0 = float("+inf")
        self.x1 = float("-inf")
        self.y0 = float("+inf")
        self.y1 = float("-inf")
        self.pixel_per_mm = 1000 / (
            self.uscan["pconfig"]["app"]["objective"]["um_per_pixel"])
        width_mm = im0.width / self.pixel_per_mm
        self.height_mm = im0.height / self.pixel_per_mm
        for info in self.cr2info.values():
            self.verbose and print(info)
            self.x0 = min(info["position"]["x"], self.x0)
            self.verbose and print('width', im0.width, im0.height)
            self.x1 = max(info["position"]["x"] + width_mm, self.x1)
            self.y0 = min(info["position"]["y"], self.y0)
            self.y1 = max(info["position"]["y"] + self.height_mm, self.y1)
        self.verbose and print(self.x0, self.x1, self.y0, self.y1)
        dx = abs(self.x1 - self.x0)
        dy = abs(self.y1 - self.y0)
        self.verbose and print(self.pixel_per_mm, dx, dy)
        global_width = int(math.ceil(dx * self.pixel_per_mm))
        global_height = int(math.ceil(dy * self.pixel_per_mm))
        self.verbose and print(
            f"Calculate image size: {global_width}w x {global_height}h")
        assert global_width > 0 and global_height > 0

        if self.output_filename.find('.jpg') >= 0:
            if global_width >= 2**16 or global_height >= 2**16:
                raise HugeJPEG('Image exceeds maximum JPEG w/h')
            # think this was tiff, not jpg...?
            if global_width * global_height >= 2**32:
                raise HugeJPEG('Image exceeds maximum JPEG size')

        self.dst = Image.new(im0.mode, (global_width, global_height))

    def image_coordinate(self, col, row):
        """
        Return image upper left "paste" coordinate
        Note image coordinate system upper left but CNC coordinate lower left
        """
        info = self.cr2info[(col, row)]
        x = int((info["position"]["x"] - self.x0) * self.pixel_per_mm)
        # y coordinate flip for differing origin
        y = int((self.y1 - info["position"]["y"] - self.height_mm) *
                self.pixel_per_mm)
        return (x, y)

    def get_overlaps(self):
        # We could also calculate this from points by regression
        point_gen = self.uscan.get("points-xy3p")
        if point_gen is None:
            point_gen = self.uscan.get("points-xy2p")
        if point_gen is None:
            print("WARNING: quick pano failed to calculate expected overlap")
            return {}
        ret = {}
        for axis in "xy":
            ret[axis] = {
                # floor better to guarantee overlap
                "overlap_pixels":
                int(point_gen["axes"][axis]["overlap_fraction"] *
                    point_gen["axes"][axis]["view_pixels"])
            }
        return ret

    def get_rotation_ccw(self):
        """
        Get the amount the image needs to be rotated CCW in order to give a square image

        Example
        If the camera is rotated -3 degrees CCW (3 degrees CW), a +3 degree correction CCW is needed
        This value will be +3 degrees
        See Image.rotate()
        """
        return self.uscan["pconfig"].get("calibration",
                                         {}).get("optics",
                                                 {}).get("rotation_ccw")

    def fill_dst_simple(self):
        """
        Paste images in simplified manner
        Directly at coordinates, no rotation / alpha required
        """
        print('"Quick pano": fast w/o rotation')
        # Fill from bottom up such that upper left is on top
        for row in range(self.iindex["rows"]):
            row = self.iindex["rows"] - row - 1
            for col in range(self.iindex["cols"]):
                col = self.iindex["cols"] - col - 1
                #for this in self.iindex["images"].values():
                # col, row = this["col"], this["row"]
                info = self.cr2info[(col, row)]
                x, y = self.image_coordinate(col, row)
                self.verbose and print(f"{row}r {col}c => {x} x {y} y")
                im = Image.open(
                    os.path.join(self.iindex["dir"], info["filename"]))
                self.dst.paste(im, (x, y))

    def fill_dst_rotate(self):
        overlaps = self.get_overlaps()
        rotation_ccw = self.get_rotation_ccw()
        # print("overlaps", overlaps)
        # Half each side of image
        # Need a few percent of overlap to ensure no gaps
        # TODO: calculate this based on rotation
        trim_x = int(overlaps["x"]["overlap_pixels"] * 0.48)
        trim_y = int(overlaps["y"]["overlap_pixels"] * 0.48)
        # print("x y", trim_x, trim_y)
        # print("rotation_ccw", rotation_ccw)
        orig_mode = self.dst.mode
        # self.dst.putalpha(255)
        self.dst = self.dst.convert('RGBA')
        print('"Quick pano": slower w/ rotation')

        # assert 0

        # Fill from bottom up such that upper left is on top
        for row in range(self.iindex["rows"]):
            print("  Row %u / %u" % (row + 1, self.iindex["rows"]))
            row = self.iindex["rows"] - row - 1
            for col in range(self.iindex["cols"]):
                col = self.iindex["cols"] - col - 1
                # print("col", col, row)
                #for this in self.iindex["images"].values():
                # col, row = this["col"], this["row"]
                info = self.cr2info[(col, row)]
                x, y = self.image_coordinate(col, row)
                self.verbose and print(f"{row}r {col}c => {x} x {y} y")
                im_orig = Image.open(
                    os.path.join(self.iindex["dir"], info["filename"]))

                im = im_orig.convert('RGBA')
                # im.putalpha(255)
                im = im.rotate(rotation_ccw, Image.BICUBIC, expand=True)
                offset_x = 0
                offset_y = 0
                """
                if rotation_ccw > 0:
                    offset_x = -int(math.sin(rotation_ccw * 3.14 / 180) * im_orig.height)
                else:
                    offset_y = -int(math.sin(-rotation_ccw * 3.14 / 180) * im_orig.width)
                print("offsets", offset_x, offset_y)
                """

                crop_x0 = 0
                crop_x1 = im.width
                crop_y0 = 0
                crop_y1 = im.height
                if col > 0:
                    crop_x0 = trim_x
                    offset_x += crop_x0
                if row > 0:
                    crop_y0 = trim_y
                    offset_y += crop_y0
                if col < self.iindex["cols"] - 1:
                    crop_x1 -= trim_x
                if row < self.iindex["rows"] - 1:
                    crop_y1 -= trim_y
                # print("crop", crop_x0, crop_y0, crop_x1, crop_y1)
                im = im.crop((crop_x0, crop_y0, crop_x1, crop_y1))
                # print("paste", (x, y), (x + offset_x, y + offset_y))

                self.dst.paste(im, (x + offset_x, y + offset_y), im)
        self.dst = self.dst.convert(orig_mode)

    def fill_dst(self):
        if self.get_rotation_ccw():
            self.fill_dst_rotate()
        else:
            self.fill_dst_simple()

    def save(self):
        self.verbose and print(('Saving %s...' % (self.output_filename, )))
        try:
            self.dst.save(self.output_filename, quality=95)
        # File "/usr/lib/python2.7/dist-packages/PIL/TiffImagePlugin.py", line 550, in _pack
        #   return struct.pack(self._endian + fmt, *values)
        # struct.error: 'L' format requires 0 <= number <= 4294967295
        except struct.error:
            try:
                os.remove(self.output_filename)
            except OSError:
                pass
            raise HugeTIF("Failed to save image of size %uw x %uh" %
                          (self.dst.width, self.dst.height))

    def run(self):
        assert self.iindex[
            "flat"], "Single image only supported on final level image set"
        self.load_scan_json()
        self.new_dst()
        self.fill_dst()
        self.save()
        self.verbose and print('Done!')


def write_quick_pano(*args, **kwargs):
    QuickPano(*args, **kwargs).run()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Test")
    add_bool_arg(parser, "--html", default=True)
    add_bool_arg(parser, "--snapshot-grid", default=True)
    add_bool_arg(parser, "--quick-pano", default=True)
    parser.add_argument("dir_in")
    args = parser.parse_args()

    iindex = index_scan_images(args.dir_in)

    if args.html:
        write_html_viewer(iindex)

    if args.snapshot_grid:
        write_snapshot_grid(iindex)

    if args.tile_image:
        write_quick_pano(iindex)


if __name__ == "__main__":
    main()
