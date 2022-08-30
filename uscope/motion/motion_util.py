import re


def parse_move(s):
    """
    ex: given "X1 Y2"
    return: {'x': 1.0, 'y': 2.0}
    """

    tokens = []
    parsing = s.replace(" ", "")
    while parsing:
        m = re.match(r"([0-9.+-]+)", parsing)
        if m:
            token = m.group(1)
            tokens.append(("number", token))
            parsing = parsing[len(token):]
            continue

        m = re.match(r"([xyzXYZ])", parsing)
        if m:
            token = m.group(1)
            tokens.append(("axis", token))
            parsing = parsing[len(token):]
            continue

        raise ValueError("failed to parse '%s' at '%s'" % (s, parsing))

    if len(tokens) % 2 != 0:
        raise ValueError("expected number per axis")

    ret = {}
    for tokeni in range(0, len(tokens), 2):
        axist = tokens[tokeni]
        numbert = tokens[tokeni + 1]
        if axist[0] != "axis":
            raise ValueError("failed to parse %s" % s)
        if numbert[0] != "number":
            raise ValueError("failed to parse %s" % s)
        ret[axist[1].lower()] = float(numbert[1])

    return ret
