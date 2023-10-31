from uscope.app.argus.scripting import ArgusScriptingPlugin


class Plugin(ArgusScriptingPlugin):
    def input_config(self):
        return {
            "Counter": {
                "widget": "QLineEdit",
                "type": float,
                "default": "1.0"
            }
        }

    def run_test(self):
        vals = self.get_input()
        new = vals['Counter'] + 1
        self.set_input_default("Counter", new)
        self.log(f"New val: {new}")
