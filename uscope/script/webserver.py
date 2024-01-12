"""
Ubuntu 20.04 setup:
sudo apt-get install -y python3-werkzeug
sudo pip3 install Flask>=2.2.2

Fixes:
<class 'ImportError'>: cannot import name 'escape' from 'jinja2' (/usr/local/lib/python3.8/dist-packages/jinja2/__init__.py)
https://stackoverflow.com/questions/71718167/importerror-cannot-import-name-escape-from-jinja2



Sample commands

# Get this microscope's objective database
$ curl 'http://localhost:8080/get/objectives'; echo

# Get the current objective
$ curl 'http://localhost:8080/get/active_objective'; echo
{"data": {"objective": "5X"}, "status": 200}

# Change to a new objective
$ curl 'http://localhost:8080/set/active_objective/5X'; echo
{"status": 200}
# POST requests also work
$ curl -X POST 'http://localhost:8080/set/active_objective/10X'; echo
# With spaces
$ curl 'http://localhost:8080/set/active_objective/100X%20Oil'; echo
$ curl 'http://localhost:8080/get/active_objective'; echo
{"data": {"objective": "100X Oil"}, "status": 200}
# An invalid value
$ curl 'http://localhost:8080/set/active_objective/1000X'; echo
{"status": 400}
"""

from uscope.gui.scripting import ArgusScriptingPlugin
from multiprocessing import Process
from flask import Flask, request, current_app
from http import HTTPStatus
import json
from threading import Thread
from werkzeug.serving import make_server

app = Flask(__name__)
SERVER_PORT = 8080
HOST = '127.0.0.1'


class ServerThread(Thread):
    def __init__(self):
        super().__init__()
        self.server = make_server(host=HOST, port=SERVER_PORT, app=app)
        self.ctx = app.app_context()
        self.ctx.push()

    def run(self):
        self.server.serve_forever()

    def shutdown(self):
        self.server.shutdown()


class Plugin(ArgusScriptingPlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.verbose = True

    def log_verbose(self, msg):
        if self.verbose:
            self.log(msg)

    def run_test(self):
        self.log(f"Running Pyuscope Webserver Plugin on port: {SERVER_PORT}")
        self.objectives = self._ac.microscope.get_objectives()
        # Keep a reference to this plugin
        app.plugin = self
        self.server = ServerThread()
        self.server.start()
        # Keep plugin alive while server is running
        while self.server and self.server.is_alive():
            self.sleep(0.1)

    def cleanup(self):
        self.server.shutdown()
        self.server.join()


@app.route('/get/objectives', methods=['GET'])
def objectives():
    plugin = current_app.plugin
    objectives = plugin.get_objectives_config()
    plugin.log_verbose(f"/get/objectives")
    data = {'objectives': objectives}
    return json.dumps({
        'data': data,
        'status': HTTPStatus.OK,
    })


@app.route('/get/active_objective', methods=['GET'])
def active_objective():
    plugin = current_app.plugin
    objective = plugin.get_active_objective()
    plugin.log_verbose(f"/get/active_objective: '{objective}'")
    data = {'objective': objective}
    return json.dumps({
        'data': data,
        'status': HTTPStatus.OK,
    })


@app.route('/set/active_objective/<objective>', methods=['GET', 'POST'])
def active_objective_set(objective):
    plugin = current_app.plugin
    try:
        # Validate objective name before sending request
        plugin.log_verbose(f"/set/active_objective/{objective}")
        if objective not in plugin.objectives.names():
            plugin.log(f"WARNING: objective '{objective}' not found")
            return json.dumps({'status': HTTPStatus.BAD_REQUEST})
        plugin.set_active_objective(objective)
        return json.dumps({'status': HTTPStatus.OK})
    except Exception as e:
        print(e)
        return json.dumps({
            'status': HTTPStatus.INTERNAL_SERVER_ERROR,
            'error': f"{type(e)}: {e}"
        })
