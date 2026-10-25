"""
VM1 cal plate
Must be used at 20x
"""

from uscope.motion.motion_util import parse_move


class LIACal2:
    def __init__(self, microscope):
        self.microscope = microscope
        self.pos_fid70 = parse_move("X29.120 Y124.925 Z-28.426")
        self.pos_fid150 = parse_move("X29.181 Y140.909 Z-28.526")

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
    ret = getattr(microscope, "_lia_cal2", None)
    if ret is None:
        ret = LIACal2(microscope)
        microscope._lia_cal2 = ret
    return ret


def find_centroid(im, show=None):
    from uscope.cal.analysis.centroid import find_centroid as _find
    return _find(im, show=show)


def find_centroid_fn(fn, show=None):
    from uscope.cal.analysis.centroid import find_centroid_fn as _find
    return _find(fn, show=show)
