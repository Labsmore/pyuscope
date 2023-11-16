from uscope.util import add_bool_arg
from uscope.scan_util import index_scan_images
import os
from PIL import Image
import struct

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


def write_summary_image(iindex, output_filename=None):
    if output_filename is None:
        d = os.path.join(iindex["dir"], "summary")
        if not os.path.exists(d):
            os.mkdir(d)
        output_filename = os.path.join(iindex["dir"], "summary", "index.jpg")

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


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Test")
    add_bool_arg(parser, "--html", default=True)
    add_bool_arg(parser, "--image", default=True)
    parser.add_argument("dir_in")
    args = parser.parse_args()

    iindex = index_scan_images(args.dir_in)

    if args.html:
        write_html_viewer(iindex)

    if args.image:
        write_summary_image(iindex)


if __name__ == "__main__":
    main()
