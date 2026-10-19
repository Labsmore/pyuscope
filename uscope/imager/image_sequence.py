import os
import threading
from PIL import Image
"""
PIL im objects are core
However EXIF and stuff isn't written until end in some API contexts
"""


class CapturedImage:
    """
    image: 8 bit RGB PIL image. Always present so legacy consumers work
    data: optional numpy array holding the native depth / raw pixels when the
        capture pixel format is something other than 8 bit RGB
        ex: (h, w, 3) uint16 for 16 bit RGB, (h, w) uint16 for 16 bit sensor raw
    pixel_format: gstreamer format name the frame was captured as (ex: "RGB", "GRAY16_LE")
    bit_depth: significant bits per sample in data (ex: 12). None if unknown
    bayer_pattern: ex "RGGB" for raw captures from a color camera
    """
    def __init__(self,
                 image,
                 meta=None,
                 exif_bytes=None,
                 microscope=None,
                 data=None,
                 pixel_format=None,
                 bit_depth=None,
                 bayer_pattern=None):
        self.image = image
        self.meta = meta
        self.exif_bytes = exif_bytes
        self.microscope = microscope
        self.data = data
        self.pixel_format = pixel_format
        self.bit_depth = bit_depth
        self.bayer_pattern = bayer_pattern

    def has_data(self):
        """
        True if there is native depth data beyond the 8 bit RGB image
        """
        return self.data is not None

    def save(self, fn, **kwargs):
        """
        Save the image
        If native depth data is present and the format supports it
        (.png, .tif, .tiff) the full depth data is written
        Otherwise the 8 bit RGB image is written
        """
        if self.data is not None and self._save_data(fn, **kwargs):
            return
        if self.exif_bytes is not None:
            kwargs["exif"] = self.exif_bytes
        self.image.save(fn, **kwargs)

    def _save_data(self, fn, **kwargs):
        ext = os.path.splitext(fn)[1].lower()
        if ext not in (".png", ".tif", ".tiff"):
            return False
        import cv2
        data = self.data
        if data.ndim == 3:
            # OpenCV wants BGR
            data = data[:, :, ::-1]
        if not cv2.imwrite(fn, data):
            raise IOError(f"Failed to write {fn}")
        return True

    def set_meta(self, meta):
        self.meta = meta

    def set_meta_kv(self, k, v):
        if self.meta is None:
            self.meta = {}
        self.meta[k] = v

    def set_exif_bytes(self, exif_bytes):
        # FIXME: maybe better to compute on the fly
        self.exif_bytes = exif_bytes

    def exposure(self):
        # FIXME: maybe better to compute on the fly
        # return self.meta["exposure"]
        return self.microscope.imager.captured_image_exposure(self)

    @staticmethod
    def load(fn, microscope=None):
        with open(fn[0], "rb") as f:
            im_out = Image.open(f)
            im_out.load()
            return CapturedImage(image=im_out, microscope=microscope)


"""
One or more images intended to composite into a single output image snapshot

Can include:
-Single image
-Stack
-HDR image series
"""

# ended up going a different architecture
# may revisit later
'''
class ImageSequence:
    def __init__(self,
                 captured_images=None,
                 captured_image=None,
                 n_expected=None,
                 hdr=False,
                 stack=False,
                 meta=None):
        type_ = None
        if captured_image is not None:
            assert captured_images is None
            type_ = "single"
            images = [captured_image]
        elif hdr:
            type_ = "hdr"
        elif stack:
            type_ = "stack"
        assert images is not None or n_expected is not None
        if self.n_expected is not None and images is not None:
            captured_images = []
        self._captured_images = captured_images
        self.n_expected = n_expected
        assert type_ in ("single", "hdr", "stack")
        self._type = type_
        self.meta = meta

        self._all_images_event = threading.Event()
        if self.have_all_images():
            self._all_images_event.set()

    def type_(self):
        return self._type

    def is_hdr(self):
        return self.type == "hdr"

    def is_stack(self):
        return self.type == "stack"

    def have_all_images(self):
        if self.n_expected is None:
            return True
        return len(self.captured_images) == self.n_expected

    def wait_all_images(self, timeout=None):
        if timeout is None:
            # TODO: get a better estimate from somewhere
            timeout = self.n_expected * 1.2
        self._all_images_event.wait(timeout=timeout)

    def add_captured_image(self, captured_image, meta={}):
        if self.n_expected is not None:
            assert self.n_expected < len(self.captured_images)

        self._captured_image.append(captured_image)
        if self.have_all_images():
            self._all_images.set()

    def captured_images(self):
        assert self.have_all_images()
        return self._images

    def captured_image(self):
        assert self.have_all_images()
        assert len(self._images) == 1
        return self._images[0]

    def image(self):
        """
        Return PIL object for a single / simple capture
        """
        return self.captured_image().image()
'''
