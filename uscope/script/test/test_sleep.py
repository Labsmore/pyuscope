from uscope.app.argus.scripting import ArgusScriptingPlugin


class Plugin(ArgusScriptingPlugin):
    def run_test(self):
        self.log("Enter")
        self.sleep(3)
        self.log("Exit")
