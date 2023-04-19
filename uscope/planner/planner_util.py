from uscope.planner.planner import Planner
from uscope.planner.plugins import register_plugins as _register_plugins
from uscope.config import USC


def get_objective(usc=None, objectivei=None, objectivestr=None):
    objectives = usc.get_scaled_objectives()
    if objectivestr:
        for objective in objectives:
            if objective["name"] == objectivestr:
                return objective
        raise ValueError("Failed to find named objective")
    if objectivei is not None:
        return objectives[objectivei]
    # Only one objective? Default to it
    if len(objectives) == 1:
        return objectives[0]
    assert 0, "Ambiguous objective, must specify"


def microscope_to_planner_config(usj=None,
                                 usc=None,
                                 objective=None,
                                 objectivestr=None,
                                 objectivei=None,
                                 contour=None,
                                 corners=None):
    if usc is None:
        usc = USC(usj)
    usj = usc.usj
    if objective is None:
        objective = get_objective(usc=usc,
                                  objectivei=objectivei,
                                  objectivestr=objectivestr)
    ret = {
        "imager": {
            "x_view": objective["x_view"],
        },
        "motion": {},
        "kinematics": {},
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

    # GstGUIImager does actual scaling
    # But needed to make prints nice
    ret["imager"]["raw_wh_hint"] = usc.imager.raw_wh()
    ret["imager"]["final_wh_hint"] = usc.imager.final_wh()
    v = usj["imager"].get("scalar")
    if v:
        ret["imager"]["scalar_hint"] = float(v)
    v = usc.imager.crop_tblr()
    if v:
        ret["imager"]["crop_tblr_hint"] = v

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

    # Hmm lets make these add
    # Allows essentially making a linear equation if you really want to tune it
    ret["kinematics"]["tsettle_motion"] = usc.kinematics.tsettle_motion_max(
    ) + objective.get("tsettle_motion", 0.0)
    ret["kinematics"]["tsettle_hdr"] = usc.kinematics.tsettle_hdr()

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
                microscope=None,
                verbosity=None):
    pipeline_names = []

    if "points-xy2p" in pconfig:
        pipeline_names.append("points-xy2p")
    if "points-xy3p" in pconfig:
        pipeline_names.append("points-xy3p")
    if "points-stacker" in pconfig:
        pipeline_names.append("points-stacker")
    # FIXME: needs review / testing
    if "hdr" in pconfig["imager"]:
        pipeline_names.append("hdr")
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
                  microscope=microscope,
                  verbosity=verbosity)
    if progress_callback:
        ret.register_progress_callback(progress_callback)
    return ret
