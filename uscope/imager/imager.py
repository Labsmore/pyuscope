import time
from PIL import Image
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

    def system_status_ts(self, status):
        pass


class MockImager(Imager):
    def __init__(self, verbose=False, width=640, height=480):
        Imager.__init__(self, verbose=verbose)
        self.width = width
        self.height = height

    def wh(self):
        return self.width, self.height

    def get(self):
        # Small test image
        return {"0": Image.new("RGB", (self.width, self.height), 'white')}


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
