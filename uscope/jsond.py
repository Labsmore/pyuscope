"""
JSON delta
A mechanism to apply updates to config files
See examples at bottom
"""
from uscope.util import printj
from collections import OrderedDict


def apply_update(baseline, delta):
    if delta is None:
        return
    if type(baseline) in (dict, OrderedDict):
        assert type(delta) in (dict, OrderedDict)
        for k, v in delta.items():
            # Special operation?
            if ":" in k:
                k1, command = k.split(":")
                if type(v) in (dict, OrderedDict):
                    # Delete all given keys
                    if command == "-":
                        for k2 in v:
                            del baseline[k1][k2]
                    # Insert new keys
                    elif command == "+":
                        for k2, v2 in v.items():
                            baseline[k1][k2] = v2
                    # Recursive
                    elif command == "$":
                        baseline[k1] = apply_update(baseline.get(k1), v)
                    else:
                        assert 0, f"Bad command {command}"
                elif type(v) in (tuple, list):
                    if command == "-":
                        # Delete highest index first to preserve indices
                        for k2 in sorted(v, reverse=True):
                            del baseline[k1][k2]
                    # Insert new items
                    elif command == "+":
                        for v2 in v:
                            baseline[k1].append(v2)
                    else:
                        assert 0, f"Bad command {command}"
                else:
                    assert 0, ("Unexpected type", type(v))
            # Simple replace
            else:
                baseline[k] = v
    elif type(baseline) in (tuple, list):
        pass
    elif type(baseline) in (str, int, float):
        # Can modify a dict or list item, but not immutable base values
        assert delta is None
    return baseline


def apply_updates(baseline, deltas):
    for delta in deltas:
        apply_update(baseline, delta)


def main():
    baseline = {
        # A key in the original we should keep
        "leave_alone": {
            "x_val": "one",
            "y_val": "two",
        },
        # Completely overwritten
        "overwritten_key": {
            "thrown": "away"
        },
        # A dict to modify
        "modified_dict": {
            "20x": {
                "na": 0.42,
                "magnification": 20.0,
            },
            "10x": {
                "na": 0.42,
                "magnification": 20.0,
            },
        },
        # A list to modify
        "modified_list": [
            {
                "name": "first",
                "val": "one"
            },
            {
                "name": "second",
                "val": "two"
            },
        ],

        # Modify a deeper value
        "deep_modified_dict": {
            "nested": {
                "one": 1,
                "two": 2,
            },
        },
    }
    """
    Demonstrates:
    -Adding a key to a dict
    -Deleting a key from a dict
    -Delete a value from a list matching specified key?
    -Adding a new key entirely
    """
    if 0:
        delta1 = {
            # dict: add a key
            "modified_dict:+": {
                "5x": {
                    "na": 0.42,
                    "magnification": 20.0,
                },
            },
            # dict: delete a key
            "modified_dict:-": ["10x"],

            # list: delete a key by index
            "modified_list:-": [0],
            # list: append keys
            "modified_list:+": [{
                "name": "third",
                "val": "three"
            }],
            "overwritten_key:=": {
                "new": "keys"
            },
            "something_new": {
                "oh": "snap"
            },
            "deep_modified_dict:$": {
                "nested:+": {
                    "three": 3,
                }
            }
        }

        apply_update(baseline, delta1)
        printj(baseline)

    if 0:
        delta1 = {
            # dict: add a key
            "modified_dict:+": {
                "5x": {
                    "na": 0.42,
                    "magnification": 20.0,
                },
            },
        }

        print("")
        print("")
        print("")
        apply_update(baseline, delta1)
        printj(baseline)

    if 0:
        delta1 = {
            # dict: delete a key
            "modified_dict:-": ["10x"],
        }

        print("")
        print("")
        print("")
        apply_update(baseline, delta1)
        printj(baseline)

    if 0:
        delta1 = {
            "something_new": {
                "oh": "snap"
            },
        }

        print("")
        print("")
        print("")
        apply_update(baseline, delta1)
        printj(baseline)

    if 0:
        delta1 = {
            # list: delete a key by index
            "modified_list:-": [0],
        }

        print("")
        print("")
        print("")
        apply_update(baseline, delta1)
        printj(baseline)

    if 0:
        delta1 = {
            # list: append keys
            "modified_list:+": [{
                "name": "third",
                "val": "three"
            }],
        }

        print("")
        print("")
        print("")
        apply_update(baseline, delta1)
        printj(baseline)

    if 1:
        delta1 = {
            "deep_modified_dict:$": {
                "nested:+": {
                    "three": 3,
                }
            }
        }

        print("")
        print("")
        print("")
        apply_update(baseline, delta1)
        printj(baseline)


if __name__ == "__main__":
    main()
