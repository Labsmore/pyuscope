plugins = {}


def register_plugin(name, ctor):
    plugins[name] = ctor


def register_plugins():
    def gst_videotestsrc(session):
        from uscope.imager.plugins.gst_videotestsrc.aplugin import Plugin
        return Plugin(session)

    register_plugin("gst-videotestsrc", gst_videotestsrc)

    def gst_toupcamsrc(session):
        from uscope.imager.plugins.gst_toupcamsrc.aplugin import Plugin
        return Plugin(session)

    register_plugin("gst-toupcamsrc", gst_toupcamsrc)

    def gst_libcamerasrc(session):
        from uscope.imager.plugins.gst_libcamerasrc.aplugin import Plugin
        return Plugin(session)

    register_plugin("gst-libcamerasrc", gst_libcamerasrc)

    def gst_v4l2src(session):
        from uscope.imager.plugins.gst_v4l2src.aplugin import Plugin
        return Plugin(session)

    register_plugin("gst-v4l2src", gst_v4l2src)

    def gst_v4l2src_hy800b(session):
        from uscope.imager.plugins.gst_v4l2src_hy800b.aplugin import Plugin
        return Plugin(session)

    register_plugin("gst-v4l2src-hy800b", gst_v4l2src_hy800b)

    def gst_v4l2src_mu800(session):
        from uscope.imager.plugins.gst_v4l2src_mu800.aplugin import Plugin
        return Plugin(session)

    register_plugin("gst-v4l2src-mu800", gst_v4l2src_mu800)

    def gst_v4l2src_yw500(session):
        from uscope.imager.plugins.gst_v4l2src_yw500.aplugin import Plugin
        return Plugin(session)

    register_plugin("gst-v4l2src-yw500", gst_v4l2src_yw500)

    def gst_v4l2src_yw500u3m(session):
        from uscope.imager.plugins.gst_v4l2src_yw500u3m.aplugin import Plugin
        return Plugin(session)

    register_plugin("gst-v4l2src-yw500u3m", gst_v4l2src_yw500u3m)

    def picam2src(session):
        from uscope.imager.plugins.picam2src.aplugin import Plugin
        return Plugin(session)

    register_plugin("picam2src", picam2src)


register_plugins()


def get_imager_aplugin(session, source_name):
    factory = plugins.get(source_name)
    if factory is None:
        raise Exception(f"Unknown imager plugin {source_name}")

    return factory(session)


def auto_detect_source(verbose=False):
    assert 0, 'fixme'
    # verbose and print("ADS: giving up (usb: %s)" % bool(usb))
    return "gst-testsrc"
