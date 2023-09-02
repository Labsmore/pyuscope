from uscope.app.argus.scripting import ArgusScriptingPlugin


class Plugin(ArgusScriptingPlugin):
    def run_test(self):
        self.log("Hello, world!")
