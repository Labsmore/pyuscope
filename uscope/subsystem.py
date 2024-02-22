class NoSuchFunction(Exception):
    pass


class Subsystem:
    def __init__(self, microscope):
        self.microscope = microscope

    def name(self):
        """
        Called from main thread
        Thread safe
        Thread safe
            Called from main thread
            Called from other contexts
        """
        assert 0, "Required"
        return ""

    def cache_load(self, jroot, j):
        """
        Called from main thread
        Thread safe
            Called from main thread
        """
        pass

    def cache_save(self, jroot, j):
        """
        Called from main thread
        Thread safe
            Called from main thread
        """
        pass

    def cache_sn_load(self, jroot, j):
        """
        Called from main thread
        Thread safe
            Called from main thread
        """
        pass

    def cache_sn_save(self, jroot, j):
        """
        Thread safe
            Called from main thread
        """
        pass

    def system_status_ts(self, root_status, status):
        """
        Get the current status in a thread safe manner
        Add a key to the output with our name
        Thread safe
            Called from main thread
        """

    def functions(self):
        """
        Return a list of supported functions
        Thread safe
            Called from misc contexts (ex: scripting)

        return {"shout": {"n": {"type": int}}}
        """
        return {}

    def functions_serialized(self):
        ret = dict(self.functions())
        for v in ret.values():
            if "type" in v:
                v["type"] = str(v["type"])
        return ret

    def function_ts(self, name, kwargs):
        """
        Execute a generic subsystem / instrument function
        Intended to interface to external serialized commands
        Generally this is expected to IPC to another thread
        Ex: scripting subsystem
        Thread safe
            Called from misc contexts (ex: scripting)
        """
        # Default has no supported funtions
        raise NoSuchFunction(f"Unsupported function: {name}")

    def function_parse_serialized(self, name, kwargs):
        """
        Convert kwargs into correct types
        """
        params = self.functions()[name]
        for k in list(kwargs.keys()):
            param = params[k]
            if "type" in param:
                kwargs[k] = param["type"](kwargs[k])
        # For convenience
        return kwargs
