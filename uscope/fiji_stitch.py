"""
Stitch a scan locally with Fiji (ImageJ) using the
"Grid/Collection stitching" plugin (Preibisch et al)

Tile positions are seeded from uscan.json stage coordinates
and then optionally refined by Fiji's phase correlation (compute_overlap)

Fiji executable resolution order:
explicit argument, PYUSCOPE_FIJI environment variable,
bc config "fiji_path", common install locations, PATH
"""

from uscope.scan_util import index_scan_images
from uscope.util import readj, writej, utcnow
from collections import OrderedDict
import os
import shutil
import subprocess

ENV_FIJI = "PYUSCOPE_FIJI"
TILE_CONFIGURATION = "TileConfiguration.txt"
# Relative to the scan directory
OUT_DIR = "fiji"
OUT_IMAGE = "stitched.tif"

# Old launcher (ImageJ-*) and Fiji 2.15+ Jaunch launcher (fiji-*)
FIJI_EXE_NAMES = (
    "ImageJ-linux64",
    "fiji-linux-x64",
    "fiji-linux-arm64",
    "ImageJ-win64.exe",
    "fiji-windows-x64.exe",
    "Contents/MacOS/ImageJ-macosx",
    "Contents/MacOS/fiji-macos",
)
FIJI_PATH_NAMES = ("ImageJ-linux64", "fiji-linux-x64", "fiji", "ImageJ")


def fiji_install_dirs():
    home = os.path.expanduser("~")
    return [
        os.path.join(home, "Fiji.app"),
        os.path.join(home, "Fiji"),
        os.path.join(home, "fiji", "Fiji.app"),
        os.path.join(home, "Applications", "Fiji.app"),
        "/opt/Fiji.app",
        "/opt/Fiji",
        "/opt/fiji/Fiji.app",
        "/opt/fiji",
        "/usr/local/Fiji.app",
        "/usr/local/fiji",
        "/Applications/Fiji.app",
    ]


def is_executable(fn):
    return bool(fn) and os.path.isfile(fn) and os.access(fn, os.X_OK)


def resolve_fiji_path(path):
    """
    Accept either the launcher itself or a Fiji.app directory
    """
    path = os.path.expanduser(path)
    if os.path.isdir(path):
        for name in FIJI_EXE_NAMES:
            fn = os.path.join(path, name)
            if is_executable(fn):
                return fn
        return None
    if is_executable(path):
        return path
    return shutil.which(path)


def config_fiji_path():
    try:
        from uscope import config
        return config.get_bc().fiji_path()
    except Exception:
        return None


def find_fiji(path=None, use_config=True, install_dirs=None, which=True):
    """
    Return the Fiji launcher executable or None if not found
    An explicitly configured path that doesn't resolve is an error
    since silently using a different Fiji would be confusing
    """
    for configured, source in (
        (path, "argument"),
        (os.getenv(ENV_FIJI), ENV_FIJI),
        (use_config and config_fiji_path(), "config fiji_path"),
    ):
        if not configured:
            continue
        ret = resolve_fiji_path(configured)
        if not ret:
            raise ValueError(f"Fiji not found at {configured} ({source})")
        return ret

    if install_dirs is None:
        install_dirs = fiji_install_dirs()
    for d in install_dirs:
        ret = resolve_fiji_path(d) if os.path.isdir(d) else None
        if ret:
            return ret

    if which:
        for name in FIJI_PATH_NAMES:
            ret = shutil.which(name)
            if ret:
                return ret
    return None


def find_uscan_json(directory):
    """
    Processed images (hdr/, stack/, ff1/, ...) are nested below the scan
    so search upward
    """
    d = os.path.realpath(directory)
    while True:
        fn = os.path.join(d, "uscan.json")
        if os.path.exists(fn):
            return fn
        parent = os.path.dirname(d)
        if parent == d:
            raise ValueError(f"Failed to find uscan.json for {directory}")
        d = parent


def find_image_dir(scan_dir):
    """
    Return the final processed image directory of a scan
    Ex: scan/hdr/stack/ff1 if that is where processing ended
    This is the deepest directory containing a flat (single image per tile)
    image set
    """
    best = None
    best_depth = -1
    for root, dirs, _files in os.walk(scan_dir):
        dirs.sort()
        # Our own output
        if os.path.basename(root) == OUT_DIR:
            dirs[:] = []
            continue
        try:
            iindex = index_scan_images(root)
        # Unrelated images (ex: summary/quick_pano.jpg)
        except (AssertionError, ValueError):
            continue
        if not iindex["images"] or not iindex["flat"]:
            continue
        depth = os.path.relpath(root, scan_dir).count(os.sep)
        if os.path.realpath(root) == os.path.realpath(scan_dir):
            depth = -0.5
        if depth > best_depth:
            best = root
            best_depth = depth
    if best is None:
        raise ValueError(
            f"No processed (single image per tile) images found in {scan_dir}"
        )
    return best


