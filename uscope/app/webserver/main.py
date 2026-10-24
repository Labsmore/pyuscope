"""
Control the microscope over HTTP: browser UI + JSON API

Examples:
./app/webserver.py
./app/webserver.py --microscope mock-grbl --port 8081
./app/webserver.py --network
./app/webserver.py --api-only

Then browse to http://localhost:8080/ or use the API, ex:
curl 'http://localhost:8080/get/position'; echo
See uscope/script/webserver_common.py for the full API
"""

from uscope.session import HeadlessSession
from uscope.scripting import (ScriptRunner, HeadlessScriptHost,
                              input_defaults, input_find_button)
from uscope.script import webserver_common
from uscope import util
from uscope import app_log
from uscope.config import get_bc

import argparse
import os
import sys
import traceback

SCRIPT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "script")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Control the microscope over HTTP (browser UI + JSON API)")
    parser.add_argument("--microscope",
                        help="Which microscope config to use")
    parser.add_argument("--port",
                        type=int,
                        default=webserver_common.DEFAULT_PORT,
                        help="Port to listen on (default: %(default)s)")
    parser.add_argument(
        "--network",
        action="store_true",
        help="Accept connections from other computers (default: localhost only)")
    parser.add_argument("--api-only",
                        action="store_true",
                        help="Serve the JSON API without the browser UI")
    parser.add_argument(
        "--imager",
        choices=("config", "mock"),
        default="config",
        help=
        "config: camera from the microscope config. mock: no camera (blank images)"
    )
    parser.add_argument(
        "--no-argus-state",
        action="store_true",
        help=
        "Don't start with the objective / global scalar last saved by Argus")
    return parser.parse_args()


def run(args):
    session = HeadlessSession(microscope_name=args.microscope,
                              imager="mock" if args.imager == "mock" else None,
                              argus_state=not args.no_argus_state)
    util.register_emergency_stop(session.motion.emergency_stop)
    try:
        host = HeadlessScriptHost(image_dir=os.path.join(
            session.bc.get_data_dir(), "script_images"),
                                  assume_yes=True)
        runner = ScriptRunner(session=session, host=host)
        script = "webserver_api.py" if args.api_only else "webserver_page.py"
        plugin = runner.load(os.path.join(SCRIPT_DIR, script))
        config = plugin.input_config()
        values = input_defaults(config)
        values["port"] = args.port
        values["button"] = input_find_button(
            config, "network" if args.network else "localhost")

        where = "<this computer>" if args.network else "localhost"
        print(f"pyuscope web server: http://{where}:{args.port}/" +
              (" (API only)" if args.api_only else ""))
        print("Press Ctrl+C to stop")
        runner.run(values)
        try:
            while not host.done_event.wait(0.2):
                pass
        except KeyboardInterrupt:
            print("")
            print("Stopping web server (^C again to force)")
            runner.stop()
            host.done_event.wait()
            plugin.join(timeout=1.0)
            return 0
        plugin.join(timeout=1.0)
        # Server stopped on its own (ex: port already in use)
        if not plugin.succeeded():
            print("Web server stopped: %s" %
                  (plugin.result_message or "failed to start"))
            return 1
        return 0
    finally:
        session.shutdown()


def main():
    args = parse_args()
    # Separate from Argus' log.txt as both may run at once
    app_log.install(os.path.join(get_bc().get_data_dir(),
                                 "webserver_log.txt"),
                    stdout=True)
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
