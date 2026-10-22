"""
Run a script without the GUI

Examples:
./app/headless.py --microscope mock-grbl uscope/script/hello.py
./app/headless.py --microscope mock-grbl uscope/script/test/test_all.py --button all
./app/headless.py uscope/script/test/test_input.py --input distance=2.5 --input Mode=Fast
./app/headless.py uscope/script/webserver_api.py --button localhost
./app/headless.py --microscope mock-grbl --imager-config '{"source": "gst-toupcamsrc", "width": 5440, "height": 3648, "scalar": 0.5}' uscope/script/test/test_image.py

Inputs default to the script's input_config() defaults
--config loads a saved script config (as from the Scripting tab "Save config")
--input KEY=VALUE overrides by key or label
--button selects a button by label or value

show_image() output is saved under --image-dir
"""

from uscope.session import HeadlessSession
from uscope.scripting import (ScriptRunner, HeadlessScriptHost,
                              input_defaults, input_set, input_find_button,
                              input_buttons)
from uscope.util import readj
from uscope import util

import argparse
import json
import os
import sys
import traceback


def parse_args():
    parser = argparse.ArgumentParser(description="Run a script headless")
    parser.add_argument("script", help="Script .py file")
    parser.add_argument("--microscope",
                        help="Which microscope config to use")
    parser.add_argument("--config",
                        help="Script input config (.json / .j5)")
    parser.add_argument("--input",
                        action="append",
                        default=[],
                        metavar="KEY=VALUE",
                        help="Override an input value")
    parser.add_argument("--button", help="Press a button (label or value)")
    parser.add_argument("--image-dir",
                        help="Where show_image() saves images")
    parser.add_argument("--yes",
                        action="store_true",
                        help="Answer the first choice to all prompts")
    parser.add_argument("--list-inputs",
                        action="store_true",
                        help="Print the script's inputs and buttons then exit")
    parser.add_argument(
        "--imager",
        choices=("config", "mock"),
        default="config",
        help=
        "config: camera from the microscope config. mock: no camera (blank images)"
    )
    parser.add_argument(
        "--imager-config",
        help=
        "Override the microscope config imager section (.j5 file or inline JSON)"
    )
    return parser.parse_args()


def load_imager_config(arg):
    if not arg:
        return None
    if os.path.exists(arg):
        return readj(arg)
    return json.loads(arg)


def build_input(config, args):
    values = input_defaults(config)
    if args.config:
        values.update(readj(args.config))
    overrides = {}
    for kv in args.input:
        if "=" not in kv:
            raise ValueError(f"--input expects KEY=VALUE, got {kv}")
        k, v = kv.split("=", 1)
        overrides[k] = v
    input_set(config, values, overrides)
    if args.button:
        values["button"] = input_find_button(config, args.button)
    return values


def list_inputs(config):
    print("Inputs (key: default)")
    for k, v in input_defaults(config).items():
        print(f"  {k}: {v!r}")
    buttons = input_buttons(config)
    if buttons:
        print("Buttons (label: value)")
        for button in buttons:
            print(f"  {button['label']}: {button['value']!r}")


def run(args):
    session = HeadlessSession(
        microscope_name=args.microscope,
        imager="mock" if args.imager == "mock" else None,
        imager_config=load_imager_config(args.imager_config))
    util.register_emergency_stop(session.motion.emergency_stop)
    try:
        image_dir = args.image_dir
        if image_dir is None:
            image_dir = os.path.join(session.bc.get_data_dir(),
                                     "script_images")
        host = HeadlessScriptHost(image_dir=image_dir, assume_yes=args.yes)
        runner = ScriptRunner(session=session, host=host)
        plugin = runner.load(args.script)
        config = plugin.input_config()
        if args.list_inputs:
            list_inputs(config)
            return 0

        runner.run(build_input(config, args))
        try:
            while not host.done_event.wait(0.2):
                pass
        except KeyboardInterrupt:
            print("")
            print("Interrupted: stopping script (^C again to force)")
            runner.stop()
            host.done_event.wait()
        plugin.join(timeout=1.0)

        if plugin.succeeded():
            if plugin.new_defaults:
                print(f"New input defaults: {plugin.new_defaults}")
            print("Script completed ok")
            return 0
        print(f"Script completed w/ issue: {plugin.result_message}")
        return 1
    finally:
        session.shutdown()


def main():
    args = parse_args()
    try:
        sys.exit(run(args))
    except KeyboardInterrupt:
        print("Forced exit")
        util.run_emergency_stops()
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        util.run_emergency_stops()
        sys.exit(1)


if __name__ == "__main__":
    main()
