from uscope.scripting import ScriptingPlugin


class Plugin(ScriptingPlugin):
    def run_test(self):
        self.log("Hello, world!")
