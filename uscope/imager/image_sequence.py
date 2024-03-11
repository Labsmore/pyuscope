import threading
from PIL import Image
"""
PIL im objects are core
However EXIF and stuff isn't written until end in some API contexts
"""


class CapturedImage:
    def __init__(self, image, meta=None, exif_bytes=None, microscope=None):
        self.image = image
        self.meta = meta
        self.exif_bytes = exif_bytes
        self.microscope = microscope

    def save(self, fn, **kwargs):
        if self.exif_bytes is not None:
            kwargs["exif"] = self.exif_bytes
        self.image.save(fn, **kwargs)

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
