from uscope.imager.imager_util import format_mm_3dec

import subprocess
import shutil
import threading
import traceback
import tempfile
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import glob
import os
import math
from uscope import config
import cv2
from pathlib import Path
"""
ImageProcessing plugin
"""


class IPPlugin:
    """
    Thread safe: no
    If you want to do multiple in parallel create multiple instances
    """
    def __init__(self,
                 log=None,
                 need_tmp_dir=False,
                 default_options={},
                 microscope=None):
        self.microscope = microscope
        # Set before anything that can raise so __del__ doesn't mask the error
        self.tmp_dir = None
        if not log:

            def log(s):
                print(s)

        self.verbose = False
        self.usc = config.get_usc()
        self.log = log
        self.default_options = default_options

        self.need_tmp_dir = need_tmp_dir
        if need_tmp_dir:
            self.create_tmp_dir()
        self.delete_tmp = True

    def __del__(self):
        if self.tmp_dir:
            self.tmp_dir.cleanup()
            self.tmp_dir = None

    def get_tmp_dir(self):
        assert self.tmp_dir
        return self.tmp_dir.name

    def create_tmp_dir(self):
        if self.tmp_dir:
            return
        self.tmp_dir = tempfile.TemporaryDirectory()

    def clear_tmp_dir(self):
        """
        Delete between runs
        """
        assert self.tmp_dir
        for filename in os.listdir(self.get_tmp_dir()):
            file_path = os.path.join(self.get_tmp_dir(), filename)
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)

    def run(self, data_in, data_out, options={}):
        """
        Take images in from images_in and produce one or more images_out
        data_in: dictionary of input items
            simple plugin: a single key called "images" containing a list of EtherealImageR
        data_out: dictionary of output products
            simple plugin: a single key called "image" containing a an EtherealImageW
        """
        if self.tmp_dir:
            self.clear_tmp_dir()
        try:
            self._run(data_in, data_out, options=options)
        finally:
            if self.tmp_dir:
                self.clear_tmp_dir()

    def _run(self, data_in, data_out, options={}):
        assert 0, "required"


class HDREnfusePlugin(IPPlugin):
    def __init__(self, log, default_options={}, microscope=None):
        super().__init__(log=log,
                         microscope=microscope,
                         default_options=default_options,
                         need_tmp_dir=True)
        self.enfuse = config.get_bc().enfuse_cli()

    def _run(self, data_in, data_out, options={}):
        assert self.enfuse, "Requires enfuse"
        ewf = options.get("ewf", "gaussian")
        best_effort = options.get("best_effort", False)
        out_fn = data_out["image"].get_filename()
        args = list(self.enfuse) + [
            "--output", out_fn, "--exposure-weight-function", ewf
        ]
        # 2023-10-25, quick experiment, didn't seem to work
        if 0 and ".tif" in out_fn:
            args.append("-d")
            args.append("16")
        for image_in in data_in["images"]:
            fn = image_in.get_filename()
            args.append(fn)
        self.log(" ".join(args))
        try:
            subprocess.check_call(args)
        except subprocess.CalledProcessError:
            if not best_effort:
                raise
            else:
                self.log("WARNING: ignoring exception")
                traceback.print_exc()


"""
luminance-hdr-cli -o lum.jpg -e '-2,0,+2' c005_r006_h00.jpg c005_r006_h01.jpg c005_r006_h02.jpg

The minimum EXIF tags for automated EV is something like:
-33434 (exposure time)
-34855 (ISO)
-33437 (f number)

luminance-hdr-cli --tmo ferradans --tmoFerRho 1.0 --tmoFerInvAlpha 0.5 -o out/out4.jpg *.jpg
"""


