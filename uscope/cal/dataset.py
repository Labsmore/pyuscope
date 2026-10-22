"""
On-disk calibration dataset: manifest + lossless images.

Layout:
  <session>/<calname>/
    manifest.json
    measurements.csv   (optional)
    images/...
    processed.json     (analyze output)
    analysis.txt
"""

import csv
import os

from uscope.util import writej, readj, utcnow
from uscope import version


def _version_str():
    try:
        return version.get_meta().get("description") or "4.7.0"
    except Exception:
        return "4.7.0"


class Dataset:
    def __init__(self, path, manifest=None):
        self.path = path
        self.manifest = manifest or {}
        self.images_dir = os.path.join(path, "images")

    @classmethod
    def create(cls, session_dir, name, meta=None):
        path = os.path.join(session_dir, name)
        os.makedirs(os.path.join(path, "images"), exist_ok=True)
        manifest = {
            "schema_version": 1,
            "calibration": name,
            "pyuscope_version": _version_str(),
            "started_at": utcnow().isoformat(),
            "captures": [],
        }
        if meta:
            manifest.update(meta)
        ds = cls(path, manifest)
        ds.save_manifest()
        return ds

    @classmethod
    def load(cls, path):
        manifest = readj(os.path.join(path, "manifest.json"))
        return cls(path, manifest)

    def save_manifest(self):
        writej(os.path.join(self.path, "manifest.json"), self.manifest)

    def capture_path(self, basename):
        if not os.path.exists(self.images_dir):
            os.makedirs(self.images_dir)
        return os.path.join(self.images_dir, basename)

    def add_capture(self, basename, tags=None, image=None, captured=None):
        """Record a capture. Optionally save image or CapturedImage."""
        fn = self.capture_path(basename)
        if captured is not None:
            captured.save(fn)
        elif image is not None:
            image.save(fn)
        rec = {"fn": "images/%s" % basename}
        if tags:
            rec.update(tags)
        self.manifest.setdefault("captures", []).append(rec)
        self.save_manifest()
        return fn

    def capture_abspath(self, rec_or_fn):
        fn = rec_or_fn["fn"] if isinstance(rec_or_fn, dict) else rec_or_fn
        if os.path.isabs(fn):
            return fn
        return os.path.join(self.path, fn)

    def captures(self, **match):
        """Return captures whose tags contain match."""
        out = []
        for rec in self.manifest.get("captures") or []:
            if all(rec.get(k) == v for k, v in match.items()):
                out.append(rec)
        return out

    def write_measurements(self, rows, fieldnames=None):
        if not rows:
            return
        if fieldnames is None:
            fieldnames = list(rows[0].keys())
        fn = os.path.join(self.path, "measurements.csv")
        with open(fn, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow(row)

    def write_processed(self, pj):
        writej(os.path.join(self.path, "processed.json"), pj)

    def read_processed(self):
        fn = os.path.join(self.path, "processed.json")
        if not os.path.exists(fn):
            return None
        return readj(fn)

    def write_analysis_log(self, lines):
        fn = os.path.join(self.path, "analysis.txt")
        with open(fn, "w") as f:
            if isinstance(lines, str):
                f.write(lines)
                if not lines.endswith("\n"):
                    f.write("\n")
            else:
                f.write("\n".join(lines) + "\n")

    def finish(self, seconds=None, extra=None):
        self.manifest["finished_at"] = utcnow().isoformat()
        if seconds is not None:
            self.manifest["seconds"] = seconds
        if extra:
            self.manifest.update(extra)
        self.save_manifest()


def microscope_meta(microscope, plugin=None):
    """Snapshot of microscope + current imager/objective for a manifest."""
    meta = {}
    if microscope is not None:
        meta["microscope"] = {
            "model": microscope.model(),
            "serial": microscope.serial(),
        }
        try:
            obj = None
            if plugin is not None:
                obj = plugin.get_objective_config()
            elif hasattr(microscope, "get_active_objective"):
                name = microscope.get_active_objective()
                if name:
                    obj = microscope.get_objectives().get_config(name)
            if obj:
                meta["objective"] = {
                    "name": obj.get("name"),
                    "magnification": obj.get("magnification"),
                    "na": obj.get("na"),
                    "um_per_pixel": obj.get("um_per_pixel"),
                    "x_view": obj.get("x_view"),
                    "y_view": obj.get("y_view"),
                }
        except Exception:
            pass
        try:
            imager = microscope.usc.imager
            wh = imager.raw_wh()
            meta["imager"] = {
                "source": imager.source(),
                "width": wh[0],
                "height": wh[1],
                "crop": imager.crop_tblr(),
            }
        except Exception:
            pass
        try:
            meta["motion"] = {
                "backlash": microscope.usc.motion.backlash(),
                "backlash_compensation":
                microscope.usc.motion.backlash_compensation(),
            }
        except Exception:
            pass
    return meta
