from uscope.app.argus.scripting import ArgusScriptingPlugin


class Plugin(ArgusScriptingPlugin):
    def show_input(self):
        return "Wobble +/-"

    def run_test(self):
        # XXX: could get this from the input field
        self.wobble_pm = 0.002
        self.tsleep = 1.0

        try:
            self.log("Wobble begin")
            start_pos = self.pos()
            self.backlash_disable()
            while True:
                self.move_absolute({"z": start_pos["z"] + self.wobble_pm},
                                   block=True)
                self.sleep(self.tsleep)
                self.move_absolute({"z": start_pos["z"] - self.wobble_pm},
                                   block=True)
                self.sleep(self.tsleep)
        finally:
            self.log("Wobble end")
            self.backlash_enable()
            self.move_absolute(start_pos, block=True)
