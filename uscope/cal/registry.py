"""Name -> Calibration instance."""

_REGISTRY = None


def _build():
    from uscope.cal.calibrations.scale import ScaleCal
    from uscope.cal.calibrations.backlash_xy import BacklashXYCal
    from uscope.cal.calibrations.repeat_xy import RepeatXYCal
    from uscope.cal.calibrations.dark_frame import DarkFrameCal
    from uscope.cal.calibrations.flat_field import FlatFieldCal
    from uscope.cal.calibrations.usable_area import UsableAreaCal
    from uscope.cal.calibrations.exposure_linearity import ExposureLinearityCal
    from uscope.cal.calibrations.backlash_z import BacklashZCal
    from uscope.cal.calibrations.settle import SettleCal
    from uscope.cal.calibrations.parfocal import ParfocalCal

    cals = [
        ScaleCal(),
        BacklashXYCal(),
        RepeatXYCal(),
        DarkFrameCal(),
        FlatFieldCal(),
        UsableAreaCal(),
        ExposureLinearityCal(),
        BacklashZCal(),
        SettleCal(),
        ParfocalCal(),
    ]
    return {c.name: c for c in cals}


def all_cals():
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build()
    return _REGISTRY


def get(name):
    cals = all_cals()
    if name not in cals:
        raise KeyError("Unknown calibration: %s" % name)
    return cals[name]


def names():
    return list(all_cals().keys())
