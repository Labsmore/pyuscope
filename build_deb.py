#!/usr/bin/env -S uv run --no-project
"""Build a distribution-specific pyuscope Debian package."""

import argparse
import gzip
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--architecture",
        help="Debian target architecture (default: host architecture)",
    )
    parser.add_argument(
        "--distribution-tag",
        help=(
            "Override the package distribution tag "
            "(default: derive it from /etc/os-release)"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist",
        help="Directory for the generated .deb (default: ./dist)",
    )
    return parser.parse_args()


def run(*args, cwd=None, env=None):
    print("+", " ".join(os.fspath(arg) for arg in args), flush=True)
    subprocess.run(
        [os.fspath(arg) for arg in args],
        cwd=cwd,
        env=env,
        check=True,
    )


def require_commands(*commands):
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise SystemExit("Missing required command(s): " + ", ".join(missing))


def host_architecture():
    return subprocess.check_output(
        ["dpkg", "--print-architecture"], text=True
    ).strip()


def os_release():
    values = {}
    try:
        lines = Path("/etc/os-release").read_text().splitlines()
    except OSError as error:
        raise SystemExit(f"Could not read /etc/os-release: {error}")
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed = shlex.split(value)
        values[key] = parsed[0] if parsed else ""
    return values


def distribution_tag(override=None):
    if override:
        tag = override
    else:
        values = os_release()
        distribution = values.get("ID")
        release = values.get("VERSION_ID")
        if not distribution or not release:
            raise SystemExit(
                "/etc/os-release must define both ID and VERSION_ID"
            )
        tag = f"{distribution}{release}"

    tag = tag.lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.]*", tag):
        raise SystemExit(
            f"Invalid distribution tag {tag!r}; use letters, numbers, and dots"
        )
    return tag


def project_version():
    pyproject = (ROOT / "pyproject.toml").read_text()
    match = re.search(
        r"(?m)^version\s*=\s*[\"']([^\"']+)[\"']\s*$", pyproject
    )
    if not match:
        raise SystemExit("Could not determine version from pyproject.toml")
    return match.group(1)


def git_build_info():
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    branch = subprocess.check_output(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    if branch == "HEAD":
        branch = "(detached)"
    tags = subprocess.check_output(
        ["git", "tag", "--points-at", "HEAD"], cwd=ROOT, text=True
    ).splitlines()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=ROOT,
            text=True,
        ).strip()
    )
    return commit, branch, ", ".join(tags) if tags else "none", dirty


def copy_release_sources(destination):
    paths = subprocess.check_output(
        [
            "git",
            "ls-files",
            "-z",
            "--",
            "app",
            "configs",
            "fonts",
            "cal",
            "uscope",
            "utils",
            "README.md",
            "LICENSE.txt",
            "VERSION.md",
        ],
        cwd=ROOT,
    ).split(b"\0")
    for raw_path in paths:
        if not raw_path:
            continue
        relative = Path(os.fsdecode(raw_path))
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)

    for name in ("pyproject.toml", "uv.lock"):
        source = ROOT / name
        if not source.is_file():
            raise SystemExit(f"Required workspace file is missing: {source}")
        shutil.copy2(source, destination / name)


