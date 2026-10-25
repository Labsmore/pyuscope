#!/usr/bin/env -S uv run
"""
Run test/unit in parallel, one process per test file

Each file gets its own PYUSCOPE_DATA_DIR and a timeout
Mock hardware latency is disabled (PYUSCOPE_MOCK_LATENCY=0)

./test/run_unit.py                      # everything
./test/run_unit.py test_crop test_mock  # selected files
./test/run_unit.py -j 4 -t 60
"""

import argparse
import glob
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIT_DIR = os.path.join(ROOT, "test", "unit")


def run_one(name, timeout):
    env = dict(os.environ)
    env["PYUSCOPE_MOCK_LATENCY"] = "0"
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = os.pathsep.join(
        [ROOT, UNIT_DIR, env.get("PYTHONPATH", "")])
    tstart = time.time()
    with tempfile.TemporaryDirectory(prefix="pyuscope-test-") as data_dir:
        env["PYUSCOPE_DATA_DIR"] = data_dir
        try:
            p = subprocess.run(
                [sys.executable, "-m", "unittest", name],
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
            output = p.stdout.decode("utf-8", errors="replace")
            status = "ok" if p.returncode == 0 else "FAIL"
        except subprocess.TimeoutExpired as e:
            output = (e.stdout or b"").decode("utf-8", errors="replace")
            status = "TIMEOUT"
    dt = time.time() - tstart
    return name, status, dt, output


def summary_line(output):
    for l in reversed(output.splitlines()):
        if l.startswith("Ran ") or l.startswith("OK") or l.startswith(
                "FAILED"):
            return l
    return ""


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("-j",
                        type=int,
                        default=min(8,
                                    os.cpu_count() or 1),
                        help="Parallel test files")
    parser.add_argument("-t",
                        type=float,
                        default=120.0,
                        help="Per file timeout (seconds)")
    parser.add_argument("-v",
                        action="store_true",
                        help="Print output of passing files too")
    parser.add_argument("names",
                        nargs="*",
                        help="Test files (ex: test_crop). Default: all")
    args = parser.parse_args()

    names = args.names or sorted(
        os.path.basename(fn)[:-3]
        for fn in glob.glob(os.path.join(UNIT_DIR, "test_*.py")))
    names = [n[:-3] if n.endswith(".py") else os.path.basename(n)
             for n in names]

    tstart = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=args.j) as pool:
        futures = [
            pool.submit(run_one, name, args.t) for name in names
        ]
        for future in futures:
            name, status, dt, output = future.result()
            results.append((name, status, dt, output))
            print("%-8s %6.1fs %-28s %s" %
                  (status, dt, name, summary_line(output)))
            sys.stdout.flush()

    bad = [r for r in results if r[1] != "ok"]
    for name, status, _dt, output in results:
        if status != "ok" or args.v:
            print("")
            print("=" * 70)
            print("%s: %s" % (name, status))
            print("=" * 70)
            print(output)
    print("")
    print("%u files, %u failed, %0.1fs" %
          (len(results), len(bad), time.time() - tstart))
    slowest = sorted(results, key=lambda r: -r[2])[:3]
    print("Slowest: " + ", ".join("%s %0.1fs" % (r[0], r[2])
                                  for r in slowest))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
