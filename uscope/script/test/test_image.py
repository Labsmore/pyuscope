from uscope.scripting import ScriptingPlugin


class Plugin(ScriptingPlugin):
    def run_test(self):
        self.log("Enter")
        image = self.image()
        self.log(f"got image {image}")
