from uscope.planner.planner import Planner
from uscope.planner.plugins import register_plugins as _register_plugins


def get_objective(usj, objectivei=None, objectivestr=None):
    if objectivestr:
        for objective in usj["objectives"]:
            if objective["name"] == objectivestr:
                return objective
        raise ValueError("Failed to find named objective")
    # Only one objective? Default to it
    if objectivei is None and not objectivestr and len(usj["objectives"]) == 0:
        return usj["objectives"][0]
    assert 0, "Ambiguous objective, must specify"


def microscope_to_planner_config(usj,
                                 objective=None,
                                 objectivestr=None,
                                 objectivei=None,
                                 contour=None,
                                 corners=None):
    if objective is None:
        objective = get_objective(usj=usj,
                                  objectivei=objectivei,
                                  objectivestr=objectivestr)
    ret = {
        "imager": {
            "x_view": objective["x_view"],
        },
        "motion": {},
    }

    if contour is not None:
        ret["points-xy2p"] = {
            "contour": contour,
        }
    if corners is not None:
        ret["points-xy3p"] = {
            "corners": corners,
        }
    assert "points-xy2p" in ret or "points-xy3p" in ret, (contour, corners)

    v = usj["imager"].get("scalar")
    if v:
        ret["imager"]["scalar"] = float(v)
    v = usj["imager"].get("save_extension")
    if v:
        ret["imager"]["save_extension"] = v
    v = usj["imager"].get("save_quality")
    if v:
        ret["imager"]["save_quality"] = v

    v = usj["motion"].get("origin")
    if v:
        ret["motion"]["origin"] = v

    v = usj["motion"].get("backlash")
    if v:
        ret["motion"]["backlash"] = v
    v = usj["motion"].get("backlash_compensation")
    if v:
        ret["motion"]["backlash_compensation"] = v

    # By definition anything in planner section is planner config
    # give more thought to precedence at some point
    for k, v in usj.get("planner", {}).items():
        ret[k] = v

    return ret


"""
Setup typical planner pipeline given configuration
"""


def get_planner(pconfig,
                motion,
                imager,
                out_dir,
                dry,
                meta_base=None,
                log=None,
                progress_callback=None,
                verbosity=None):
    pipeline_names = []

    if "points-xy2p" in pconfig:
        pipeline_names.append("points-xy2p")
    if "points-xy3p" in pconfig:
        pipeline_names.append("points-xy3p")
    if "points-stacker" in pconfig:
        pipeline_names.append("points-stacker")
    # FIXME: needs review / testing
    # if "hdr" in pconfig["imager"]:
    #    pipeline_names.append("hdr")
    pipeline_names.append("kinematics")
    pipeline_names.append("image-capture")
    if not imager.remote():
        pipeline_names.append("image-save")
    # pipeline_names.append("scraper")

    ret = Planner(pconfig=pconfig,
                  motion=motion,
                  imager=imager,
                  out_dir=out_dir,
                  dry=dry,
                  meta_base=meta_base,
                  log=log,
                  pipeline_names=pipeline_names,
                  verbosity=verbosity)
    if progress_callback:
        ret.register_progress_callback(progress_callback)
    return ret
