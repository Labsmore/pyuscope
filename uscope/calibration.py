"""
VM1 cal plate
Must be used at 20x
"""

from uscope.instrument import Instrument
from uscope.motion.motion_util import parse_move


class LIACal2(Instrument):
    def __init__(self, microscope):
        super().__init__(microscope=microscope)
        self.pos_fid70 = None
        self.pos_fid150 = None

    def name(self):
        return "LIA-CAL2"

    def cache_load(self, j):
        self.pos_fid70 = parse_move(
            j.get("pos_fid70", "X29.120 Y124.925 Z-28.426"))
        self.pos_fid150 = parse_move(
            j.get("pos_fid150", "X29.181 Y140.909 Z-28.526"))

    def cache_save(self):
        return {}

    def assert_offsets(self):
        obj = self.microscope.get_active_objective()
        assert (
            self.microscope.name == "lip-vm1" and obj == "L Plan 20X"
        ), f"FIXME: only support VM1 @ 20x, got {self.microscope.name}, {obj}"

    def fid70_go(self, **kwargs):
        """
        Go to the 70 um spot
        """
        self.assert_offsets()
        self.microscope.motion_ts().move_absolute(self.pos_fid70, **kwargs)

    def fd150_go(self, **kwargs):
        """
        Go to the 150 um spot
        """
        self.assert_offsets()
        self.microscope.motion_ts().move_absolute(self.pos_fid150, **kwargs)


def get_cal(microscope):
    ret = microscope.get_instrument_default("LIA-CAL2")
    if ret is not None:
        return ret
    ret = LIACal2(microscope)
    microscope.add_instrument(ret)
    return ret


def find_centroid(im, show=None):
    from uscope.cal.analysis.centroid import find_centroid as _find
    return _find(im, show=show)


def find_centroid_fn(fn, show=None):
    from uscope.cal.analysis.centroid import find_centroid_fn as _find
    return _find(fn, show=show)
