from uscope.scripting import ScriptingPlugin


class Plugin(ScriptingPlugin):
    def run_test(self):
        self.log("Enter")
        self.sleep(3)
        self.log("Exit")
