"""
Scripting engine
Qt free: usable from Argus or headless (see app/headless.py)

A script is a .py file defining a class named Plugin that subclasses ScriptingPlugin
It runs in its own thread against a session object
(ArgusCommon in the GUI, HeadlessSession otherwise)

Session interface used here:
    microscope, usc, motion, motion_thread, image_processing_thread
    objective_config()
    get_active_objective() / set_active_objective(name)
    image_save_extension()
    set_um_per_pixel_raw_1x(val)
    apply_capture_format_override(fmt)
    run_planner_hconfigs(hconfigs)
    set_planner_xy3p_corners(corners)
    imager_properties

Front end specific behavior (where logs go, how images are shown, prompting the user)
is provided by a ScriptHost
"""

from uscope.motion import motion_util
from uscope.microscope import StopEvent, MicroscopeStop
from uscope.util import time_str_1dec
from uscope.threads import CBSync
from uscope import version
from uscope.subsystem import Subsystem

import importlib.util
import os
import sys
import ctypes
import threading
import time
import traceback
import json
import queue
import numpy as np
from PIL import Image


class TestFailed(Exception):
    pass


class TestAborted(Exception):
    pass


class TestKilled(SystemExit):
    pass


class ScriptHost:
    """
    Front end hooks
    All methods are called from the script thread
    """
    def log(self, msg):
        print(msg)

    def show_image(self, im, title):
        """
        im: a private PIL image copy
        """
        pass

    def prompt(self, title, text, buttons, check_running):
        """
        Block until the user picks one of buttons and return its label
        Call check_running() periodically while waiting
        By convention the last button is the safe / cancel choice
        """
        return buttons[-1]

    def done(self, plugin):
        pass


class HeadlessScriptHost(ScriptHost):
    """
    Logs to stdout (and optionally a file)
    show_image() saves to image_dir
    prompt() asks on stdin if interactive, otherwise picks the first button
    if assume_yes or the last (cancel) button if not
    """
    def __init__(self,
                 image_dir=None,
                 log_fn=None,
                 assume_yes=False,
                 interactive=None):
        self.image_dir = image_dir
        self.log_fn = log_fn
        self.assume_yes = assume_yes
        if interactive is None:
            interactive = sys.stdin is not None and sys.stdin.isatty()
        self.interactive = interactive
        self.images = []
        self.done_event = threading.Event()
        self._lock = threading.Lock()

    def log(self, msg):
        if self.log_fn:
            self.log_fn(msg)
        else:
            print(msg)

    def show_image(self, im, title):
        if not self.image_dir:
            self.log(f"show_image: {title} (no image directory, not saved)")
            return
        os.makedirs(self.image_dir, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "_"
                       for c in title) or "image"
        with self._lock:
            fn = os.path.join(self.image_dir,
                              "%03u_%s.png" % (len(self.images), safe))
            self.images.append(fn)
        if im.mode not in ("RGB", "RGBA", "L", "I;16"):
            im = im.convert("RGB")
        im.save(fn)
        self.log(f"show_image: {title} => {fn}")

    def prompt(self, title, text, buttons, check_running):
        self.log(f"PROMPT {title}: {text}")
        if self.assume_yes:
            ret = buttons[0]
        elif self.interactive:
            while True:
                ans = input("  [%s] > " % "/".join(buttons)).strip()
                for button in buttons:
                    if ans.lower() == button.lower():
                        return button
        else:
            ret = buttons[-1]
        self.log(f"  => {ret}")
        return ret

    def done(self, plugin):
        self.done_event.set()


