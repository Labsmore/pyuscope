import threading


class PlannerPlugin:
    def __init__(self, planner):
        self.planner = planner
        # Convenience shortcut. Might remove after cleanup
        self.pc = self.planner.pc
        self.motion = self.planner.motion
        self.imager = self.planner.imager
        self.comment = self.planner.comment
        self.dry = self.planner.dry
        self.log = self.planner.log

    def wait_imaging_ok(self):
        """
        Return once its safe to image
        Could be due to vibration, exposure settings, frame sync, etc
        """
        pass

    def scan_begin(self, state):
        """
        Called once when the scan is starting
        """
        pass

    def scan_end(self, state):
        """
        Called once when the scan is completing
        """
        pass

    def gen_meta(self, meta):
        """
        Generate final metadata output
        """
        pass

    def print_run_header(self):
        """
        Use one or more self.comment() to provide output
        """
        pass

    def images_expected(self):
        """
        Image multiplier
        Ex: planner might return 200
        Ex: focus stack might return 3
        Ex: explosre stack might return 5
        The full pipline would then have 200 * 3 * 5 = 3000
        For now its linear, deal with special circumstances later
        If not directly contributing to image generation return None
        """
        return None

    def iterate(self, state):
        """
        Core plugin function
        The pipeline is such that subsequent items will get iterated once for each item before them

        XXX: moved to just replace_keys
        yield append_keys, replace_keys
        replace_keys is reserved for hacks / special use
        state should be reated as read only
        yield/return None as a nop / passthrough. its equivalent to {}, {}
        Note that returning an iterator with 0 elements will collapse the pipeline
        This can make sense if there are 0 things to image, but may be an error

        Ex:
        point_generator => take_jpg_picture
        Save to 

        Ex:
        point_generator => (take_raw_picture, take_jpg_picture)
        Save to both jpg and raw
        Maybe skip this for now to keep things simple

        Ex:
        point_generator => focus_stacker => hdr => average_n_pictures => take_jpg_picture
        This way point_generator can move each time to a new position, focus_stacker can set 

        Yield a dictionary with keys like following
        High level pipeline logic will combine them into aggregate state

        {
            # Contribution, if any, to final file name
            "fn": "c03_r12",
            "images": [
                {
                    # PIL image
                    "im", im1,
                    "fn": "z01",
                    "src": "ArgusGUI",
                },
                {
                    # PIL image
                    "im", im2,
                    "fn": "z02",
                    "src": "gxs700",
                }
            ]
        }
        """
        yield None

    '''
    flatten dict. try not to add anything to second level
    def state_add_dict(self, state, state_key, sub_key, sub_val):
        """
        Create a new state change order just with the new key added
        TODO: think of a cleaner way to do this, maybe using modifiers instead
        """
        replace_keys = {
            # Copy dict so we don't modify old state
            state_key: dict(state[state_key]),
        }
        # Add / substitute the new key
        replace_keys[state_key][sub_key] = sub_val
        return replace_keys
    '''


plugins = {}


def register_plugin(name, ctor):
    plugins[name] = ctor


def get_planner_plugin(planner, name):
    ctor = plugins.get(name)
    if ctor is None:
        raise Exception("Unknown planner plugin %s" % name)
    return ctor(planner)
