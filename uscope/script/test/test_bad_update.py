from uscope.scripting import ScriptingPlugin


class Plugin(ScriptingPlugin):
    def input_config(self):
        return {
            "Counter": {
                "widget": "QLineEdit",
                "type": float,
                "default": "1.0"
            }
        }

    def run_test(self):
        self.set_input_default("not counter", "oopsie")