class ScriptingPlugin:
    def __init__(self, session, host=None):
        self.session = session
        if host is None:
            host = ScriptHost()
        self.host = host
        self._input = None
        self.se = None
        self._thread = None
        self.ident = None
        # Graceful shutdown request
        self._running = threading.Event()
        self.reset()
        self.tstart = None
        self.tend = None

    def reset(self):
        self._succeeded = None
        self.result_message = None
        self.new_defaults = {}
        self._running.set()

    def start(self):
        """
        Run in a new thread
        A plugin may be started again once the previous run is done
        """
        if self._thread is not None:
            # done() is reported just before the thread exits
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                raise RuntimeError("Script is already running")
        self._thread = threading.Thread(target=self.run,
                                        name="script",
                                        daemon=True)
        self._thread.start()

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def log(self, s):
        """
        Log a message to the script window
        (not the main window)
        """
        self.host.log(str(s))

    def show_image(self, im, title="Image"):
        """
        Display an image in a non-modal window
        (headless: saved to disk)
        Accepts a PIL image or a numpy array (OpenCV BGR / grayscale)
        Returns immediately
        """
        if isinstance(im, np.ndarray):
            if im.ndim == 3 and im.shape[2] == 3:
                im = im[:, :, ::-1]
            im = Image.fromarray(im)
        self.host.show_image(im.copy(), title)

    def prompt(self, title, text, buttons=("OK", "Cancel")):
        """
        Ask the user to pick a button and return its label
        Blocks until answered
        """
        self.check_running()
        ret = self.host.prompt(title, text, list(buttons), self.check_running)
        self.check_running()
        return ret

    def message_box_yes_cancel(self, title, message):
        return self.prompt(title, message, ("Yes", "Cancel")) == "Yes"

    def shutdown(self):
        """
        Request graceful termination
        """
        self._running.clear()

    def get_input(self):
        """
        Return a dictionary containing applicable fields
        """
        return self._input

    def set_input_default(self, k, value):
        """
        Allows a script to have modes that setup various parameters
        NOTE: this used to be by label, now its by key
        """
        self.new_defaults[k] = value

    def fail(self, message):
        """
        Thread should call this to abort a run
        Indicates the operation failed
        """
        self._succeeded = False
        self.result_message = message
        raise TestFailed(message)

    def check_running(self):
        """
        This thread should periodically check for graceful shutdown
        """
        self.se.poll()
        if not self._running.is_set():
            raise TestAborted()

    def succeeded(self):
        return bool(self._succeeded)

    def run(self, input_=None, button_value=None, top_level=True):
        self.tstart = time.time()
        if button_value is not None:
            self._input = {"button": {"value": button_value}}
        if input_ is not None:
            self._input = input_
        if self._input is None:
            self._input = {}
        self.ident = threading.current_thread().ident
        try:
            with StopEvent(self.session.microscope) as self.se:
                self.run_test()
                self.wrap_cleanup("Done. Running cleanup")
            self._succeeded = True
        except TestFailed:
            self.wrap_cleanup("Failed. Running cleanup")
            self._succeeded = False
            self.result_message = "Failed"
        # Test stopped but not microscope
        except TestAborted:
            self.wrap_cleanup("Aborted. Running cleanup")
            self._succeeded = False
            self.result_message = "Aborted"
        # Full microscope stop
        # Closer to estop
        # Don't clean up
        except MicroscopeStop:
            self._succeeded = False
            self.result_message = "Aborted"
        # Test unstable and force killed
        # Unstable, don't attempt cleanup
        except TestKilled:
            self._succeeded = False
            self.result_message = "killed"
        # Generic test crash
        # Try to cleanup if possible
        # Includes OSError (ex: camera disconnect)
        except Exception as e:
            self.wrap_cleanup("Exception. Running cleanup")
            self._succeeded = False
            self.result_message = f"Exception: {e}"
            print("")
            print("Script generated unhandled exception")
            traceback.print_exc()
        finally:
            self._running.clear()
            if top_level:
                self.host.done(self)

    def wrap_cleanup(self, msg):
        self.tend = time.time()
        try:
            self._running.set()
            self.log(msg)
            self.log("Completed after %s" %
                     time_str_1dec(self.tend - self.tstart))
            try:
                self.cleanup()
            except Exception as _e:
                self.log("Script generated unhandled exception in cleanup")
                print("Script generated unhandled exception in cleanup")
                traceback.print_exc()
        finally:
            self._running.clear()

    def cleanup(self):
        pass

    """
    Main API
    """

    def position(self):
        """
        Get current stage position
        Returns a dictionary like:
        {"x": 12.345, "y": 2.356, "z": 4.5}
        TOOD: add a parameter that forces update instead of from cache
        """
        self.check_running()
        return self.session.motion_thread.get_pos_cache()

    # deprecated, don't use
    def pos(self):
        return self.position()

    def move_absolute(self, pos, block=True):
        """
        Set current position
        Pos can include one or more positions like:
        {"z": 4.5}
        {"x": 12.345, "y": 2.356, "z": 4.5}
        """
        self.check_running()

        # TODO: find a way to get exceptions to bubble up here
        self.session.motion.check_valid_position(pos)
        self.session.motion_thread.move_absolute(pos, block=block)
        self.check_running()

    def move_relative(self, pos, block=True):
        self.check_running()
        # TODO: find a way to get exceptions to bubble up here
        self.session.motion_thread.move_relative(pos, block=block)
        self.check_running()

    def position_format(self, axes):
        """
        Convert a dictionary of axis positions to a string
        Ex: {"x" 1, "y": 2} => "X1 Y2"
        """
        return self.session.usc.motion.format_positions(axes)

    def position_parse(self, s):
        """
        Convert a axis position string to a dictionary of positions
        Ex: "X1 Y2" => {"x" 1, "y": 2}
        """
        return motion_util.parse_move(s)

    def sleep(self, t):
        """
        Sleep for given number of seconds, watching for abort requests
        """

        delta = 0.1
        tstart = time.time()
        while True:
            dt = time.time() - tstart
            remain = t - dt
            if remain < 0:
                break
            self.check_running()
            time.sleep(min(delta, remain))
        self.check_running()

    def image(self, wait_imaging_ok=True, mode=None):
        """
        Request and return a snapshot as PIL image

        FIXME: this is an unprocessed image
        Should be returning like snapshot

        Modes:
        -raw: as the data comes on the USB device
            Pixel format follows imager_get_capture_format()
            (8 bit RGB by default; 16 bit / sensor raw if selected)
            Use image_captured() to get the native depth data
        -processed: system specific post processing
            A normal single "snpashot"
        -composite: include any advanced options active
            ex: focus stacking, HDR, stabilization, etc
        """
        return self.image_captured(wait_imaging_ok=wait_imaging_ok,
                                   mode=mode).image

    def wait_imaging_ok(self):
        """
        Wait for camera / stage to settle
        After this a picture can be snapped with acceptable quality
        """

        self.check_running()
        self.session.microscope.kinematics.wait_imaging_ok()

    def image_save_extension(self):
        """
        Return currently selected filename postfix such as .jpg or .tif
        """
        return self.session.image_save_extension()

    def autofocus(self, block=True):
        """
        Autofocus at the current location
        Suggest to call is_idle() if you aren't sure a previous operation is done
        """
        self.check_running()
        self.session.image_processing_thread.auto_focus(
            objective_config=self.session.objective_config(), block=block)

    def get_objectives_config(self):
        """
        Returns the entire objective DB structure
        """
        self.check_running()
        return self.session.microscope.get_objectives().get_full_config()

    def get_objective_config(self):
        """
        Sample entry:

        {
            "magnification": 5,
            "model": "5X",
            "na": 0.1,
            # The auto-generated name on the dropdown menu
            "name": "5X",
            "tsettle_motion": 0.0,
            "um_per_pixel": 1.0,
            "vendor": "Mock",
            "x_view": 0.8,
            "y_view": 0.75
        }
        """
        return self.get_objectives_config()[self.get_active_objective()]

    def get_active_objective(self):
        """
        Returns the name of the active objective
        """
        return self.session.get_active_objective()

    def set_active_objective(self, objective):
        """
        Check if name is in cache
        """
        self.session.set_active_objective(objective)

    def microscope_model(self):
        """
        Config file name
        Will always return something
        """
        return self.session.microscope.model()

    def microscope_serial(self):
        """
        From GRBL
        May not be present and return None
        """
        return self.session.microscope.serial()

    def system_status(self):
        """
        Query some high level system info
        Sample entry:
        {
            "imager": {
                "active": true,
            },
            "motion": {
                "active": false,
            },
            "taking_snapshot": false,
            "autofocusing": false,
            # none if not running
            "planner": {
                # In seconds
                "remaining_time": 123.45,
                "images_captured": 1,
                "images_to_capture": 120,
                # The log message printed to the screen
                "eta_message": "1 / 120, 00:01:23"
            },
        }
        """
        return self.session.microscope.system_status_ts()

    def is_idle(self):
        """
        Is the system idle with the exception of the script engine running
        No movements in progress
        Planner not running
        No pictures being snapped
        """
        self.check_running()
        status = self.system_status()
        # Assume idle if not implemented
        imager_idle = not status.get("imager", {}).get("active", False)
        motion_idle = not status.get("motion", {}).get("active", False)
        planner_idle = status.get("planner") is None
        autofocus_idle = not status.get("autofocusing", False)
        return imager_idle and motion_idle and planner_idle and autofocus_idle

    def pyuscope_version(self):
        """
        Return misc version info

        {
            "tag": "v4.5.0",
            "ver": {
                "major": 4,
                "minor": 5,
                "patch": 0,
            },
            "githash": "ae2b9d253755a11771fd08514fb8369c5fbf2141",
            "dirty": true,
            "description": "v4.5.0-ae2b9d25-dirty" 
        }
        """
        self.check_running()
        return version.get_meta()

    """
    Advanced API
    Try to use the higher level functions first if possible
    These functions:
    -Will change more frequently
    -Be more model dependent
    -Require more advanced software concepts
    """

    def run_plugin(self, plugin, input_=None, button_value=None):
        """
        WARNING: this is very rough and doesn't work well

        TODO:
        -Better Input defaults
        -Imports don't get fully cleaned?
        -Cleanup / stop doesn't work correctly
        """
        p = plugin.Plugin(session=self.session, host=self.host)
        p.check_running = self.check_running
        p.run(input_=input_, button_value=button_value, top_level=False)

    def run_planner_hconfig(self, hconfig, cbsync=None):
        """
        Run motion planner engine
        This required advanced configuration and is currently intended for expert use only
        A planner job may run 24 hours
        So blocking / status primitives are provided with a few options

        You can also use system_status() to check if its done

        hconfig["pconfig"]: planner config
        hconfig["done_callback"]: callback
        hconfig["out_dir_config"] out_dir_config
            dt_prefix
                Prefix with datetime string
                Default: True
            user_basename
                User supplied file name
            extension
                Output file names
                Default: take from GUI? Might not do that right now...
        """

        if hconfig.get("callback_done") is None:
            if cbsync is None:
                cbsync = CBSync(block=True)
            # TODO: pass CBSync instead
            hconfig["callback_done"] = cbsync.callback
        # Token validation
        try:
            # hack: make sure it serializes
            json.dumps(hconfig["pconfig"])
            json.dumps(hconfig.get("out_dir_config", {}))
            #assert "extension" in hconfig, "FIXME"
        except Exception as e:
            raise Exception(f"hconfig failed to validate: {e}")
        self.session.run_planner_hconfigs([hconfig])

        if cbsync is not None:
            # should be a single JSON object in the callback
            return cbsync.check_return_kw1()

    def planner_set_xy3p_corners(self, corners):
        """
        Fill in the XY3P panorama corners as if set with the GUI buttons
        corners: {"ll": pos, "ul": pos, "lr": pos}
        pos: {"x": 1.0, "y": 2.0, "z": 3.0} (z optional)
            Stage position with that corner of the scan area at the image corner
            ie the planner extends the scan by half a view past each position
        Argus: also selects the XY3P tab. Use its Go button to start a scan
        """
        self.check_running()
        self.session.set_planner_xy3p_corners(corners)

    def subsystem_functions(self):
        """
        Return a list of the supported subsystems and associated functions
        """
        return self.session.microscope.subsystem_functions()

    def subsystem_functions_serialized(self):
        """
        Return a list of the supported subsystems and associated functions
        """
        return self.session.microscope.subsystem_functions_serialized()

    def subsystem_function(self, subsystem_, function_, **kwargs):
        """
        Send a command to a subsystem / instrument
        For example
            subsystem: "quad_relay_illuminator"
            function: "on"
            kwargs: {"corner": "top", "brightness": 0.5}
        """
        self.session.microscope.subsystem_function_ts(subsystem_, function_,
                                                      kwargs)

    def subsystem_function_serialized(self, subsystem_, function_, **kwargs):
        """
        Like above, but all values are strings
        Need to be converted to native types
        """
        kwargs = dict(kwargs)
        subsystem = self.session.microscope.subsystems[subsystem_]
        subsystem.function_parse_serialized(function_, kwargs)
        subsystem.function_ts(function_, kwargs)

    def motion(self):
        """
        Get a (thread safe) motion object
        Access to the more powerful but less stable stage API
        """
        return self.session.microscope.motion_ts()

    def imager(self):
        """
        Get a (thread safe) imager object
        Access to the more powerful but less stable camera API
        """
        # Planner uses this directly / is already thread safe
        return self.session.microscope.imager_ts()

    def kinematics(self):
        """
        Get a (thread safe) kinematics object
        Access to the more powerful but less stable system synchronization API
        """
        return self.session.microscope.kinematics_ts()

    def backlash_disable(self, block=True):
        """
        Disable backlash compensation
        """
        self.session.motion_thread.backlash_disable(block=block)

    def backlash_enable(self, block=True):
        """
        Enable backlash compensation
        """
        self.session.motion_thread.backlash_enable(block=block)

    def set_um_per_pixel_raw_1x(self, val):
        """
        Set calibration info
        """
        self.session.set_um_per_pixel_raw_1x(val)

    def imager_get_disp_properties(self):
        """
        Get imager configuration as displayed in the GUI
        Return all current values
        This can be used to enumerate what can be set
        """
        # assert 0, "FIXME: https://github.com/Labsmore/pyuscope/issues/441"
        return self.imager().get_properties()

    def imager_get_disp_property(self, name):
        """
        Get an imager configuration as displayed in the GUI
        """
        return self.imager().get_property(name)

    def imager_set_disp_property(self, name, value):
        """
        Set an imager configuration as displayed in the GUI
        """
        self.imager().set_property(name, value)

    def imager_set_disp_properties(self, properties):
        """
        Set an imager configuration as displayed in the GUI
        """
        self.imager().set_properties(properties)

    def imager_get_exposure(self):
        """
        Exposure in camera units (typically us)
        None if the camera doesn't have an exposure control
        """
        prop = self.session.imager_properties.get_exposure_disp_property()
        if prop is None:
            return None
        return self.imager_get_disp_property(prop)

    def imager_set_exposure(self, value):
        prop = self.session.imager_properties.get_exposure_disp_property()
        if prop is None:
            raise ValueError("Camera doesn't support setting exposure")
        self.imager_set_disp_property(prop, value)

    def imager_get_auto_exposure(self):
        """
        True / False or None if the camera doesn't support auto exposure
        """
        prop = self.session.imager_properties.get_auto_exposure_disp_property(
        )
        if prop is None:
            return None
        return bool(self.imager_get_disp_property(prop))

    def imager_set_auto_exposure(self, enabled):
        prop = self.session.imager_properties.get_auto_exposure_disp_property(
        )
        if prop is None:
            raise ValueError("Camera doesn't support auto exposure")
        self.imager_set_disp_property(prop, bool(enabled))

    def imager_supported_capture_formats(self):
        """
        Pixel formats this camera can capture in
        gstreamer format strings, ex: ["RGB", "ARGB64", "GRAY16_LE", "GRAY8"]
            RGB: 8 bit RGB (default)
            ARGB64: 16 bit RGB
            GRAY16_LE: 16 bit sensor raw (bayer mosaic, not demosaiced)
            GRAY8: 8 bit sensor raw
        Empty if the camera doesn't support switching
        """
        return self.imager().supported_capture_formats()

    def imager_get_capture_format(self):
        """
        Current capture pixel format (see imager_supported_capture_formats)
        """
        return self.imager().get_capture_format()

    def imager_set_capture_format(self, fmt, persist=False):
        """
        Switch the capture pixel format
        The camera is briefly stopped and restarted (~1.5 sec). Exposure etc are kept
        Returns immediately; the next image() waits for a frame in the new format

        persist: also make this the user override (as Video => Capture format menu)
            Default is a temporary change: wrap in try/finally to restore
            the previous format when your script is done
        """
        self.check_running()
        if persist:
            self.session.apply_capture_format_override(fmt)
        else:
            self.imager().set_capture_format(fmt)

    def image_captured(self, wait_imaging_ok=True, mode=None):
        """
        Like image() but return the full CapturedImage object
        Useful for 16 bit / raw captures where .data holds the native
        depth numpy array, along with .pixel_format, .bit_depth, .bayer_pattern
        .save(fn) writes full depth to .png / .tif, 8 bit otherwise
        """
        if mode is None:
            mode = "processed"
        assert mode in ("raw", "processed", "composite")
        self.check_running()
        if wait_imaging_ok:
            self.wait_imaging_ok()
        return self.imager().get_by_mode(mode=mode)

    def white_balance_calibrate(self, quality="full", apply=True):
        """
        Measure software white balance gains from a centered white target
        Set exposure so the target isn't saturated and turn off auto white balance first
        apply: save as the active calibration (applied to processed images)
        Returns [r, g, b] gains
        Raises if the measurement looks bad (ex: saturated); nothing is applied then
        """
        from uscope.cal.context import CalContext
        from uscope.cal.session import CalSession

        name = "white_balance"
        ctx = CalContext(plugin=self, quality=quality, prompt_fn=self.prompt)
        cal_session = CalSession(self.session.microscope, [name],
                                 quality=quality)
        result = cal_session.run_collect_analyze(ctx)[name]
        self.log("White balance: " + result.message)
        if not result.ok:
            raise Exception("White balance failed: " + result.message)
        if apply:
            cal_session.apply_one(name)
        return result.entries[0].value["gains"]

    def white_balance_gains(self):
        """
        Active software white balance gains [r, g, b] or None
        """
        return self.session.microscope.usc.imager.white_balance_gains()

    def white_balance_clear(self):
        """
        Remove the software white balance calibration
        """
        self.session.microscope.cal_store.delete("imager.white_balance")

    def software_gain(self):
        """
        Scalar gain applied to processed images
        """
        return self.session.microscope.usc.imager.software_gain()

    def set_software_gain(self, gain):
        """
        Set (and persist) a scalar gain applied to processed images
        Output is clipped. Useful when exposure / sensor gain can't go higher
        (ex: low light IR imaging). 1.0 is a no-op
        """
        from uscope.cal.store import CalEntry
        gain = float(gain)
        if gain <= 0:
            raise ValueError("Gain must be positive")
        self.session.microscope.cal_store.set(
            CalEntry(key="imager.software_gain", value=gain))

    """
    Plugin defined functions
    """

    def run_test(self):
        """
        The script entry point
        The most important user function
        """
        pass

    def input_config(self):
        """
        Return a dictionary to configure input fields
        See uscope/gui/input_widget.py for the format
        """
        return {}

    def show_run_button(self):
        """
        Instrument might want to disable if they have alternate buttons
        """
        return True


