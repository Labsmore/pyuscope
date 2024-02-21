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
    def run_test(self):
        self.log("Argus software QA running")
        self.sleep(0.1)

        self.log("Microscope model: " + self.microscope_model())
        self.log("Microscope s/n: " + self.microscope_serial())
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
        self.log("pyuscope_version: " + json.dumps(self.pyuscope_version()))

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

        self.log("Done")
