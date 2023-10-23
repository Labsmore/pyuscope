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
    m = re.match(r"c([0-9]+)_r([0-9]+)_z([0-9]+)_h([0-9]+)(\.[a-z]+)",
                 basename)
    if m:
        return {
            "col": int(m.group(1)),
            "col_str": "c" + m.group(1),
            "row": int(m.group(2)),
            "row_str": "r" + m.group(2),
            "stack": int(m.group(3)),
            "stack_str": "z" + m.group(3),
            "hdr": int(m.group(4)),
            "hdr_str": "h" + m.group(4),
            "extension": m.group(5),
        }
    m = re.match(r"c([0-9]+)_r([0-9]+)_z([0-9]+)(\.[a-z]+)", basename)
    if m:
        return {
            "col": int(m.group(1)),
            "col_str": "c" + m.group(1),
            "row": int(m.group(2)),
            "row_str": "r" + m.group(2),
            "stack": int(m.group(3)),
            "stack_str": "z" + m.group(3),
            "extension": m.group(4),
        }
    m = re.match(r"c([0-9]+)_r([0-9]+)_h([0-9]+)(\.[a-z]+)", basename)
    if m:
        return {
            "col": int(m.group(1)),
            "col_str": "c" + m.group(1),
            "row": int(m.group(2)),
            "row_str": "r" + m.group(2),
            "hdr": int(m.group(3)),
            "hdr_str": "h" + m.group(3),
            "extension": m.group(4),
        }
    m = re.match(r"c([0-9]+)_r([0-9]+)_is([0-9]+)(\.[a-z]+)", basename)
    if m:
        return {
            "col": int(m.group(1)),
            "col_str": "c" + m.group(1),
            "row": int(m.group(2)),
            "row_str": "r" + m.group(2),
            "stabilization": int(m.group(3)),
            "stabilization_str": "is" + m.group(3),
            "extension": m.group(4),
        }
    m = re.match(r"c([0-9]+)_r([0-9]+)(\.[a-z]+)", basename)
    if m:
        return {
            "col": int(m.group(1)),
            "col_str": "c" + m.group(1),
            "row": int(m.group(2)),
            "row_str": "r" + m.group(2),
            "extension": m.group(3),
        }
    return None


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
    for fn_full in sorted(
            list(glob.glob(dir_in + "/*.jpg")) +
            list(glob.glob(dir_in + "/*.tif"))):
        basename = os.path.basename(fn_full)

        v = iindex_parse_fn(basename)
        if not v:
            continue
        images[basename] = v
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
    ret["stabilization"] = stabilization
    ret["hdrs"] = hdrs
    ret["stacks"] = stacks
    ret["cols"] = cols
    ret["rows"] = rows
    return ret
