from uscope.touptek_widget import TTControlScroll
from uscope.v4l2_widget import V4L2GstControlScroll

def get_control_scroll(vidpip):
        # Need to hide this when not needed
        if vidpip.source_name == "gst-toupcamsrc":
            return TTControlScroll(vidpip)
        elif vidpip.source_name == "gst-v4l2src":
            return V4L2GstControlScroll(vidpip)
        else:
            print("WARNING: no control layout for source %s" % (vidpip.source_name,)) 
            return None
