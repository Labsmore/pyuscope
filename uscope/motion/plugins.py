from uscope.motion import hal as cnc_hal
from uscope.motion.lcnc import hal as lcnc_hal
from uscope.motion.lcnc import hal_ar as lcnc_ar
from uscope.motion.lcnc.client import LCNCRPC
from uscope.motion.grbl import GrblHal
import socket

plugins = {}


def register_plugin(name, ctor):
    plugins[name] = ctor


def get_lcnc_host(mj):
    try:
        return mj["lcnc"]["host"]
    except KeyError:
        return "mk"


def register_plugins():

    def mock_hal(mj, log):
        return cnc_hal.MockHal(log=log)

    register_plugin("mock", mock_hal)

    def lcnc_py(mj, log):
        import linuxcnc

        return lcnc_hal.LcncPyHal(linuxcnc=linuxcnc, log=log)

    register_plugin("lcnc-py", lcnc_py)

    def lcnc_rpc(mj, log):
        try:
            return lcnc_hal.LcncPyHal(linuxcnc=LCNCRPC(host=get_lcnc_host(mj)),
                                      log=log)
        except socket.error:
            raise
            raise Exception("Failed to connect to LCNCRPC %s" %
                            get_lcnc_host(mj))

    register_plugin("lcnc-rpc", lcnc_rpc)

    def lcnc_arpc(mj, log):
        return lcnc_ar.LcncPyHalAr(host=get_lcnc_host(mj), log=log)

    register_plugin("lcnc-arpc", lcnc_rpc)

    def lcnc_rsh(mj, log):
        return lcnc_hal.LcncRshHal(log=log)

    register_plugin("lcnc-rsh", lcnc_rsh)

    def grbl_ser(mj, log):
        return GrblHal()

    register_plugin("grbl-ser", grbl_ser)
    # XXX: look into the network protocols
    # used by fluid nc


register_plugins()


def get_motion_hal(usj=None, mj=None, log=print):
    if mj is None:
        mj = usj["motion"]
    name = mj["hal"]
    log("get_motion_hal: %s" % name)
    ctor = plugins.get(name)
    if ctor is None:
        raise Exception("Unknown motion HAL %s" % name)
    return ctor(mj, log)
