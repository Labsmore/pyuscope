from collections import OrderedDict
import glob
import os
import re


def iindex_filename_key(filename):
    filename = os.path.basename(filename)
    return filename.split(".")[0]


def reduce_iindex_filename(filename, remove_key):
    """
    Given a list of shared filenames, return the next base filename
    ex: 
    filenames: c000_r002_z00.jpg, c000_r002_z01.jpg, c000_r002_z02.jpg
    remove_key: stack
    return: c000_r002
    """
    parsed = iindex_parse_fn(filename)
    return unkey_fn_prefix(parsed, remove_key)


def unkey_fn_prefix(filev, remove_key):
    ret = f"{filev['col_str']}_{filev['row_str']}"
    if "stack" in filev and remove_key != "stack":
        ret += f"_{filev['stack_str']}"
    if "hdr" in filev and remove_key != "hdr":
        ret += f"_{filev['hdr_str']}"
    if "stabilization" in filev and remove_key != "stabilization":
        ret += f"_{filev['stabilization_str']}"
    return ret


# buckets = bucket_group(iindex_in, "stack")
def bucket_group(iindex_in, bucket_key):
    # Bucket [fn_base][exposures]
    fns = OrderedDict()
    for fn, filev in iindex_in["images"].items():
        # assert image_suffix == filev['extension']
        fn_prefix = unkey_fn_prefix(filev, bucket_key)
        fns.setdefault(fn_prefix, {})[filev[bucket_key]] = fn
    return fns


def iindex_parse_fn(basename):
    """
    Nobody is going to be impressed with my regular expression skills
    but this should work...for now
    2023-10-23: collapsing under its own weight
    Rewrite this to be more proper
    """
    ret = {}
    ret["basename"] = basename
    parts, extension = basename.split(".")
    ret["extension"] = "." + extension
    for part in parts.split("_"):
        m = re.match(r"c([0-9]+)", part)
        if m:
            ret["col"] = int(m.group(1))
            ret["col_str"] = part
            continue

        m = re.match(r"r([0-9]+)", part)
        if m:
            ret["row"] = int(m.group(1))
            ret["row_str"] = part
            continue

        m = re.match(r"h([0-9]+)", part)
        if m:
            ret["hdr"] = int(m.group(1))
            ret["hdr_str"] = part
            continue

        m = re.match(r"z([0-9]+)", part)
        if m:
            ret["stack"] = int(m.group(1))
            ret["stack_str"] = part
            continue

        m = re.match(r"is([0-9]+)", part)
        if m:
            ret["stabilization"] = int(m.group(1))
            ret["stabilization_str"] = part
            continue

        # Should we allow non-confirming files?
        # return None
        assert 0, f"Unrecognized part {part} in basename {basename}"

    assert "row" in ret, basename
    assert "col" in ret, basename

    return ret


def index_scan_images(dir_in):
    """
    Return dict of image_name to
    {
        # Max number of elements
        "hdrs": 2,
        "stacks": 3,

        "images": {
            "c000_r028_h01.jpg": {
                "hdr": 1,
                "col": 0,
                "row": 28,
                "extension": ".jpg",
            },
            "c001_r000_z01_h02.tif": {
                "hdr": 2,
                "stack": 1,
                "col": 0,
                "row": 28,
                "extension": ".tif",
            },
        },
    }
    """
    ret = OrderedDict()
    images = OrderedDict()
    cols = 0
    rows = 0
    hdrs = 0
    stacks = 0
    stabilization = 0
    crs = OrderedDict()
    for fn_full in sorted(
            list(glob.glob(dir_in + "/*.jpg")) +
            list(glob.glob(dir_in + "/*.tif"))):
        basename = os.path.basename(fn_full)

        v = iindex_parse_fn(basename)
        if not v:
            continue
        images[basename] = v
        crs[(v.get("col"), v.get("row"))] = v
        stabilization = max(stabilization, v.get("stabilization", -1) + 1)
        hdrs = max(hdrs, v.get("hdr", -1) + 1)
        stacks = max(stacks, v.get("stack", -1) + 1)
        rows = max(rows, v.get("row") + 1)
        cols = max(cols, v.get("col") + 1)

    # xxx: maybe this removes /
    # yes
    working_dir = os.path.realpath(dir_in)
    # while working_dir[-1] == "/":
    #    working_dir = working_dir[0:len(working_dir) - 1]

    ret["dir"] = working_dir
    ret["images"] = images
    ret["crs"] = crs
    ret["stabilization"] = stabilization
    ret["hdrs"] = hdrs
    ret["stacks"] = stacks
    ret["flat"] = stacks == 0 and hdrs == 0 and stabilization == 0
    ret["cols"] = cols
    ret["rows"] = rows
    return ret