"""
Support instrument functions by passing function calls via a queue
The plugin should be polling the queue
"""


class InstrumentScriptSS(Subsystem):
    def __init__(self, session, plugin, configj):
        super().__init__(session.microscope)
        self._configj = configj
        self.session = session
        assert plugin is not None
        self.plugin = plugin
        self.queue_in = queue.Queue()
        for name in self.functions():
            # Sanity check it exists before passing IPC
            assert getattr(self.plugin, name) is not None

    def function_ts(self, name, kwargs):
        # Make sure its an intended function
        assert name in self.functions(), f"Invalid function {name}"
        self.queue_in.put((name, kwargs))

    def functions(self):
        return self.plugin.functions()

    def name(self):
        # NOTE: this will be added if not specified as JSON key
        return self._configj["name"]

    def parameters(self):
        """
        Passed to instrument_init()
        """
        return self._configj.get("parameters", {})

    def poll_functions(self):
        while True:
            try:
                name, kwargs = self.queue_in.get(timeout=0.1)
            except queue.Empty:
                return
            # Lookup by name and add arguments
            getattr(self.plugin, name)(**kwargs)


class InstrumentScriptPlugin(ScriptingPlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.subsystem = None

    def functions(self):
        return {}

    def set_subsystem(self, subsystem):
        self.subsystem = subsystem

    def instrument_init(self, parameters):
        pass

    def is_button(self):
        return self.get_input().get("button") is not None

    def instrument_button(self, button):
        pass

    def run_test(self):
        assert self.subsystem
        self.instrument_init(self.subsystem.parameters())

        button = self.get_input().get("button")
        if button:
            self.instrument_button(button["value"])
        else:
            while True:
                self.check_running()
                try:
                    self.subsystem.poll_functions()
                except Exception as e:
                    self.log(f"Subsystem got exception: {e}")
                    self.sleep(0.1)


def load_plugin_module(filename):
    spec = importlib.util.spec_from_file_location("pyuscope_plugin", filename)
    plugin_module = importlib.util.module_from_spec(spec)
    sys.modules["pyuscope_plugin"] = plugin_module
    spec.loader.exec_module(plugin_module)
    return plugin_module


class ScriptRunner:
    """
    Load / run / stop a script file
    Shared by the Argus scripting tab and the headless runner
    """
    def __init__(self, session, host):
        self.session = session
        self.host = host
        self.plugin = None
        self.filename = None

    def load(self, filename):
        """
        Raises on failure, leaving nothing loaded
        """
        self.unload()
        plugin_module = load_plugin_module(filename)
        # Entry point: construct the ScriptingPlugin class named Plugin
        self.plugin = plugin_module.Plugin(session=self.session,
                                           host=self.host)
        self.filename = filename
        return self.plugin

    def unload(self):
        self.plugin = None
        self.filename = None

    def is_running(self):
        return self.plugin is not None and self.plugin.is_alive()

    def run(self, input_=None):
        assert self.plugin, "No script loaded"
        if self.is_running():
            raise RuntimeError("Script is already running")
        self.plugin.reset()
        self.plugin._input = input_ if input_ is not None else {}
        self.plugin.start()

    def stop(self):
        if self.plugin:
            self.plugin.shutdown()

    def kill(self):
        """
        Force an exception into the script thread
        Returns False if there was nothing to kill
        """
        if not self.is_running() or not self.plugin.ident:
            return False
        thread_id = self.plugin.ident
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_long(thread_id), ctypes.py_object(TestKilled))
        if res > 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_long(thread_id), 0)
            raise RuntimeError("Exception raise failure")
        return res == 1

    def attach_instrument(self, configj):
        """
        Register the loaded InstrumentScriptPlugin as a microscope subsystem
        """
        subsystem = InstrumentScriptSS(session=self.session,
                                       plugin=self.plugin,
                                       configj=configj)
        self.session.microscope.add_subsystem(subsystem)
        self.plugin.set_subsystem(subsystem)
        return subsystem


