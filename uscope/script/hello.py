from uscope.gui.scripting import ArgusScriptingPlugin


class Plugin(ArgusScriptingPlugin):
    def run_test(self):
        self.log("Hello, world!")
