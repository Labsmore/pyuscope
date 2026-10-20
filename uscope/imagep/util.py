import time
import os
import threading
from PIL import Image, UnidentifiedImageError
import subprocess
import shutil
import re
# Note: must import the submodule, the package __init__ doesn't expose it
from pyzbar import pyzbar

# Rayleigh criterion
# https://oeis.org/A245461
RC_CONST = 1.21966989


class EtherealImageR:
    """
    An image that may be on filesystem or in memory
    User tells it what it wants it will munge it into place
    Read only
    """
    def __init__(self, im=None, fn=None, meta=None):
        self.im = im
        self.fn = fn
        self.tmp_files = set()
        self.meta = meta

    def __del__(self):
        self.flush()

    def flush(self):
        """
        Remove all temporary files
        """
        for fn in self.tmp_files:
            os.unlink(fn)
        self.tmp_files.clear()

    def get_filename(self):
        """
        Return any valid filename
        """
        if self.fn:
            return self.fn
        else:
            assert 0, "FIXME"

    def to_filename(self, fn):
        """
        Make image exist at given location
        Image will be in native / existing format
        Image may be written or symlinked to
        """
        assert fn not in self.tmp_files
        if self.im:
            self.im.write(fn)
        else:
            os.symlink(self.fn, fn)
        self.tmp_files.add(fn)

    def to_filename_tif(self, fn):
        """
        Ensure resulting file is a .tif, converting if necessary
        """
        if self.fn:
            subprocess.check_call(["convert", self.fn, fn])
            assert os.path.exists(fn)
        elif self.im:
            self.im.write(fn)
        else:
            assert 0

    def release_filename(self, fn):
        """
        Return the filename allocated earlier
        """
        self.tmp_files.remove(fn)

    def to_im(self):
        """
        Return a read only PIL image
        """
        if self.im:
            return self.im
        else:
            return Image.open(self.fn)

    def to_mutable_im(self):
        """
        Return a writable PIL image
        """
        if self.im:
            return self.im.copy()
        else:
            return Image.open(self.fn)


class EtherealImageW:
    """
    An image that will be written to output
    User gives some hints as to how it would like the image to be output
    FIXME: filename centric. Allow setting an Image
    This mostly works as we currently write all intermediate images anyway
    """

    temp_images = 0

    # NOTE: this is thread safe because EtherealImageW()s are constructed in the queing process
    # from the controller
    # However it would be ideal to make this thread safe
    @staticmethod
    def get_image_id():
        EtherealImageW.temp_images += 1
        return EtherealImageW.temp_images

    def __init__(self,
                 want_dir=None,
                 want_basename=None,
                 want_fn=None,
                 want_im=False,
                 meta=None,
                 temp_dir=None):
        # for now assume will get the desired output file name
        self.im = None
        self.want_fn = None
        self.temp_filename = None
        self.want_im = False

        if want_fn:
            self.want_fn = want_fn
        elif want_dir and want_basename:
            self.want_fn = os.path.join(want_dir, want_basename)
        elif want_im:
            self.want_im = True
            assert temp_dir
            self.temp_filename = os.path.join(
                temp_dir, "ethereal_%04u.tif" % EtherealImageW.get_image_id())
            self.want_fn = self.temp_filename
        else:
            assert 0, "Unknown operating mode"
        self.meta = meta

    def get_filename(self):
        return self.want_fn

    def get_im(self):
        """
        Return the output as a fully loaded PIL image
        If the output was only wanted in memory (want_im) the backing temp
        file is deleted: intermediate images would otherwise accumulate in
        the engine temp dir for the life of the process
        """
        if self.im is not None:
            return self.im
        with open(self.want_fn, "rb") as f:
            im = Image.open(f)
            im.load()
        if self.want_im:
            self.im = im
            self.flush()
        return im

    def flush(self):
        """
        Remove the backing temp file, if any
        """
        if self.temp_filename and os.path.exists(self.temp_filename):
            os.unlink(self.temp_filename)

    def __del__(self):
        try:
            self.flush()
        except Exception:
            # Interpreter teardown
            pass


