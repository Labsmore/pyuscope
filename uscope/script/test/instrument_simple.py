from uscope.gui.scripting import InstrumentScriptPlugin


class Plugin(InstrumentScriptPlugin):
    def run_test(self):
        while True:
            self.log("Hello instrument running")
            self.sleep(5)
