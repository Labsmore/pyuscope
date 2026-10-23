from PIL import Image


def get_scaled(image, factor, filt=Image.NEAREST):
    if factor == 1.0:
        return image
    else:
        return image.resize(
            (int(image.size[0] * factor), int(image.size[1] * factor)), filt)


def format_mm_3dec(value):
    """
    Given a value in mm return a string printing it nicely
    Currently always gives 3 decimal places
    """
    if value >= 100:
        return "%d mm" % value
    elif value >= 10:
        return "%0.1f mm" % value
    elif value >= 1:
        return "%0.2f mm" % value
    elif value >= 0.1:
        return "%d um" % (value * 1000, )
    elif value >= 0.01:
        return "%0.1f um" % (value * 1000, )
    elif value >= 0.001:
        return "%0.2f um" % (value * 1000, )
    else:
        if value < 0.0001 and value != 0.0:
            print("WARNING: formatitng very small number: %g" % (value, ))
        return "%0.3f um" % (value * 1000, )


def hdr_exposure_sequence(center, pm_stops, stops_per):
    """
    Exposure bracket around center, sorted low to high
    pm_stops: number of exposures on each side of center
    stops_per: stops (factors of 2) between adjacent exposures, may be fractional
    Values are left as floats: the property layer rounds to what the camera accepts
    """
    pm_stops = int(pm_stops)
    if pm_stops < 0:
        raise ValueError("Bracket count must be >= 0")
    stops_per = float(stops_per)
    if stops_per <= 0:
        raise ValueError("Stops per exposure must be > 0")
    return [
        center * 2**(stopi * stops_per)
        for stopi in range(-pm_stops, pm_stops + 1)
    ]


def format_exposure_csv(exposures):
    """
    Round to the nearest integer when that keeps every value distinct
    Small exposure units (ex: V4L2 100 us steps) would otherwise collapse
    """
    rounded = [int(round(x)) for x in exposures]
    if len(set(rounded)) == len(rounded) and min(rounded) > 0:
        return ",".join("%u" % x for x in rounded)
    return ",".join("%.4g" % x for x in exposures)
