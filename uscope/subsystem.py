class NoSuchFunction(Exception):
    pass


class Subsystem:
    def __init__(self, microscope):
        self.microscope = microscope

    def name(self):
        assert 0, "Required"
        return ""

    def cache_load(self, jroot, j):
        pass

    def cache_save(self, jroot, j):
        pass

    def cache_sn_load(self, jroot, j):
        pass

    def cache_sn_save(self, jroot, j):
        pass

    def system_status_ts(self, root_status, status):
        """
        Get the current status in a thread safe manner
        Add a key to the output with our name
        """

    def functions(self):
        """
        Return a list of supported functions
        """
        return {}

    def function(self, name, kwargs):
        """
        Execute a generic subsystem / instrument function
        Intended to interface to external serialized commands
        """
        raise NoSuchFunction(f"Unsupported function: {name}")
