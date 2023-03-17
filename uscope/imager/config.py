from uscope.imager.imager_util import auto_detect_source
from uscope.imager.touptek import toupcamsrc_info


def default_usj_imager_config(opts):
    raise Exception("FIXME")


def default_gstimager_config(opts):
    """
    For GstImager
    """
    """
    For microscope.json
    This could also be used to generate a config file
    """

    source = opts.get("source", None)
    if source is None:
        source = auto_detect_source()
        if source.find("gst-") != 0:
            raise Exception("Did not detect gst source")
        source = source.replace("gst-", "")
    opts["source"] = source

    if source == "toupcamsrc":
        """
        Supported configurations:
        -Resolution not specified: default to esize 0
        -Resolution specified: match it to esize
        -esize and resolution specified
            redundant but quick initialization

        Not supported:
        -esize specified but not resolution
            Might work for now but might drop
        """
        toupcamsrc = opts.get("toupcamsrc", {})
        opts["toupcamsrc"] = toupcamsrc
        if "wh" not in opts or toupcamsrc.get("esize") is None:
            # 750 ms hmm
            info = toupcamsrc_info()
            # Not specified: give default
            if "wh" not in opts and toupcamsrc.get("esize") is None:
                toupcamsrc["esize"] = 0
            # Specified resolution but not esize
            # Probably most "user friendly" way to specify resolution
            if toupcamsrc.get("esize") is not None:
                esize = toupcamsrc.get("esize")
                res = info["eSizes"][esize]["StillResolution"]
                opts["wh"] = res["w"], res["h"]
            # Find esize for given resolution
            else:
                w, h = opts["wh"]
                for esize, vs in info["eSizes"].items():
                    vs = vs["StillResolution"]
                    if (w, h) == (vs["w"], vs["h"]):
                        toupcamsrc["esize"] = esize
                        break
                else:
                    raise ValueError("failed to find esize")
    elif source == "v4l2src":
        # TODO: scrape info one way or the other to identify preferred device
        pass
