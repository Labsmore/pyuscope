#!/usr/bin/env -S uv run --no-project
"""Build pyuscope Debian packages for supported distributions."""

import argparse
import io
import os
import platform
import shutil
import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONTAINERFILE = ROOT / "deb.containerfile"
GST_PLUGIN_ROOT = ROOT / "gst-plugin-toupcam"

DISTRIBUTIONS = {
    "ubuntu26.04": "ubuntu:26.04",
    "ubuntu24.04": "ubuntu:24.04",
    "ubuntu22.04": "ubuntu:22.04",
    "debian12": "debian:12",
    "debian13": "debian:13",
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--distribution",
        action="append",
        choices=DISTRIBUTIONS,
        dest="distributions",
        help="Build only this distribution; may be repeated (default: all)",
    )
    parser.add_argument(
        "--engine",
        choices=("auto", "podman", "docker"),
        default="auto",
        help="Container engine (default: prefer Podman, then Docker)",
    )
    parser.add_argument(
        "--rebuild-images",
        action="store_true",
        help="Rebuild cached toolchain images",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist",
        help="Host output directory (default: ./dist)",
    )
    return parser.parse_args()


def command(*args, cwd=None):
    printable = " ".join(os.fspath(arg) for arg in args)
    print(f"+ {printable}", flush=True)
    subprocess.run(
        [os.fspath(arg) for arg in args],
        cwd=cwd,
        check=True,
    )


def select_engines(requested):
    if requested != "auto":
        if shutil.which(requested) is None:
            raise SystemExit(f"Container engine not found: {requested}")
        return [requested]
    engines = [
        candidate
        for candidate in ("podman", "docker")
        if shutil.which(candidate)
    ]
    if not engines:
        raise SystemExit("Neither Podman nor Docker was found")
    return engines


