from uscope.motion import hal as cnc_hal
from uscope.motion.lcnc import hal as lcnc_hal
from uscope.motion.lcnc import hal_ar as lcnc_ar
from uscope.motion.lcnc.client import LCNCRPC
from uscope.motion.grbl import GrblHal
from uscope.config import get_usc
import socket

plugins = {}


def register_plugin(name, ctor):
    plugins[name] = ctor


def get_lcnc_host(usc_motion):
    try:
        return usc_motion.j["lcnc"]["host"]
    except KeyError:
        return "mk"


def register_plugins():
    def mock_hal(usc_motion, kwargs):
        return cnc_hal.MockHal(**kwargs)

    register_plugin("mock", mock_hal)

    def lcnc_py(usc_motion, kwargs):
        import linuxcnc

        return lcnc_hal.LcncPyHal(linuxcnc=linuxcnc, **kwargs)

    register_plugin("lcnc-py", lcnc_py)

    def lcnc_rpc(usc_motion, kwargs):
        try:
            return lcnc_hal.LcncPyHal(
                linuxcnc=LCNCRPC(host=get_lcnc_host(usc_motion)), **kwargs)
        except socket.error:
            raise
            raise Exception("Failed to connect to LCNCRPC %s" %
                            get_lcnc_host(usc_motion))

    register_plugin("lcnc-rpc", lcnc_rpc)

    def lcnc_arpc(usc_motion, kwargs):
        return lcnc_ar.LcncPyHalAr(host=get_lcnc_host(usc_motion), **kwargs)

    register_plugin("lcnc-arpc", lcnc_arpc)

    def lcnc_rsh(usc_motion, kwargs):
        return lcnc_hal.LcncRshHal(**kwargs)

    register_plugin("lcnc-rsh", lcnc_rsh)

    def grbl_ser(usc_motion, kwargs):
        port = usc_motion.j.get("grbl", {}).get("port")
        return GrblHal(port=port, **kwargs)

    register_plugin("grbl-ser", grbl_ser)
    # XXX: look into the network protocols
    # used by fluid nc


register_plugins()


def get_motion_hal(usc=None, usc_motion=None, log=print):
    if usc_motion is None:
        if usc is None:
            usc = get_usc()
        usc_motion = usc.motion
    name = usc_motion.hal()
    scalars = usc_motion.scalars()
    # log("get_motion_hal: %s" % name)
    ctor = plugins.get(name)
    if ctor is None:
        raise Exception("Unknown motion HAL %s" % name)
    kwargs = {"scalars": scalars, "log": log}

    slj = usc_motion.j.soft_limits()
    if slj:
        soft_limits = {}
        for axis in "xyz":
            axmin = slj.get(axis + "min")
            axmax = slj.get(axis + "max")
            if axmin is not None or axmax is not None:
                axmin = axmin if axmin else 0.0
                axmax = axmax if axmax else 0.0
                soft_limits[axis] = (axmin, axmax)
        kwargs["soft_limits"] = soft_limits

    return ctor(usc_motion, kwargs)