def get_pixels_per_mm(uscan):
    um_per_pixel = uscan.get("pconfig", {}).get("app", {}).get(
        "objective", {}).get("um_per_pixel")
    if um_per_pixel:
        return 1000.0 / um_per_pixel
    for k in ("points-xy3p", "points-xy2p"):
        points = uscan.get(k)
        if points:
            return points["axes"]["x"]["pixels_per_mm"]
    raise ValueError("Failed to find image scale in uscan.json")


def get_crs_mm(uscan):
    """
    Return {(col, row): (x_mm, y_mm)}
    Raw files can have several captures per tile (HDR, stack)
    which were taken at (nearly) the same xy => average them
    """
    acc = {}
    files = uscan.get("files", {})
    for info in files.values():
        if "col" not in info or "position" not in info:
            continue
        acc.setdefault((info["col"], info["row"]), []).append(
            (info["position"]["x"], info["position"]["y"]))
    if not acc:
        # Older scans / dry runs: use planned points
        for k in ("points-xy3p", "points-xy2p"):
            points = uscan.get(k)
            if points:
                for p in points["points"].values():
                    acc.setdefault((p["col"], p["row"]), []).append(
                        (p["x"], p["y"]))
                break
    if not acc:
        raise ValueError("uscan.json has no tile positions")
    ret = {}
    for cr, xys in acc.items():
        ret[cr] = (sum(xy[0] for xy in xys) / len(xys),
                   sum(xy[1] for xy in xys) / len(xys))
    return ret


def tile_positions(uscan, iindex):
    """
    Return OrderedDict basename => (x_pixel, y_pixel)
    Upper left of the first tile is at 0, 0
    Stage is y up but image is y down => flip y
    (same convention as QuickPano)
    """
    if not iindex["flat"]:
        raise ValueError("Fiji stitch requires a single image per tile")
    pixels_per_mm = get_pixels_per_mm(uscan)
    crs_mm = get_crs_mm(uscan)
    missing = sorted(set(iindex["crs"].keys()) - set(crs_mm.keys()))
    if missing:
        raise ValueError(f"Missing positions for tiles (col, row): {missing}")
    used = [crs_mm[cr] for cr in iindex["crs"].keys()]
    x0 = min(xy[0] for xy in used)
    y1 = max(xy[1] for xy in used)
    ret = OrderedDict()
    for (col, row), info in sorted(iindex["crs"].items(),
                                   key=lambda kv: (kv[0][1], kv[0][0])):
        x_mm, y_mm = crs_mm[(col, row)]
        ret[info["basename"]] = ((x_mm - x0) * pixels_per_mm,
                                 (y1 - y_mm) * pixels_per_mm)
    return ret


def format_tile_configuration(positions):
    lines = [
        "# Define the number of dimensions we are working on",
        "dim = 2",
        "",
        "# Define the image coordinates",
    ]
    for basename, (x, y) in positions.items():
        lines.append(f"{basename}; ; ({x:.3f}, {y:.3f})")
    return "\n".join(lines) + "\n"


def write_tile_configuration(image_dir, positions, fn=TILE_CONFIGURATION):
    fn_out = os.path.join(image_dir, fn)
    with open(fn_out, "w") as f:
        f.write(format_tile_configuration(positions))
    return fn_out


def macro_path(fn):
    """
    Path as an ImageJ macro string literal body
    Forward slashes work on Windows too and avoid escaping
    """
    fn = os.path.abspath(fn).replace("\\", "/")
    if "[" in fn or "]" in fn:
        # Plugin options are [bracket] delimited
        raise ValueError(f"Fiji can't handle [ or ] in path: {fn}")
    return fn.replace('"', '\\"')


def format_macro(image_dir,
                 output_fn,
                 layout_file=TILE_CONFIGURATION,
                 compute_overlap=True,
                 fusion_method="Linear Blending",
                 regression_threshold=0.30,
                 max_avg_displacement_threshold=2.50,
                 absolute_displacement_threshold=3.50):
    options = [
        "type=[Positions from file]",
        "order=[Defined by TileConfiguration]",
        f"directory=[{macro_path(image_dir)}]",
        f"layout_file=[{layout_file}]",
        f"fusion_method=[{fusion_method}]",
        f"regression_threshold={regression_threshold:.2f}",
        f"max/avg_displacement_threshold={max_avg_displacement_threshold:.2f}",
        f"absolute_displacement_threshold={absolute_displacement_threshold:.2f}",
    ]
    if compute_overlap:
        options += ["compute_overlap", "subpixel_accuracy"]
    options += [
        "computation_parameters=[Save memory (but be slower)]",
        "image_output=[Fuse and display]",
    ]
    return f"""\
// Generated by pyuscope
setBatchMode(true);
run("Grid/Collection stitching", "{" ".join(options)}");
// Color tiles are fused as a 3 channel composite
getDimensions(width, height, channels, slices, frames);
if (channels == 3) run("Stack to RGB");
saveAs("Tiff", "{macro_path(output_fn)}");
close();
print("pyuscope: stitch done");
eval("script", "System.exit(0);");
"""