class HDRLuminancePlugin(IPPlugin):
    def __init__(self, log, default_options={}, microscope=None):
        super().__init__(log=log,
                         microscope=microscope,
                         default_options=default_options,
                         need_tmp_dir=True)

    def _run(self, data_in, data_out, options={}):
        out_fn = data_out["image"].get_filename()
        args = [
            "luminance-hdr-cli",
            "--tmo",
            "ferradans",
            "--tmoFerRho",
            "1.0",
            "--tmoFerInvAlpha",
            "0.5",
            "-o",
            out_fn,
        ]
        fns_in = [image_in.get_filename() for image_in in data_in["images"]]
        for fn in sorted(fns_in):
            args.append(fn)
        self.log(" ".join(args))
        p = subprocess.Popen(args,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        output, error = p.communicate()
        if p.returncode != 0:
            self.log("")
            self.log("luminance-hdr-cli failed")
            self.log(f"{args}")
            self.log(f"{output}")
            self.log(f"{error}")
            raise subprocess.SubprocessError(
                f"luminance-hdr-cli failed w/ code {p.returncode}")


"""
Stack using enfuse
Currently skips align
"""


class StackEnfusePlugin(IPPlugin):
    def __init__(self, log, default_options={}, microscope=None):
        super().__init__(log=log,
                         microscope=microscope,
                         default_options=default_options,
                         need_tmp_dir=True)
        # X1 has "perfect" axes
        # Other systems have a lot of jitter
        self.align_xy = self.usc.ipp.get_plugin("stack-enfuse").get(
            "align_xy", False)
        env_align_xy = os.getenv("PYUSCOPE_ENFUSE_ALIGN_XY")
        if env_align_xy:
            self.align_xy = env_align_xy == "Y"
            self.log("StackEnfusePlugin: align_xy via environment: %s" %
                     (self.align_xy))
        self.align_zoom = self.usc.ipp.get_plugin("stack-enfuse").get(
            "align_zoom", False)
        env_align_zoom = os.getenv("PYUSCOPE_ENFUSE_ALIGN_ZOOM")
        if env_align_zoom:
            self.align_zoom = env_align_zoom == "Y"
            self.log("StackEnfusePlugin: align_zoom via environment: %s" %
                     (self.align_zoom))
        self.enfuse = config.get_bc().enfuse_cli()
        self.align_image_stack = config.get_bc().align_image_stack_cli()

    def _run(self, data_in, data_out, options={}):
        assert self.enfuse, "Requires enfuse"
        if self.align_xy or self.align_zoom:
            assert self.align_image_stack, "Requires align_image_stack"
        best_effort = options.get("best_effort", False)

        def check_call(args):
            try:
                subprocess.check_call(args)
            except subprocess.CalledProcessError:
                if not best_effort:
                    raise
                else:
                    self.log("WARNING: ignoring exception")
                    traceback.print_exc()

        # Stacking can fail to align features
        # Consider what to do such as filling in a patch image
        # from the middle of the stack
        """
        align_image_stack -m -a OUT $(ls)
        -m  Optimize field of view for all images, except for first. Useful for aligning focus stacks with slightly different magnification.
            might not apply but keep for now
       -a prefix
    
        enfuse --exposure-weight=0 --saturation-weight=0 --contrast-weight=1 --hard-mask --output=baseOpt1.tif OUT*.tif
        """
        """
        tmp_dir = "/tmp/cs_auto"
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
        os.mkdir(tmp_dir)
        """

        prefix = "aligned_"
        if self.align_xy or self.align_zoom:
            # Always output as .tif
            args = list(self.align_image_stack) + [
                # is there a reason to use -i vs -x -y?
                # -x -y is more explicit, let's do that for now
                "-l",
            ]
            if self.align_xy:
                args += ["-x", "-y"]
            if self.align_zoom:
                args += ["-m"]
            args += [
                "-v", "--use-given-order", "-a",
                os.path.join(self.get_tmp_dir(), prefix)
            ]
            for image_fn in sorted(
                [imr.get_filename() for imr in data_in["images"]]):
                args.append(image_fn)
            # self.log(" ".join(args))
            check_call(args)
        else:
            for imi, image_in in enumerate(data_in["images"]):
                fn_aligned = os.path.join(self.get_tmp_dir(),
                                          prefix + "%04u.tif" % imi)
                image_in.to_filename_tif(fn_aligned)

        out_fn = data_out["image"].get_filename()
        args = list(self.enfuse) + [
            "--exposure-weight=0", "--saturation-weight=0",
            "--contrast-weight=1", "--hard-mask", "--output=" + out_fn
        ]
        # 2023-10-25, quick experiment, didn't seem to work
        if 0 and ".tif" in out_fn:
            args.append("-d")
            args.append("16")
        for fn in sorted(
                glob.glob(os.path.join(self.get_tmp_dir(), prefix + "*"))):
            args.append(fn)
        # self.log(" ".join(args))
        check_call(args)

        if self.delete_tmp:
            # Remove old files
            # This can also confuse globbing to find extra tifs
            for fn in glob.glob(os.path.join(self.get_tmp_dir(),
                                             prefix + "*")):
                os.unlink(fn)


class StabilizationPlugin(IPPlugin):
    def __init__(self, log, default_options={}, microscope=None):
        super().__init__(log=log,
                         microscope=microscope,
                         default_options=default_options,
                         need_tmp_dir=True)

    def _run(self, data_in, data_out, options={}):
        # https://stackoverflow.com/questions/34865765/finding-the-median-of-a-set-of-pictures-using-pil-and-numpy-gives-a-black-and-wh

        images_np = []
        exif = None
        for image_in in data_in["images"]:
            fn = image_in.get_filename()
            with Image.open(fn) as im:
                image_matrix = np.array(im)
                if exif is None:
                    exif = im.info.get('exif')
            images_np.append(image_matrix)

        # Works for both RGB (h, w, 3) and grayscale (h, w)
        image_stack = np.stack(images_np, axis=-1)
        median_array = np.median(image_stack, axis=-1)
        median_array = median_array.astype(np.uint8)
        median_image = Image.fromarray(median_array)
        median_image.save(data_out["image"].get_filename(),
                          quality=90,
                          exif=exif)


"""
Correct uneven illumination using a flat field mask
"""


class FFCal:
    """
    Flat field calibration tables
    Read only after construction, so a single instance is shared by all
    plugin instances (one per worker thread) that use the same cal file.
    The float64 scalar tables are large (3 x 8 bytes x pixels) and were
    previously duplicated per worker.
    """
    _cache = {}
    _cache_lock = threading.Lock()

    @classmethod
    def get(cls, fn):
        key = (os.path.realpath(fn), os.path.getmtime(fn))
        with cls._cache_lock:
            ret = cls._cache.get(key)
            if ret is None:
                ret = cls(fn)
                cls._cache[key] = ret
            return ret

    def __init__(self, fn):
        self.fn = fn
        with Image.open(fn) as ff_im:
            ff_im.load()
            self.size = ff_im.size
            ff_rband_im, ff_gband_im, ff_bband_im = ff_im.split()
        ff_rband_np = np.array(ff_rband_im)
        ff_gband_np = np.array(ff_gband_im)
        ff_bband_np = np.array(ff_bband_im)

        (self.ff_rmin, self.ff_rmax) = self.bounds_close_band(ff_rband_im)
        (self.ff_gmin, self.ff_gmax) = self.bounds_close_band(ff_gband_im)
        (self.ff_bmin, self.ff_bmax) = self.bounds_close_band(ff_bband_im)

        # Boost dim values by scalars in the range 1.0 to near 0.0
        # The lower the flat field value, the more it needs to be scaled
        # Values at max flat field value stay the same
        # Note: dead pixels always 0 not currently handled / will crash
        self.rband_scalar = self.ff_rmax / ff_rband_np
        self.gband_scalar = self.ff_gmax / ff_gband_np
        self.bband_scalar = self.ff_bmax / ff_bband_np

    @staticmethod
    def bounds_close_band(band):
        hist = band.histogram()
        width, height = band.size
        npixels = width * height
        thresh = 0.01

        low = None
        high = None
        pixels = 0
        for i, vals in enumerate(hist):
            pixels += vals
            if low is None and pixels / npixels >= thresh:
                low = i
            if high is None and pixels / npixels >= (1.0 - thresh):
                high = i
                break
        return low, high


class CorrectFF1Plugin(IPPlugin):
    def __init__(self, log, default_options={}, microscope=None):
        super().__init__(log=log,
                         microscope=microscope,
                         default_options=default_options,
                         need_tmp_dir=True)
        # Plugin is always registered
        # Maybe should have a mechanism to exclude if it can't actually run?
        self.ff = None
        self.tables = None
        try:
            self.tables = self.usc.imager.illumination_tables()
        except Exception:
            self.tables = None
        if self.tables is None and self.usc.imager.has_ff_cal():
            try:
                self.ff = FFCal.get(self.usc.imager.ff_cal_fn())
            except Exception:
                self.ff = None
            if self.ff is not None:
                self.verbose and self.log(
                    f"ff r: {self.ff.ff_rmin} : {self.ff.ff_rmax}")
                self.verbose and self.log(
                    f"ff g: {self.ff.ff_gmin} : {self.ff.ff_gmax}")
                self.verbose and self.log(
                    f"ff b: {self.ff.ff_bmin} : {self.ff.ff_bmax}")

    def npf2im(self, statef):
        #return statef, None
        rounded = np.round(statef)
        #print("row1: %s" % rounded[1])
        statei = np.array(rounded, dtype=np.uint16)
        #print(len(statei), len(statei[0]), len(statei[0]))
        height = len(statef)
        width = len(statef[0])

        # for some reason I isn't working correctly
        # only L
        # workaround by plotting manually
        im = Image.new("RGB", (width, height), "Black")
        for y, row in enumerate(statei):
            for x, val in enumerate(row):
                # this causes really weird issues if not done
                val = tuple(int(x) for x in val)
                im.putpixel((x, y), val)

        return im

    def _run(self, data_in, data_out, options={}):
        self.verbose and self.log("FF1: run")

        image_in = data_in["image"]
        im = image_in.to_mutable_im()

        if self.tables is not None:
            if im.size != self.tables.size:
                raise Exception(
                    "Calibration image size %uw x %uh but got image %uw x %uh"
                    % (self.tables.size[0], self.tables.size[1], im.width,
                       im.height))
            arr = np.array(im)
            out = self.tables.apply(arr)
            if out.ndim == 2:
                final_im = Image.fromarray(out).convert("RGB")
            else:
                if out.dtype != np.uint8:
                    scale = 255.0 / max(float(out.max()), 1.0)
                    out = np.clip(out * scale, 0, 255).astype(np.uint8)
                final_im = Image.fromarray(out)
                if final_im.mode != "RGB":
                    final_im = final_im.convert("RGB")
            final_im.save(data_out["image"].get_filename(), quality=90)
            return

        # Calibration must be loaded
        assert self.ff, "FF correction requested but no calibration loaded"

        if im.size != self.ff.size:
            raise Exception(
                "Calibration image size %uw x %uh but got image %uw x %uh" %
                (self.ff.size[0], self.ff.size[1], im.width, im.height))

        rband_im, gband_im, bband_im = im.split()
        # fixme = Image.merge("RGB", (rband_im, gband_im, bband_im))
        rband_np = np.array(rband_im)
        gband_np = np.array(gband_im)
        bband_np = np.array(bband_im)

        # Rescale based on flat field
        rband_np = np.multiply(rband_np, self.ff.rband_scalar)
        gband_np = np.multiply(gband_np, self.ff.gband_scalar)
        bband_np = np.multiply(bband_np, self.ff.bband_scalar)

        # floats aren't ideal for images
        rband_np = np.round(rband_np)
        gband_np = np.round(gband_np)
        bband_np = np.round(bband_np)
        # print("max", rband_np.max(), gband_np.max(), bband_np.max())

        rband_np = np.minimum(rband_np, 255)
        gband_np = np.minimum(gband_np, 255)
        bband_np = np.minimum(bband_np, 255)
        # print("max", rband_np.max(), gband_np.max(), bband_np.max())

        rband_np = rband_np.astype(np.uint8)
        gband_np = gband_np.astype(np.uint8)
        bband_np = bband_np.astype(np.uint8)
        # print("max", rband_np.max(), gband_np.max(), bband_np.max())

        # Convert back into more standard PIL format
        final_im_r = Image.fromarray(rband_np, "L")
        final_im_g = Image.fromarray(gband_np, "L")
        final_im_b = Image.fromarray(bband_np, "L")
        final_im = Image.merge("RGB", (final_im_r, final_im_g, final_im_b))

        final_im.save(data_out["image"].get_filename(), quality=90)


"""
Sharpen image using a kernel
"""


class CorrectSharp1Plugin(IPPlugin):
    def __init__(self, log, default_options={}, microscope=None):
        self.kernel = None
        super().__init__(log=log,
                         microscope=microscope,
                         default_options=default_options,
                         need_tmp_dir=True)
        """
        2023-08-20
        This "seemed about right" for 20x
        It's not particularly tuned
        In general I see shadows going further so shrug maybe should be bigger?
        """
        self.kernel = np.array([
            [-0.25, -0.50, -0.50, -0.50 - 0.25],  # -2
            [-0.50, -0.75, -1.00, -0.75 - 0.50],  # -3.5
            [-0.50, -1.00, 15.00, -1.00 - 0.50],  # -3.0
            [-0.50, -0.75, -1.00, -0.75 - 0.50],  # -3.5
            [-0.25, -0.50, -0.50, -0.50 - 0.25],  # -2
        ])

    def _run(self, data_in, data_out, options={}):
        assert self.kernel is not None

        print("SHARP1: run")
        pil_im = data_in["image"].to_im()
        cv_im = np.array(pil_im.convert('RGB'))[:, :, ::-1].copy()
        result = cv2.filter2D(cv_im, -1, self.kernel)
        cv2.imwrite(data_out["image"].get_filename(), result,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 90])


