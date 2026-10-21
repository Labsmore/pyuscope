#!/usr/bin/env python3
"""Expose selected distribution Python bindings to an isolated uv environment."""

import argparse
import importlib.util
import os
import subprocess
from pathlib import Path


# Must match the apt packages in deb.containerfile and the deb Depends in
# build_deb.py: anything required here must be installed in both places
REQUIRED_MODULES = ("gi", "PyQt5")
OPTIONAL_MODULES = (
    # Only needed by gi for Gdk/Gtk, which pyuscope doesn't use
    "cairo",
    "sip",
    "pygame",
    "libcamera",
    "picamera2",
    "linuxcnc",
    "hal",
)


def environment_site_packages(environment):
    python = environment / "bin" / "python"
    if not python.is_file():
        raise SystemExit(f"Virtual environment Python not found: {python}")
    output = subprocess.check_output(
        [
            os.fspath(python),
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        text=True,
    )
    return Path(output.strip())


def module_source(name):
    spec = importlib.util.find_spec(name)
    if spec is None:
        return None
    locations = list(spec.submodule_search_locations or ())
    if locations:
        return Path(locations[0])
    if spec.origin:
        return Path(spec.origin)
    return None


def link_module(site_packages, name, required):
    source = module_source(name)
    if source is None:
        if required:
            raise SystemExit(
                f"Required system Python binding is not installed: {name}"
            )
        return False

    destination = site_packages / source.name
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() and destination.resolve() == source.resolve():
            return True
        print(f"Using environment module {name}: {destination}")
        return False

    destination.symlink_to(source, target_is_directory=source.is_dir())
    print(f"Linked system binding {name}: {source}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "environment",
        type=Path,
        help="uv virtual environment to populate",
    )
    args = parser.parse_args()

    site_packages = environment_site_packages(args.environment.resolve())
    site_packages.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_MODULES:
        link_module(site_packages, name, required=True)
    for name in OPTIONAL_MODULES:
        link_module(site_packages, name, required=False)


if __name__ == "__main__":
    main()
