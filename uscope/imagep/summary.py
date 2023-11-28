from uscope.util import add_bool_arg
from uscope.scan_util import index_scan_images
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


def write_tile_image(iindex, output_filename=None):
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
            scan_fn = os.path.dirname(d)
            if scan_fn == "/":
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

    def fill_dst(self):
        # Fill from bottom up such that upper left is on top
        for row in range(self.iindex["cols"]):
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
    add_bool_arg(parser, "--tile-image", default=True)
    add_bool_arg(parser, "--quick-pano", default=True)
    parser.add_argument("dir_in")
    args = parser.parse_args()

    iindex = index_scan_images(args.dir_in)

    if args.html:
        write_html_viewer(iindex)

    if args.tile_image:
        write_tile_image(iindex)

    if args.tile_image:
        write_quick_pano(iindex)


if __name__ == "__main__":
    main()
