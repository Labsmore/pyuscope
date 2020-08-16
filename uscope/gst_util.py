from .img_util import get_scaled

import os
from PIL import Image
import io
import threading
import traceback

gobject = None
pygst = None
gst = None
try:
    from gi.repository import GObject
    import gobject, pygst
    pygst.require('1.0')
    import gst
except ImportError:
    # XXX: why is this a warning and not an error?
    # think it was hack for CLI x-ray system?
    print("WARNING: gst import failed")
    pass


# Example sink code at
# https://coherence.beebits.net/svn/branches/xbox-branch-2/coherence/transcoder.py
class ResizeSink(gst.Element):
    # Above didn't have this but seems its not optional
    __gstdetails__ = ('ResizeSink','Sink', \
                      'Resize source to get around X11 memory limitations', 'John McMaster')

    _sinkpadtemplate = gst.PadTemplate("sinkpadtemplate", gst.PAD_SINK,
                                       gst.PAD_ALWAYS, gst.caps_new_any())

    _srcpadtemplate = gst.PadTemplate("srcpadtemplate", gst.PAD_SRC,
                                      gst.PAD_ALWAYS, gst.caps_new_any())

    def __init__(self):
        gst.Element.__init__(self)
        self.sinkpad = gst.Pad(self._sinkpadtemplate, "sink")
        self.srcpad = gst.Pad(self._srcpadtemplate, "src")
        self.add_pad(self.sinkpad)
        self.add_pad(self.srcpad)

        self.sinkpad.set_chain_function(self.chainfunc)
        self.sinkpad.set_event_function(self.eventfunc)

    def chainfunc(self, pad, buffer):
        try:
            print('Got resize buffer')
            # Simplest: just propagate the data
            # self.srcpad.push(buffer)

            # Import into PIL and downsize it
            # Raw jpeg to pr0n PIL wrapper object
            print('resize chain', len(buffer.data), len(buffer.data) / 3264.0)
            #open('temp.jpg', 'w').write(buffer.data)
            #io = StringIO.StringIO(buffer.data)
            io = io.StringIO(str(buffer))
            try:
                image = Image.open(io)
            except:
                print('failed to create image')
                return gst.FLOW_OK
            # Use a fast filter since this is realtime
            image = get_scaled(image, 0.5, Image.NEAREST)

            output = io.StringIO()
            image.save(output, 'jpeg')
            self.srcpad.push(gst.Buffer(output.getvalue()))
        except:
            traceback.print_exc()
            os._exit(1)

        return gst.FLOW_OK

    def eventfunc(self, pad, event):
        return True


# nope...
# metaclass conflict: the metaclass of a derived class must be a (non-strict) subclass of the metaclasses of all its bases
# ...and one stack overflow post later I know more about python classes than I ever wanted to
# basically magic + magic = fizzle
#class CaptureSink(gst.Element, QObject):
class CaptureSink(gst.Element):
    __gstdetails__ = ('CaptureSink','Sink', \
                      'Captures images for the CNC', 'John McMaster')

    _sinkpadtemplate = gst.PadTemplate("sinkpadtemplate", gst.PAD_SINK,
                                       gst.PAD_ALWAYS, gst.caps_new_any())

    def __init__(self):
        gst.Element.__init__(self)
        self.sinkpad = gst.Pad(self._sinkpadtemplate, "sink")
        self.add_pad(self.sinkpad)

        self.sinkpad.set_chain_function(self.chainfunc)
        self.sinkpad.set_event_function(self.eventfunc)

        self.image_requested = threading.Event()
        self.next_image_id = 0
        self.images = {}

    def request_image(self, cb):
        '''Request that the next image be saved'''
        # Later we might make this multi-image
        if self.image_requested.is_set():
            raise Exception('Image already requested')
        self.cb = cb
        self.image_requested.set()

    def get_image(self, image_id):
        '''Fetch the image but keep it in the buffer'''
        return self.images[image_id]

    def del_image(self, image_id):
        '''Delete image in buffer'''
        del self.images[image_id]

    def pop_image(self, image_id):
        '''Fetch the image and delete it form the buffer'''
        ret = self.images[image_id]
        del self.images[image_id]
        # Arbitrarily convert to PIL here
        # TODO: should pass rawer/lossless image to PIL instead of jpg?
        return Image.open(io.StringIO(ret))

    '''
    gstreamer plugin core methods
    '''

    def chainfunc(self, pad, buffer):
        #print 'Capture sink buffer in'
        try:
            '''
            Two major circumstances:
            -Imaging: want next image
            -Snapshot: want next image
            In either case the GUI should listen to all events and clear out the ones it doesn't want
            '''
            #print 'Got image'
            if self.image_requested.is_set():
                #print 'Processing image request'
                # Does this need to be locked?
                # Copy buffer so that even as object is reused we don't lose it
                # is there a difference between str(buffer) and buffer.data?
                self.images[self.next_image_id] = str(buffer)
                # Clear before emitting signal so that it can be re-requested in response
                self.image_requested.clear()
                #print 'Emitting capture event'
                self.cb(self.next_image_id)
                #print 'Capture event emitted'
                self.next_image_id += 1
        except:
            traceback.print_exc()
            os._exit(1)

        return gst.FLOW_OK

    def eventfunc(self, pad, event):
        return True


def register():
    gobject.type_register(ResizeSink)
    gst.element_register(ResizeSink, 'myresize', gst.RANK_MARGINAL)

    gobject.type_register(CaptureSink)
    # Register the element into this process' registry.
    gst.element_register(CaptureSink, 'capturesink', gst.RANK_MARGINAL)
