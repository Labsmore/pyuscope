class Subsystem:
    def __init__(self, microscope):
        self.microscope = microscope

    def name(self):
        assert 0, "Required"
        return ""

    def cache_load(self, j):
        pass

    def cache_save(self):
        return {}

    def cache_sn_load(self, j):
        pass

    def cache_sn_save(self):
        return {}

    def system_status_ts(self, status):
        """
        Get the current status in a thread safe manner
        Add a key to the output with our name
        """
