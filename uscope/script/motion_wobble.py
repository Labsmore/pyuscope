from uscope.gui.scripting import ArgusScriptingPlugin


class Plugin(ArgusScriptingPlugin):
    def show_input(self):
        return "Wobble +/- (mm)"

    def run_test(self):
        # XXX: could get this from the input field
        self.wobble_pm = 0.002
        self.tsleep = 1.0

        if self.get_input():
            self.wobble_pm = float(self.get_input())

        start_pos = None
        try:
            self.log("Wobble begin")
            self.log("  z +/-: %0.6f" % self.wobble_pm)
            self.log("  tsleep: %0.1f" % self.tsleep)
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
            if start_pos is not None:
                self.move_absolute(start_pos, block=True)
