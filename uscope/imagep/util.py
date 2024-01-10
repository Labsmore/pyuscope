import time
import os
from PIL import Image
import subprocess
import tempfile
import shutil

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

        tempfile.TemporaryDirectory()

        if want_fn:
            self.want_fn = want_fn
        elif want_dir and want_basename:
            self.want_fn = os.path.join(want_dir, want_basename)
        elif want_im:
            self.want_im = True
            assert temp_dir
            self.temp_filename = os.path.join(
                temp_dir, "ethereal_%u04u.tif" % EtherealImageW.get_image_id())
            self.want_fn = self.temp_filename
        else:
            assert 0, "Unknown operating mode"
        self.meta = meta

    def get_filename(self):
        return self.want_fn

    def get_im(self):
        return Image.open(self.want_fn)


class TaskBarrier:
    """
    Track when all allocated tasks are complete
    TODO: consider an event to wake up for faster response
    """
    def __init__(self):
        self.ntasks_allocated = 0
        self.ntasks_completed = 0

    def callback(self):
        self.ntasks_completed += 1

    def allocate_callback(self):
        self.ntasks_allocated += 1
        return self.callback

    def wait(self, timeout=None):
        tstart = time.time()
        while self.ntasks_allocated > self.ntasks_completed:
            if timeout and time.time() - tstart > timeout:
                raise Exception("Timed out")
            time.sleep(0.1)

    def idle(self):
        return self.ntasks_allocated == self.ntasks_completed


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
