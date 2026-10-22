"""Run a set of calibrations: collect, analyze, optionally apply."""

import os
import time

from uscope.cal.base import Quality, session_root
from uscope.cal.dataset import Dataset, microscope_meta
from uscope.cal.registry import get as get_cal
from uscope.util import datetime_file_str, writej


def order_names(names):
    """Topological order honoring Calibration.requires. Detects cycles."""
    incoming = {n: set() for n in names}
    for n in names:
        cal = get_cal(n)
        for req in cal.requires:
            if req in incoming:
                incoming[n].add(req)
    ordered = []
    ready = [n for n, deps in incoming.items() if not deps]
    while ready:
        n = ready.pop(0)
        ordered.append(n)
        for m, deps in incoming.items():
            if n in deps:
                deps.remove(n)
                if not deps and m not in ordered and m not in ready:
                    ready.append(m)
    if len(ordered) != len(names):
        leftover = [n for n in names if n not in ordered]
        raise ValueError("Cyclic or missing calibration requires: %s" % leftover)
    return ordered


class CalSession:
    def __init__(self, microscope, names, quality=Quality.FULL, path=None):
        self.microscope = microscope
        self.names = order_names(list(names))
        self.quality = quality
        if path:
            self.path = path
        else:
            self.path = os.path.join(session_root(microscope),
                                     datetime_file_str())
        os.makedirs(self.path, exist_ok=True)
        self.results = {}
        self.datasets = {}
        self._write_session()

    def _write_session(self):
        writej(os.path.join(self.path, "session.json"), {
            "schema_version": 1,
            "quality": self.quality,
            "calibrations": self.names,
            "microscope": {
                "model": self.microscope.model(),
                "serial": self.microscope.serial(),
            },
        })

    def estimate_seconds(self, ctx):
        total = 0.0
        for name in self.names:
            total += get_cal(name).estimate_seconds(ctx, self.quality)
        return total

    def preflight(self, ctx):
        checks = {}
        for name in self.names:
            checks[name] = get_cal(name).preflight(ctx)
        return checks

    def collect_one(self, ctx, name):
        cal = get_cal(name)
        ctx.check_running()
        ctx.progress(0.0, "Collecting %s" % cal.title)
        meta = microscope_meta(self.microscope, plugin=ctx.plugin)
        meta["quality"] = self.quality
        meta["params"] = {}
        ds = Dataset.create(self.path, name, meta=meta)
        t0 = time.time()
        ctx.save_origin()
        try:
            cal.collect(ctx, ds, quality=self.quality)
        finally:
            ctx.restore_origin()
        ds.finish(seconds=int(time.time() - t0))
        self.datasets[name] = ds
        return ds

    def analyze_one(self, name, log=None, ds=None):
        cal = get_cal(name)
        if ds is None:
            ds = self.datasets.get(name)
        if ds is None:
            ds = Dataset.load(os.path.join(self.path, name))
        result = cal.analyze(ds, log=log)
        self.results[name] = result
        ds.write_processed({
            "ok": result.ok,
            "message": result.message,
            "values": result.values,
            "stats": result.stats,
        })
        if result.message or result.values:
            ds.write_analysis_log(result.summary_lines())
        return result

    def apply_one(self, name, store=None):
        result = self.results.get(name)
        if result is None:
            raise ValueError("No result for %s" % name)
        store = store or self.microscope.cal_store
        source = os.path.relpath(self.path, store.dir) if store.dir else self.path
        return get_cal(name).apply(result, store, source=os.path.join(source, name))

    def run_collect_analyze(self, ctx, on_result=None):
        n = len(self.names)
        for i, name in enumerate(self.names):
            ctx.check_running()
            ctx.progress(i / max(n, 1), "Starting %s" % name)
            self.collect_one(ctx, name)
            result = self.analyze_one(name, log=ctx.log)
            if on_result:
                on_result(name, result)
        ctx.progress(1.0, "Done")
        return self.results