"""
Input config helpers
Equivalent of InputWidget for non-GUI use
"""


def input_defaults(config):
    """
    Return the values InputWidget.getValues() would give with nothing edited
    """
    ret = {}
    for label, lconfig in config.items():
        widget = lconfig["widget"]
        key = lconfig.get("key", label)
        if widget == "QLineEdit":
            val = lconfig.get("default")
            val = "" if val is None else str(val)
            if not val and lconfig.get("empty_as_none", False):
                continue
            ret[key] = input_convert(lconfig, label, val)
        elif widget == "QComboBox":
            val = lconfig.get("default")
            if not val:
                values = lconfig.get("values", [])
                val = values[0] if values else ""
            ret[key] = str(val)
        elif widget in ("QPushButton", "QPushButtons"):
            pass
        else:
            raise ValueError(f"bad config: unknown widget type {widget}")
    return ret


def input_convert(lconfig, label, val):
    if "type" in lconfig:
        try:
            return lconfig["type"](val)
        except ValueError:
            raise ValueError(
                f"bad input on {label} for type {lconfig['type']}: {val}")
    return val


def input_set(config, values, overrides):
    """
    Apply string overrides (ex: from the command line) by key or label
    Converts to the configured type
    """
    for k, v in overrides.items():
        for label, lconfig in config.items():
            if k in (label, lconfig.get("key", label)):
                values[lconfig.get("key", label)] = input_convert(
                    lconfig, label, v)
                break
        else:
            # Not declared: pass through as is
            values[k] = v
    return values


def input_buttons(config):
    """
    Return a list of button events (as passed in input["button"])
    """
    ret = []
    for label, lconfig in config.items():
        widget = lconfig["widget"]
        if widget == "QPushButton":
            ret.append({
                "group": label,
                "label": label,
                "value": lconfig.get("value"),
            })
        elif widget == "QPushButtons":
            for button_label, value in lconfig["buttons"].items():
                ret.append({
                    "group": label,
                    "label": button_label,
                    "value": value,
                })
    return ret


def input_find_button(config, name):
    """
    Find a button by label or (string) value
    """
    buttons = input_buttons(config)
    for button in buttons:
        if name == button["label"] or name == button["value"]:
            return button
    choices = [
        b["value"] if isinstance(b["value"], str) else b["label"]
        for b in buttons
    ]
    raise ValueError(f"Unknown button {name}. Choices: {choices}")
