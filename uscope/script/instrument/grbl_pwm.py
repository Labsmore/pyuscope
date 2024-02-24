"""
Uses GRBL specific functionality

In your .pyuscope:
"instruments": {
    "pwm": {
        "tab_name": "PWM",
        "visible": true,
        "plugin_path": "/home/labsmore/pyuscope/uscope/script/instrument/grbl_pwm.py",
        "parameters": {
            "startup_onoff": true,
            "shutdown_onoff": false,
        }
    },
},
"""
from uscope.gui.scripting import InstrumentScriptPlugin


class Plugin(InstrumentScriptPlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # TODO: query via $30 / $31 etc
        self.min_rpm = 0
        self.max_rpm = 10000
        self.parameters = {}

    def functions(self):
        return {
            "set_duty_cycle": {
                "duty_cycle": {
                    "type": float,
                    "min": 0,
                    "max": 100
                }
            },
            "set_on": {
                "on": {
                    "type": int
                }
            },
        }

    def set_duty_cycle(self, duty_cycle=None):
        if duty_cycle is None:
            raise ValueError("Required")
        assert 0 <= duty_cycle <= 100, f"Bad duty cycle: {duty_cycle}"
        if duty_cycle > 0:
            rpm = int(round(self.max_rpm * duty_cycle / 100))
            self.log(f"Set PWM RPM: {rpm} / {self.max_rpm}")
            self.motion().command(f"M3 S{rpm}")
        else:
            self.log(f"Set PWM off")
            self.motion().command("M5")

    def set_on(self, on=True):
        if on:
            self.set_duty_cycle(100)
        else:
            self.set_duty_cycle(0)

    def instrument_init(self, parameters):
        self.parameters = parameters
        self.log("Initializing PWM")
        set_on = parameters.get("startup_onoff")
        self.log(f"Initial state: {set_on}")
        if set_on is not None:
            self.set_on(bool(set_on))

    def cleanup(self):
        # XXX: not sure how reliable this sequence is for window close
        set_on = self.parameters.get("shutdown_onoff")
        self.log(f"Final state: {set_on}")
        if set_on is not None:
            self.set_on(bool(set_on))
