"""
Microscope calibration: store, datasets, collect/analyze/apply.

Nominal values in configs/<model>/microscope.j5 are always the default.
Measured values live in data/microscopes/<model>_sn-<sn>/calibration.j5
and overlay the nominals only when present and enabled.
Every calibration step is optional.
"""

from uscope.cal.store import CalStore, CalEntry, SCHEMA_VERSION

__all__ = ["CalStore", "CalEntry", "SCHEMA_VERSION"]
