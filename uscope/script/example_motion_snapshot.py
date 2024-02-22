from uscope.gui.scripting import ArgusScriptingPlugin


class Plugin(ArgusScriptingPlugin):
    def run_test(self):
        self.log("Hello, world!")
        origin = self.position()
        self.log(f"Starting at {origin}")
        try:
            self.log("Snaping image")
            self.move_relative({"x": 0.1, "y": 0.2})
            im = self.image()
            im.show()
        finally:
            self.log("Moving back to origin on exit")
            self.move_absolute(origin)