def fiji_command(fiji, macro_fn, mem=None):
    ret = [fiji, "--headless", "--console"]
    if mem:
        # ex: 16g
        ret.append(f"--mem={mem}")
    ret += ["-macro", os.path.abspath(macro_fn)]
    return ret


def run_fiji(args, log=None, log_fn=None, timeout=None):
    """
    Run Fiji, echoing its output to log (and log_fn)
    Return the exit code
    """
    if log is None:
        log = print
    log("Fiji: " + " ".join(args))
    logf = open(log_fn, "w") if log_fn else None
    try:
        popen = subprocess.Popen(args,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,
                                 universal_newlines=True)
        for line in iter(popen.stdout.readline, ""):
            if logf:
                logf.write(line)
            log("Fiji: " + line.rstrip())
        popen.stdout.close()
        return popen.wait(timeout=timeout)
    finally:
        if logf:
            logf.close()


def stitch_dir(image_dir,
               out_dir=None,
               fiji=None,
               compute_overlap=True,
               fusion_method="Linear Blending",
               mem=None,
               lazy=False,
               dry=False,
               log=None,
               timeout=None,
               run=run_fiji):
    """
    Stitch a flat image directory (one image per tile)
    Write TileConfiguration.txt next to the images (required by the plugin)
    and the macro, Fiji log and stitched.tif to out_dir
    Return the stitched image filename
    """
    if log is None:
        log = print
    image_dir = os.path.realpath(image_dir)
    if out_dir is None:
        out_dir = os.path.join(image_dir, OUT_DIR)
    output_fn = os.path.join(out_dir, OUT_IMAGE)
    if lazy and os.path.exists(output_fn):
        log(f"Fiji stitch: lazy skip, already have {output_fn}")
        return output_fn

    fiji = find_fiji(fiji)
    if not fiji and not dry:
        raise ValueError(
            f"Fiji not found. Install from https://fiji.sc/ or set {ENV_FIJI} / bc config fiji_path"
        )

    iindex = index_scan_images(image_dir)
    if len(iindex["images"]) < 2:
        raise ValueError(f"Need at least 2 images to stitch in {image_dir}")
    uscan = readj(find_uscan_json(image_dir))
    positions = tile_positions(uscan, iindex)

    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
    layout_fn = write_tile_configuration(image_dir, positions)
    log(f"Fiji stitch: wrote {layout_fn} ({len(positions)} tiles)")
    macro_fn = os.path.join(out_dir, "stitch.ijm")
    with open(macro_fn, "w") as f:
        f.write(
            format_macro(image_dir,
                         output_fn,
                         compute_overlap=compute_overlap,
                         fusion_method=fusion_method))
    args = fiji_command(fiji or "fiji", macro_fn, mem=mem)
    if dry:
        log("Fiji stitch: dry run, would run: " + " ".join(args))
        return output_fn

    # Don't confuse a stale result with a new one
    if os.path.exists(output_fn):
        os.remove(output_fn)
    time_start = utcnow().isoformat()
    rc = run(args,
             log=log,
             log_fn=os.path.join(out_dir, "fiji.log"),
             timeout=timeout)
    # Fiji macro errors often still exit 0 => check for output
    if rc != 0 or not os.path.exists(output_fn):
        raise Exception(
            f"Fiji stitch failed (exit code {rc}). See {out_dir}/fiji.log")
    writej(
        os.path.join(out_dir, "fiji_stitch.json"), {
            "type": "fiji_stitch",
            "time_start": time_start,
            "time_end": utcnow().isoformat(),
            "fiji": fiji,
            "image_dir": image_dir,
            "output": output_fn,
            "compute_overlap": compute_overlap,
            "fusion_method": fusion_method,
        })
    log(f"Fiji stitch: wrote {output_fn}")
    return output_fn


def stitch_scan_dir(scan_dir, out_dir=None, **kwargs):
    """
    Stitch a completed (and processed) scan directory
    Output defaults to <scan_dir>/fiji/
    """
    image_dir = find_image_dir(scan_dir)
    if out_dir is None:
        out_dir = os.path.join(scan_dir, OUT_DIR)
    return stitch_dir(image_dir, out_dir=out_dir, **kwargs)
