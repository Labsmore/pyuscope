"""
Try to run as much QC as possible on argus software
This should be part of pre-release checklist
It assumes the microscope hardware is healthy

Is it possible to get this to run as actual "unittest"?
For now just try to get to end
"""

from uscope.gui.scripting import ArgusScriptingPlugin
import json


class Plugin(ArgusScriptingPlugin):
    def input_config(self):
        return {
            "all": {
                "widget": "QPushButtons",
                "buttons": {
                    "all": "all",
                },
            },
            "microscope": {
                "widget": "QPushButtons",
                "buttons": {
                    "pyuscope_version": "pyuscope_version",
                    "microscope_model": "microscope_model",
                    "microscope_serial": "microscope_serial",
                },
            },
            "high": {
                "widget": "QPushButtons",
                "buttons": {
                    "system_status": "system_status",
                    "is_idle": "is_idle",
                },
            },
            "movement": {
                "widget": "QPushButtons",
                "buttons": {
                    "autofocus": "autofocus",
                    "move_absolute_current": "move_absolute_current",
                },
            },
        }

    def show_run_button(self):
        return False

    def run_test(self):
        test = self.get_input().get("button", {}).get("value")
        # self.log(f"Input: {self.get_input()}")
        self.log(f"Running test: {test}")
        if test == "all":
            self.sleep(0.1)

            self.log("Microscope model: " + self.microscope_model())
            self.log("Microscope serial: " + self.microscope_serial())
            """
            Objective
            """
            active_objective = self.get_active_objective()
            self.log("Active objective: " + active_objective)
            # loopback
            self.set_active_objective(active_objective)
            new_active_objective = self.get_active_objective()
            self.log("Active objective: " + new_active_objective)
            assert new_active_objective == active_objective
            objective_config = self.get_objective_config()
            self.log("objective config: " + json.dumps(objective_config))

            self.log("Waiting imaging ok")
            self.wait_imaging_ok()

            self.log("extension: " + self.image_save_extension())

            self.log("system status: " + json.dumps(self.system_status()))
            self.log("pyuscope_version: " +
                     json.dumps(self.pyuscope_version()))

            self.log("autofocus")
            assert self.is_idle()
            self.autofocus()
            assert self.is_idle()

            motion = self.motion()
            imager = self.imager()
            kinematics = self.kinematics()
            self.backlash_disable()
            self.backlash_enable()

            # FIXME: broken
            # self.imager_get_disp_properties()

            # FIXME: test
            # self.run_plugin()

            # FIXME: test
            # self.run_planner_hconfig()
        elif test == "microscope_model":
            self.log("Microscope model: " + self.microscope_model())
        elif test == "microscope_serial":
            self.log("Microscope serial: " + self.microscope_serial())
        elif test == "system_status":
            self.log("system status: " + json.dumps(self.system_status()))
        elif test == "pyuscope_version":
            self.log("pyuscope_version: " +
                     json.dumps(self.pyuscope_version()))
        elif test == "is_idle":
            self.log("is idle: " + self.is_idle())
        elif test == "autofocus":
            assert self.is_idle()
            self.autofocus()
            assert self.is_idle()
        elif test == "move_absolute_current":
            position = self.position()
            self.move_absolute(position)

        self.log("Done")
