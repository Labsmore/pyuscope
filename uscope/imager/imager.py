import time
from PIL import Image
from uscope.imager.image_sequence import CapturedImage
'''
R:127
G:103
B:129
'''

# driver does not play well with other and effectively results in a system restart
# provide some basic protection
"""
def camera_in_use():
    '''
    C:\Program Files\AmScope\AmScope\x86\scope.exe
    '''
    for p in psutil.get_process_list():
        try:
            if p.exe.find('scope.exe') >= 0:
                print 'Found process %s' % p.exe
                return True
        except:
            pass
    return False
"""


class Imager:
    def __init__(self, verbose=False):
        self.verbose = verbose
        # Used to flush pipeline with changing HDR properties
        self.last_properties_change = time.time()
        # Used for re-synchronizing after a camera disconnect
        self._t_last_restart = None

    def device_restarted(self):
        """
        The imager has had a significant reconfiguration
        Intended for a full device disconnect / renumeration recovery
        """
        self._t_last_restart = time.time()

    def since_last_restart(self):
        if self._t_last_restart is None:
            return None
        else:
            return time.time() - self._t_last_restart

    def configure(self):
        pass

    def get_sn(self):
        """Used for unit identification"""
        return None

    # Hack: control scroll is getting written directly...
    def properties_changed(self):
        self.last_properties_change = time.time()

    def since_properties_change(self):
        return time.time() - self.last_properties_change

    def wh(self):
        """Return width, height in pixels"""
        raise Exception('Required %s' % type(self))

    # Must implement at least one of the following

    def get(self):
        '''
        Return a dict of PIL image objects
        For simple imagers any key will do, but suggest "0"
        {"0": PIL}
        '''
        raise Exception('Required')

    def take(self):
        '''Take and store to internal storage'''
        raise Exception('Required')

    def remote(self):
        """Return true if the image is taken remotely and not handled here. Call take() instead of get"""
        return False

    def warm_up(self):
        pass

    def stop(self):
        pass

    def log_planner_header(self, log):
        pass

    """
    Set application specific properties like exposure time
    """

    def set_properties(self, vals):
        self.last_properties_change = time.time()
        self._set_properties(vals)

    def set_property(self, name, value):
        self.set_properties({name: value})

    def _set_properties(self, vals):
        pass

    def get_properties(self):
        # For consistency with _set_properties(), but same for now
        return self._get_properties()

    def _get_properties(self):
        return {}

    def get_property(self, name, default=None):
        return self.get_properties().get(name, default)

    def system_status_ts(self, root_status, status):
        pass

    def wait_properties(self, properties, timeout=1.0):
        """
        Wait until read_property() returns specified value
        Intended to better synchronize HDR
        """
        remaining = dict(properties)
        tstart = time.time()
        while len(remaining):
            for k, v in dict(remaining).items():
                got = self.get_property(k)
                if got == v:
                    del remaining[k]
                    continue
                if time.time() - tstart > timeout:
                    raise Exception(
                        f"Timed out waiting for properties to change: {remaining}"
                    )
                time.sleep(0.01)


class MockImager(Imager):
    def __init__(self, verbose=False, width=640, height=480):
        Imager.__init__(self, verbose=verbose)
        self.width = width
        self.height = height
        self.loopback_int = 123
        self.loopback_float = 456.7
        self.loopback_str = "hello, world!"

    def wh(self):
        return self.width, self.height

    def get(self):
        # Small test image
        return CapturedImage(
            im=Image.new("RGB", (self.width, self.height), 'white'))

    def _set_properties(self, vals):
        for k, v in vals.items():
            if k == "loopback_int":
                self.loopback_int = int(v)
            elif k == "loopback_float":
                self.loopback_float = float(v)
            elif k == "loopback_str":
                self.loopback_str = str(v)
            else:
                raise ValueError(f"bad property {k}")

    def _get_properties(self):
        return {
            "loopback_int": self.loopback_int,
            "loopback_float": self.loopback_float,
            "loopback_str": self.loopback_str,
        }


"""
class ImageProcessor:
    def __init__(self):
        # In many configurations we are scaling output
        self.scaling = False
        self._scalar = None

		self.hdring = False
		self.

    def scalar(self):
        if not self.scaling:
            return 1.0
        else:
            return self._scalar
"""