"""
VM1 correction plugin, type 1
VM1 has significant spread on blue
Work around this by:
-sharpen blue
-bias image towards red / blue
"""


class CorrectVM1V1Plugin(IPPlugin):
    def __init__(self, log, default_options={}, microscope=None):
        self.kernel = None
        super().__init__(log=log,
                         microscope=microscope,
                         default_options=default_options,
                         need_tmp_dir=True)
        psf_test = [
            1.000,
            2**-3,
            2**-3,
            2**-3,
            2**-3,
            2**-3,
            2**-3,
            2**-4,
            2**-4,
            2**-4,
            2**-4,
            2**-4,
            2**-4,
            2**-5,
            2**-5,
            2**-5,
        ]
        self.kernel = self.psf_to_kernel(psf_test, 9)

    def psf_to_kernel(self, psf, size):
        scalar = 1

        assert size % 2 == 1
        kernel = np.zeros((size, size), dtype=float)
        center = size // 2
        for dx in range(size // 2 + 1):
            for dy in range(size // 2 + 1):
                # print("dx", dx, "dy", dy)
                if dx == 0 or dy == 0:
                    val = -psf[(dx + dy) * scalar]
                # Interpolate
                else:
                    dist = (dx * dx + dy * dy)**0.5 * scalar
                    assert dist >= 1
                    dist1 = int(math.ceil(dist))
                    frac1 = 1.0 - (dist1 - dist)
                    dist0 = dist1 - 1
                    frac0 = 1.0 - frac1
                    val = -(psf[dist0] * frac0 + psf[dist1] * frac1)

                kernel[center + dx][center + dy] = val
                kernel[center + dx][center - dy] = val
                kernel[center - dx][center + dy] = val
                kernel[center - dx][center - dy] = val
        # Center should be weight to make a single positive image
        kernel[center][center] = 0
        kernel[center][center] = -sum(sum(kernel)) + 1
        return kernel

    def _run(self, data_in, data_out, options={}):
        assert self.kernel is not None

        self.verbose and print("VM1-1: run")
        pil_im = data_in["image"].to_im()
        cv_im = np.array(pil_im.convert('RGB'))[:, :, ::-1].copy()

        b, g, r = cv2.split(cv_im)
        corrected_b = b

        # Otherwise getting wrapping...
        b = (np.rint(corrected_b)).astype(float)

        self.verbose and print("kernel sum", sum(sum(self.kernel)))
        self.verbose and print("Running kernel")
        corrected_b = cv2.filter2D(b, -1, self.kernel)
        self.verbose and print("Scaling")
        corrected_b = np.matrix.round(corrected_b * 0.5 + r * 0.25 + g * 0.25)

        corrected_b = np.minimum(corrected_b, 255)
        corrected_b = np.maximum(corrected_b, 0)
        corrected_b = (np.rint(corrected_b)).astype(np.uint8)
        self.verbose and print("size", len(corrected_b), len(corrected_b[0]),
                               corrected_b[0][0].dtype)

        merged = cv2.merge([corrected_b, g, r])
        cv2.imwrite(data_out["image"].get_filename(), merged,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 90])


def text_wh(draw, text, font):
    """
    Pillow 10 removed ImageDraw.textsize() / ImageFont.getsize()
    textbbox() is available since Pillow 8.0
    """
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def nice_scalebar_um(target_um):
    """
    Pick the "round" scalebar length (1, 2, 5 x 10^n um) closest to target_um
    Closest is judged on a log scale so the result is within
    about 0.63x to 1.6x of the target
    """
    assert target_um > 0
    exp = math.floor(math.log10(target_um))
    candidates = [mant * 10**exp for mant in (1, 2, 5, 10)]
    return min(candidates,
               key=lambda val: abs(math.log10(val) - math.log10(target_um)))


class AnnotateScalebarPlugin(IPPlugin):
    """
    Adds a black band at the bottom of the image containing a scalebar
    Scalebar length is snapped to a round value (ex: 200 um, 0.5 mm)
    """
    # Scalebar is the round value closest to this fraction of image width
    SCALEBAR_WIDTH_FRACTION = 0.15
    # Text to display next to the scalebar. Can be overridden by:
    # microscope.j5 ipp.plugins.annotate-scalebar.text
    # per call options["text"]
    DEFAULT_TEXT = "Labsmore.com"

    def __init__(self, log, default_options={}, microscope=None):
        super().__init__(log=log,
                         microscope=microscope,
                         default_options=default_options,
                         need_tmp_dir=True)
        pconfig = self.usc.ipp.get_plugin("annotate-scalebar")
        self.text = pconfig.get("text", self.DEFAULT_TEXT)
        self.font_path = pconfig.get("font", None)
        if self.font_path is None:
            # Development checkout: fonts/ at project root
            project_path = Path(__file__).resolve().parents[2]
            self.font_path = str(project_path / "fonts" / "Roboto" /
                                 "Roboto-Regular.ttf")
        if not Path(self.font_path).is_file():
            # Plugins are always constructed (get_plugins()) even if unused
            # Don't take down the whole image processing pipeline over a font
            # ex: installed as a package w/o the fonts dir
            self.log(
                f"WARNING: scalebar font {self.font_path} not found, using default font"
            )
            self.font_path = None

    def get_font(self, size):
        if self.font_path:
            return ImageFont.truetype(self.font_path, size)
        try:
            # Pillow >= 10.1: scalable default font
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    def _run(self, data_in, data_out, options={}):
        pil_im = data_in["image"].to_im()
        if pil_im.mode != "RGB":
            pil_im = pil_im.convert("RGB")
        width, original_height = pil_im.size
        text = options.get("text", self.text)

        scalebar_x_start = int(width * 0.05)
        scalebar_y_start = int(width * 0.01)
        short_line_length = int(width * 0.025)
        # width=0 draws nothing
        line_width = max(1, int(width * 0.005))
        black_rectangle_height = int(original_height * 0.12)
        # Scale font with the band it needs to fit in
        font_size = max(10, int(black_rectangle_height * 0.25))
        additional_text_distance_from_scalebar = font_size

        # TODO: indicate how well calibrated the system is
        um_per_pixel = float(
            options.get("objective_config", {}).get("um_per_pixel", "0"))
        if um_per_pixel > 0.0:
            scalebar_um = nice_scalebar_um(width *
                                           self.SCALEBAR_WIDTH_FRACTION *
                                           um_per_pixel)
            scalebar_length = int(round(scalebar_um / um_per_pixel))
            scale_text = format_mm_3dec(scalebar_um / 1000)
        else:
            scalebar_length = int(width * self.SCALEBAR_WIDTH_FRACTION)
            scale_text = "Missing calibration"
        self.verbose and self.log(
            f"scalebar: um_per_pixel {um_per_pixel}, length {scalebar_length} px => {scale_text}"
        )

        new_image_height = original_height + black_rectangle_height
        modified_image = Image.new("RGB", (width, new_image_height),
                                   color="black")
        modified_image.paste(pil_im, (0, 0))

        draw = ImageDraw.Draw(modified_image)
        font = self.get_font(font_size)
        scalebar_y = new_image_height - black_rectangle_height // 2

        def draw_bar():
            draw.line([
                scalebar_x_start, scalebar_y, scalebar_x_start +
                scalebar_length, scalebar_y
            ],
                      fill="white",
                      width=line_width)
            draw.line([
                scalebar_x_start, scalebar_y - short_line_length,
                scalebar_x_start, scalebar_y + short_line_length
            ],
                      fill="white",
                      width=line_width)
            draw.line([
                scalebar_x_start + scalebar_length,
                scalebar_y - short_line_length,
                scalebar_x_start + scalebar_length,
                scalebar_y + short_line_length
            ],
                      fill="white",
                      width=line_width)

        def draw_scale_text():
            scale_text_width, scale_text_height = text_wh(
                draw, scale_text, font)
            # Centered over the bar, but don't run off the left edge
            scale_text_position = (
                max(
                    line_width, scalebar_x_start +
                    (scalebar_length - scale_text_width) // 2),
                scalebar_y - int(scale_text_height * 1.2) - scalebar_y_start)
            draw.text(scale_text_position, scale_text, font=font, fill="white")

        def draw_text():
            if not text:
                return
            _text_width, text_height = text_wh(draw, text, font)
            additional_text_position = (
                scalebar_x_start + scalebar_length +
                additional_text_distance_from_scalebar,
                scalebar_y - text_height // 2)
            draw.text(additional_text_position, text, font=font, fill="white")

        draw_bar()
        draw_scale_text()
        draw_text()

        modified_image.save(data_out["image"].get_filename(), quality=90)


def get_plugin_ctors():
    return {
        "stack-enfuse": StackEnfusePlugin,
        "hdr-enfuse": HDREnfusePlugin,
        "hdr-luminance": HDRLuminancePlugin,
        "stabilization": StabilizationPlugin,
        "correct-ff1": CorrectFF1Plugin,
        "correct-illumination": CorrectFF1Plugin,
        "correct-sharp1": CorrectSharp1Plugin,
        "correct-vm1v1": CorrectVM1V1Plugin,
        "annotate-scalebar": AnnotateScalebarPlugin,
    }


def get_plugins(log=None, microscope=None):
    return {
        k: v(log=log, microscope=microscope)
        for k, v in get_plugin_ctors().items()
    }
