from uscope.app.argus.scripting import ArgusScriptingPlugin


class Plugin(ArgusScriptingPlugin):
    def input_config(self):
        return {
            "Mode": {
                "widget": "QComboBox",
                "values": ["Fast", "Medium", "Slow"],
                "default": "Medium"
            },
            "Distance": {
                "widget": "QLineEdit",
                "type": float,
                "key": "distance",
                "default": "1.0"
            },
            "Buttons": {
                "widget": "QPushButtons",
                "buttons": {
                    "Left": "left",
                    "Right": "right",
                },
            },
        }

    def run_test(self):
        self.log("Hello, world!")
        vals = self.get_input()
        self.log(f"Mode: {vals['Mode']}")
        self.log(f"Distance: {vals['distance']}")
        self.log(f"Button: {vals.get('button')}")
