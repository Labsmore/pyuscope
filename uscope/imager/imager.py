#import psutil
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

    def wh(self):
        """Return width, height"""
        raise Exception('Required %s' % type(self))

    # Must implement at least one of the following

    def get(self):
        '''Return PIL image object'''
        raise Exception('Required')

    def take(self):
        '''Take and store to internal storage'''
        raise Exception('Required')


class MockImager(Imager):

    def __init__(self, verbose=False):
        Imager.__init__(self, verbose=verbose)

    def wh(self):
        return 640, 480

    def get(self):
        # Small test image
        return {"0": Image.new("RGB", (16, 16), 'white')}