def image_exists(engine, image):
    return (
        subprocess.run(
            [engine, "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def host_platform():
    machine = platform.machine().lower()
    platforms = {
        "amd64": "linux/amd64",
        "x86_64": "linux/amd64",
        "aarch64": "linux/arm64",
        "arm64": "linux/arm64",
        "armv7l": "linux/arm/v7",
        "armv6l": "linux/arm/v6",
        "i386": "linux/386",
        "i686": "linux/386",
    }
    try:
        return platforms[machine]
    except KeyError:
        raise SystemExit(f"Unsupported host machine architecture: {machine}")


def image_name(distribution, container_platform):
    architecture = container_platform.split("/", 1)[-1].replace("/", "-")
    return f"localhost/pyuscope-deb-builder:{distribution}-{architecture}"


def build_image(
    engine, distribution, base_image, container_platform, rebuild
):
    image = image_name(distribution, container_platform)
    if rebuild or not image_exists(engine, image):
        command(
            engine,
            "build",
            "--platform",
            container_platform,
            "--build-arg",
            f"BASE_IMAGE={base_image}",
            "--tag",
            image,
            "--file",
            CONTAINERFILE,
            ROOT,
        )
    else:
        print(f"Using cached image {image}")
    return image


def selinux_enabled():
    enforce = Path("/sys/fs/selinux/enforce")
    try:
        return enforce.read_text().strip() == "1"
    except OSError:
        return False


def volume(path, container_path, read_only, use_selinux):
    options = "ro" if read_only else "rw"
    if use_selinux:
        options += ",z"
    return f"{path}:{container_path}:{options}"


def build_distribution(
    engine,
    distribution,
    image,
    output_dir,
    cache_dir,
    container_platform,
):
    use_selinux = selinux_enabled()
    distribution_cache_dir = cache_dir / distribution
    distribution_cache_dir.mkdir(parents=True, exist_ok=True)
    args = [
        engine,
        "run",
        "--rm",
        "--platform",
        container_platform,
        "--env",
        "HOME=/tmp",
        "--env",
        "UV_CACHE_DIR=/uv-cache",
        "--env",
        "UV_LINK_MODE=copy",
        "--volume",
        volume(ROOT, "/src", True, use_selinux),
        "--volume",
        volume(output_dir, "/out", False, use_selinux),
        "--volume",
        volume(
            distribution_cache_dir,
            "/uv-cache",
            False,
            use_selinux,
        ),
        "--workdir",
        "/src",
    ]
    if engine == "podman":
        args.extend(["--userns", "keep-id"])
    else:
        args.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    args.extend(
        [
            image,
            "./build_deb.py",
            "--distribution-tag",
            distribution,
            "--output-dir",
            "/out",
        ]
    )
    command(*args)


def debian_architecture():
    return subprocess.check_output(
        ["dpkg", "--print-architecture"], text=True
    ).strip()


def build_sdk_package(architecture):
    command(
        GST_PLUGIN_ROOT / "build_toupcamsdk_deb.py",
        "--architecture",
        architecture,
        cwd=GST_PLUGIN_ROOT,
    )


def build_gst_package(engine, distribution, rebuild_images):
    args = [
        GST_PLUGIN_ROOT / "build_distribution_debs.py",
        "--engine",
        engine,
        "--distribution",
        distribution,
    ]
    if rebuild_images:
        args.append("--rebuild-images")
    command(*args, cwd=GST_PLUGIN_ROOT)


def one_package(pattern, description):
    matches = sorted(pattern.parent.glob(pattern.name))
    if not matches:
        raise SystemExit(f"Could not find {description}: {pattern}")
    if len(matches) > 1:
        raise SystemExit(
            f"Found multiple candidates for {description}: "
            + ", ".join(os.fspath(match) for match in matches)
        )
    return matches[0]


def package_field(package, field):
    value = subprocess.check_output(
        ["dpkg-deb", "--field", package, field],
        text=True,
    ).strip()
    return value or "unknown"


def create_release_bundle(output_dir, distribution, architecture):
    pyuscope_deb = one_package(
        output_dir / f"pyuscope_*+{distribution}_{architecture}.deb",
        "pyuscope package",
    )
    sdk_directories = sorted(
        (GST_PLUGIN_ROOT / "dist").glob("toupcamsdk_*")
    )
    candidates = []
    for sdk_directory in sdk_directories:
        gst_packages = list(
            sdk_directory.glob(
                f"gstreamer1.0-toupcam_*+{distribution}_{architecture}.deb"
            )
        )
        sdk_packages = list(
            sdk_directory.glob(f"toupcamsdk_*_{architecture}.deb")
        )
        if len(gst_packages) == 1 and len(sdk_packages) == 1:
            candidates.append((gst_packages[0], sdk_packages[0]))
    if len(candidates) != 1:
        raise SystemExit(
            "Expected exactly one matching GStreamer/SDK package pair for "
            f"{distribution} {architecture}, found {len(candidates)}"
        )
    gst_deb, sdk_deb = candidates[0]
    git_commit = package_field(pyuscope_deb, "X-Git-Commit")
    git_tag = package_field(pyuscope_deb, "X-Git-Tag")
    git_branch = package_field(pyuscope_deb, "X-Git-Branch")
    git_dirty = package_field(pyuscope_deb, "X-Git-Dirty")
    sdk_version = package_field(sdk_deb, "Version")
    build_os = package_field(pyuscope_deb, "X-Build-OS")

    bundle_path = pyuscope_deb.with_suffix(".tar.gz")
    if bundle_path.exists():
        bundle_path.unlink()
    bundle_directory = bundle_path.name[: -len(".tar.gz")]
    install_script = """#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

shopt -s nullglob
packages=(./*.deb)
if ((${#packages[@]} == 0)); then
    echo "No .deb packages found next to install.sh" >&2
    exit 1
fi

if ((EUID == 0)); then
    sudo_command=()
elif command -v sudo >/dev/null 2>&1; then
    sudo_command=(sudo)
else
    echo "Run this script as root or install sudo" >&2
    exit 1
fi

"${sudo_command[@]}" apt-get update
"${sudo_command[@]}" apt-get install -y "${packages[@]}"
"""
    readme = f"""# pyuscope {pyuscope_deb.name}

This release bundle contains pyuscope, the Toupcam SDK, and the matching
GStreamer Toupcam plugin for {distribution} ({architecture}).

## Installation

Extract this archive, enter the extracted directory, and run:

    ./install.sh

Alternatively, install all packages manually with APT:

    sudo apt update
    sudo apt install ./*.deb

APT installs the local packages and downloads their system dependencies.
Using `dpkg -i` directly is not recommended because dpkg does not resolve
dependencies.

## Build information

- Git hash: `{git_commit}`
- Git tag: `{git_tag}`
- Branch: `{git_branch}`
- Git dirty: `{git_dirty}`
- Toupcam SDK version: `{sdk_version}`
- Architecture: `{architecture}`
- Build OS: {build_os}
"""
    with tarfile.open(bundle_path, "w:gz") as archive:
        readme_bytes = readme.encode()
        readme_info = tarfile.TarInfo(f"{bundle_directory}/README.md")
        readme_info.size = len(readme_bytes)
        readme_info.mode = 0o644
        archive.addfile(readme_info, io.BytesIO(readme_bytes))
        install_bytes = install_script.encode()
        install_info = tarfile.TarInfo(f"{bundle_directory}/install.sh")
        install_info.size = len(install_bytes)
        install_info.mode = 0o755
        archive.addfile(install_info, io.BytesIO(install_bytes))
        archive.add(
            ROOT / "VERSION.md",
            arcname=f"{bundle_directory}/VERSION.md",
        )
        for package in (pyuscope_deb, gst_deb, sdk_deb):
            archive.add(
                package,
                arcname=f"{bundle_directory}/{package.name}",
            )
    print(f"Built release bundle {bundle_path}")
    return bundle_path


def main():
    args = parse_args()
    engines = select_engines(args.engine)
    container_platform = host_platform()
    distributions = args.distributions or list(DISTRIBUTIONS)
    output_dir = args.output_dir.expanduser().resolve()
    cache_dir = ROOT / ".deb-build-cache" / "uv"
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    architecture = debian_architecture()

    print(f"Container engines: {', '.join(engines)}")
    print(f"Container platform: {container_platform}")
    print(f"Output directory: {output_dir}")
    print("\n=== Building Toupcam SDK package ===", flush=True)
    build_sdk_package(architecture)

    bundles = []
    for distribution in distributions:
        print(f"\n=== Building {distribution} ===", flush=True)
        for engine_index, engine in enumerate(engines):
            try:
                image = build_image(
                    engine,
                    distribution,
                    DISTRIBUTIONS[distribution],
                    container_platform,
                    args.rebuild_images,
                )
                build_distribution(
                    engine,
                    distribution,
                    image,
                    output_dir,
                    cache_dir,
                    container_platform,
                )
                break
            except subprocess.CalledProcessError:
                if engine_index + 1 == len(engines):
                    raise
                print(
                    f"\n{engine} failed; retrying {distribution} with "
                    f"{engines[engine_index + 1]}",
                    flush=True,
                )
        print(
            f"\n=== Building GStreamer package for {distribution} ===",
            flush=True,
        )
        build_gst_package(args.engine, distribution, args.rebuild_images)
        bundles.append(
            create_release_bundle(
                output_dir,
                distribution,
                architecture,
            )
        )

    print("\nBuilt packages:")
    for package in sorted(output_dir.glob("pyuscope_*.deb")):
        print(package)
    print("\nBuilt release bundles:")
    for bundle in bundles:
        print(bundle)


if __name__ == "__main__":
    main()
