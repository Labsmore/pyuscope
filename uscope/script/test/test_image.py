from uscope.app.argus.scripting import ArgusScriptingPlugin


class Plugin(ArgusScriptingPlugin):
    def run_test(self):
        self.log("Enter")
        image = self.image()
        self.log(f"got image {image}")