class SubtaskException(Exception):
    pass


class TaskBarrier:
    """
    Track when all allocated tasks are complete
    allocate_callback() is called from the queuing thread,
    callback() / add_exception() from worker threads
    """
    def __init__(self):
        self.ntasks_allocated = 0
        self.ntasks_completed = 0
        self.exceptions = 0
        self.cond = threading.Condition()

    def callback(self):
        with self.cond:
            self.ntasks_completed += 1
            self.cond.notify_all()

    def allocate_callback(self):
        with self.cond:
            self.ntasks_allocated += 1
        return self.callback

    def wait(self, timeout=None):
        tstart = time.time()
        with self.cond:
            while self.ntasks_allocated > self.ntasks_completed:
                remaining = None
                if timeout is not None:
                    remaining = timeout - (time.time() - tstart)
                    if remaining <= 0:
                        raise Exception("Timed out")
                self.cond.wait(remaining)
            if self.exceptions:
                raise SubtaskException("Task(s) completed with exception")

    def idle(self):
        with self.cond:
            return self.ntasks_allocated == self.ntasks_completed

    def add_exception(self):
        with self.cond:
            self.exceptions += 1


def remove_intermediate_directories(top_dir, nested_dir):
    """
    After focus stacking, etc, keep only the final output images
    """
    top_dir = os.path.realpath(top_dir)
    nested_dir = os.path.realpath(nested_dir)
    if top_dir == nested_dir:
        return
    nested_dir_rel = nested_dir.replace(top_dir + "/", "")
    # Make sure above was done correctly
    assert os.path.exists(os.path.join(top_dir, nested_dir_rel))

    # Move all files into a temp location
    tmp_dir = os.path.join(top_dir, "tmp_intermediate")
    if os.path.exists(tmp_dir):
        raise Exception("Aborted cleanup? Manual intervention required")
    orig_files = os.listdir(top_dir)
    os.mkdir(tmp_dir)
    for f in orig_files:
        src_path = os.path.join(top_dir, f)
        dst_path = os.path.join(tmp_dir, f)
        os.rename(src_path, dst_path)

    # Fixup path after move
    nest_dir = os.path.join(tmp_dir, nested_dir_rel)
    # Now move those files to the top
    for f in os.listdir(nest_dir):
        src_path = os.path.join(nest_dir, f)
        dst_path = os.path.join(top_dir, f)
        os.rename(src_path, dst_path)

    # Keep files from original top directory that aren't part of the original scan
    # log file, etc
    for f in os.listdir(tmp_dir):
        src_path = os.path.join(tmp_dir, f)
        if not os.path.isfile(src_path):
            continue
        if ".jpg" in f or ".tif" in f:
            continue
        dst_path = os.path.join(top_dir, f)
        os.rename(src_path, dst_path)

    # Finally delete the tmp directory
    shutil.rmtree(tmp_dir)


def find_qr_code_match(image, expr):
    """
    Scan image for QR code(s) and return first match
    """
    decode_results = pyzbar.decode(image)
    for decoded in decode_results:
        data = decoded.data
        try:
            m = re.match(expr, str(data, "utf-8"))
            if m:
                return m.string
        except TypeError as e:
            # Log this error?
            print("Invalid qr_regex", e)
            return None

    # No detected match
    return None


def check_valid_image_dir(iindex, quick=False):
    """
    Quick: check for 0 size only
    Slow: actually parse the image header
    """
    errors = 0
    image_dir = iindex["dir"]
    for fn_base in iindex["images"]:
        fn = os.path.join(image_dir, fn_base)
        if os.stat(fn).st_size == 0:
            errors += 1
            print(f"ERROR: bad file {fn}")
        if not quick:
            try:
                with Image.open(fn) as _im:
                    pass
            except UnidentifiedImageError as e:
                errors += 1
                print(f"ERROR: bad file {e}")

    if errors:
        raise ValueError(
            f"Directory contains invalid image files: {image_dir}")
