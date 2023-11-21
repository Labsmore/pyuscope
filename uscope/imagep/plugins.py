import subprocess
import shutil
import traceback
import tempfile
from PIL import Image
import numpy as np
import glob
import os
import math
from uscope import config
import cv2
"""
ImageProcessing plugin
"""


class IPPlugin:
    """
    Thread safe: no
    If you want to do multiple in parallel create multiple instances
    """
    def __init__(self, log=None, need_tmp_dir=False, default_options={}):
        if not log:

            def log(s):
                print(s)

        self.verbose = False
        self.usc = config.get_usc()
        self.log = log
        self.default_options = default_options

        self.tmp_dir = None
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
    def __init__(self, log, default_options={}):
        super().__init__(log=log,
                         default_options=default_options,
                         need_tmp_dir=True)
        self.enfuse = config.get_bc().enfuse_cli()

    def _run(self, data_in, data_out, options={}):
        assert self.enfuse, "Requires enfuse"
        ewf = options.get("ewf", "gaussian")
        best_effort = options.get("best_effort", False)
        out_fn = data_out["image"].get_filename()
        args = [
            "enfuse", "--output", out_fn, "--exposure-weight-function", ewf
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
Stack using enfuse
Currently skips align
"""


class StackEnfusePlugin(IPPlugin):
    def __init__(self, log, default_options={}):
        super().__init__(log=log,
                         default_options=default_options,
                         need_tmp_dir=True)
        # X1 has "perfect" axes
        # Other systems have a lot of jitter
        self.align = self.usc.ipp.get_plugin("stack-enfuse").get(
            "align", False)
        self.enfuse = config.get_bc().enfuse_cli()
        self.align_image_stack = config.get_bc().align_image_stack_cli()

    def _run(self, data_in, data_out, options={}):
        assert self.enfuse, "Requires enfuse"
        if self.align:
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
        if self.align:
            # Always output as .tif
            args = list(self.align_image_stack) + [
                # is there a reason to use -i vs -x -y?
                # -x -y is more explicit, let's do that for now
                "-l",
                "-x",
                "-y",
                "-v",
                "--use-given-order",
                "-a",
                os.path.join(self.get_tmp_dir(), prefix)
            ]
            for image_in in data_in["images"]:
                args.append(image_in.get_filename())
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
        for fn in glob.glob(os.path.join(self.get_tmp_dir(), prefix + "*")):
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
    def __init__(self, log, default_options={}):
        super().__init__(log=log,
                         default_options=default_options,
                         need_tmp_dir=True)

    def _run(self, data_in, data_out, options={}):
        # https://stackoverflow.com/questions/34865765/finding-the-median-of-a-set-of-pictures-using-pil-and-numpy-gives-a-black-and-wh

        images_np = []
        for image_in in data_in["images"]:
            fn = image_in.get_filename()
            image_matrix = np.array(Image.open(fn))
            images_np.append(image_matrix)

        image_stack = np.concatenate([im[..., None] for im in images_np],
                                     axis=3)
        median_array = np.median(image_stack, axis=3)
        median_array = median_array.astype(np.uint8)
        median_image = Image.fromarray(median_array)
        median_image.save(data_out["image"].get_filename(), quality=90)


"""
Correct uneven illumination using a flat field mask
"""


class CorrectFF1Plugin(IPPlugin):
    def __init__(self, log, default_options={}):
        super().__init__(log=log,
                         default_options=default_options,
                         need_tmp_dir=True)
        # Plugin is always registered
        # Maybe should have a mechanism to exclude if it can't actually run?
        self.ff_im = None

        if self.usc.imager.has_ff_cal():
            self.ff_im = Image.open(self.usc.imager.ff_cal_fn())
            self.ff_rband_im, self.ff_gband_im, self.ff_bband_im = self.ff_im.split(
            )
            self.ff_rband_np = np.array(self.ff_rband_im)
            self.ff_gband_np = np.array(self.ff_gband_im)
            self.ff_bband_np = np.array(self.ff_bband_im)
            self.ff_minmax()

            # Boost dim values by scalars in the range 1.0 to near 0.0
            # The lower the flat field value, the more it needs to be scaled
            # Values at max flat field value stay the same
            # Note: dead pixels always 0 not currently handled / will crash
            self.rband_scalar = self.ff_rmax / self.ff_rband_np
            self.gband_scalar = self.ff_gmax / self.ff_gband_np
            self.bband_scalar = self.ff_bmax / self.ff_bband_np

            # It's easy to have an outlier that boosts everything
            self.verbose and print(f"ffi r: {self.ffi_rmin} : {self.ffi_rmax}")
            self.verbose and print(f"ffi g: {self.ffi_gmin} : {self.ffi_gmax}")
            self.verbose and print(f"ffi b: {self.ffi_bmin} : {self.ffi_bmax}")

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

    def ff_minmax(self):
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

        (self.ff_rmin, self.ff_rmax) = bounds_close_band(self.ff_rband_im)
        (self.ff_gmin, self.ff_gmax) = bounds_close_band(self.ff_gband_im)
        (self.ff_bmin, self.ff_bmax) = bounds_close_band(self.ff_bband_im)

    def _run(self, data_in, data_out, options={}):
        # Calibration must be loaded
        assert self.ff_im

        print(f"FF1: run")

        self.verbose and print("")

        image_in = data_in["image"]
        # im_in = "cal/cal06_ff_1.5x/2023-06-20_01-22-25_blue_20x_cal6_1.5x_pic/c000_r001.jpg"
        im = image_in.to_mutable_im()
        if im.size != self.ff_im.size:
            raise Exception(
                "Calibration image size %uw x %uh but got image %uw x %uh" %
                (self.ff_im.width, self.ff_im.height, im.width, im.height))

        rband_im, gband_im, bband_im = im.split()
        # fixme = Image.merge("RGB", (rband_im, gband_im, bband_im))
        rband_np = np.array(rband_im)
        gband_np = np.array(gband_im)
        bband_np = np.array(bband_im)

        # Rescale based on flat field
        rband_np = np.multiply(rband_np, self.rband_scalar)
        gband_np = np.multiply(gband_np, self.gband_scalar)
        bband_np = np.multiply(bband_np, self.bband_scalar)

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
    def __init__(self, log, default_options={}):
        self.kernel = None
        super().__init__(log=log,
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

        print(f"SHARP1: run")
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
    def __init__(self, log, default_options={}):
        self.kernel = None
        super().__init__(log=log,
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

        print(f"VM1-1: run")
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


def get_plugin_ctors():
    return {
        "stack-enfuse": StackEnfusePlugin,
        "hdr-enfuse": HDREnfusePlugin,
        "stabilization": StabilizationPlugin,
        "correct-ff1": CorrectFF1Plugin,
        "correct-sharp1": CorrectSharp1Plugin,
        "correct-vm1v1": CorrectVM1V1Plugin,
    }


def get_plugins(log=None):
    return {k: v(log=log) for k, v in get_plugin_ctors().items()}