def write_text(path, contents, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(mode)


def launcher(script):
    return f"""#!/bin/sh
set -e
export PYTHONPATH="/opt/pyuscope${{PYTHONPATH:+:$PYTHONPATH}}"
export PYUSCOPE_DATA_DIR="${{PYUSCOPE_DATA_DIR:-${{XDG_DATA_HOME:-$HOME/.local/share}}/pyuscope}}"
mkdir -p "$PYUSCOPE_DATA_DIR"
exec /opt/pyuscope/.venv/bin/python /opt/pyuscope/{script} "$@"
"""


def write_package_docs(package_root, version):
    doc_dir = package_root / "usr" / "share" / "doc" / "pyuscope"
    doc_dir.mkdir(parents=True)
    timestamp = format_datetime(datetime.now(timezone.utc))
    changelog = f"""pyuscope ({version}) unstable; urgency=medium

  * Build a distribution-specific pyuscope release.

 -- pyuscope maintainers <john@labsmore.com>  {timestamp}
"""
    changelog_path = doc_dir / "changelog.Debian.gz"
    with gzip.open(changelog_path, "wt", encoding="utf-8") as output:
        output.write(changelog)
    changelog_path.chmod(0o644)
    shutil.copy2(ROOT / "LICENSE.txt", doc_dir / "copyright")


def main():
    args = parse_args()
    require_commands("dpkg", "dpkg-deb", "git", "uv")

    architecture = args.architecture or host_architecture()
    current_architecture = host_architecture()
    if architecture != current_architecture:
        raise SystemExit(
            "Cross-building pyuscope is not supported: requested "
            f"{architecture}, host is {current_architecture}"
        )

    distribution = distribution_tag(args.distribution_tag)
    build_os = os_release().get("PRETTY_NAME", "unknown")
    git_commit, git_branch, git_tag, git_dirty = git_build_info()
    upstream_version = project_version()
    version = f"{upstream_version}+{distribution}"
    output_dir = args.output_dir.expanduser().resolve()
    output_path = output_dir / f"pyuscope_{version}_{architecture}.deb"

    print(f"Version:      {version}")
    print(f"Distribution: {distribution}")
    print(f"Architecture: {architecture}")

    with tempfile.TemporaryDirectory(prefix="pyuscope-deb-") as temporary:
        package_root = Path(temporary) / "package"
        application_root = package_root / "opt" / "pyuscope"
        application_root.mkdir(parents=True)
        copy_release_sources(application_root)

        virtual_environment = application_root / ".venv"
        run(
            "uv",
            "venv",
            virtual_environment,
        )
        sync_environment = os.environ.copy()
        sync_environment.pop("VIRTUAL_ENV", None)
        sync_environment["UV_PROJECT_ENVIRONMENT"] = os.fspath(
            virtual_environment
        )
        run(
            "uv",
            "sync",
            "--frozen",
            "--no-install-project",
            "--no-install-package",
            "pygame",
            cwd=application_root,
            env=sync_environment,
        )
        run(
            "/usr/bin/python3",
            application_root / "utils" / "uv_system_bindings.py",
            virtual_environment,
        )

        bin_dir = package_root / "usr" / "bin"
        write_text(bin_dir / "pyuscope", launcher("app/argus.py"), 0o755)
        write_text(
            bin_dir / "pyuscope-argus", launcher("app/argus.py"), 0o755
        )

        write_text(
            package_root / "DEBIAN" / "control",
            f"""Package: pyuscope
Version: {version}
Section: science
Priority: optional
Architecture: {architecture}
X-Build-OS: {build_os}
X-Git-Commit: {git_commit}
X-Git-Tag: {git_tag}
X-Git-Branch: {git_branch}
X-Git-Dirty: {"yes" if git_dirty else "no"}
Depends: python3, python3-gi, python3-gst-1.0, python3-pygame, python3-pyqt5, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0, gir1.2-gst-rtsp-server-1.0, libgstrtspserver-1.0-0, libzbar0, gstreamer1.0-tools, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, imagemagick
Suggests: gstreamer1.0-toupcam
Maintainer: pyuscope maintainers <john@labsmore.com>
Description: Digital microscope controller
 Graphical and command-line microscope control, automated scanning, image
 processing, and hardware integration tools.
""",
        )
        write_package_docs(package_root, version)

        package_root.chmod(0o755)
        # Skip symlinks: uv_system_bindings.py links system packages into the
        # venv and chmod would follow them into root owned /usr/lib
        for directory in (
            path
            for path in package_root.rglob("*")
            if path.is_dir() and not path.is_symlink()
        ):
            directory.chmod(0o755)
        output_dir.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            output_path.unlink()
        run(
            "dpkg-deb",
            "--build",
            "--root-owner-group",
            package_root,
            output_path,
        )
        print(f"Built {output_path}")


if __name__ == "__main__":
    main()
