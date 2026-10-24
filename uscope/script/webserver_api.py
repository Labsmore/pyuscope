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

from uscope.scripting import ScriptingPlugin
from uscope.script import webserver_common

from flask import Flask

app = Flask(__name__)


class Plugin(ScriptingPlugin):
    def __init__(self, *args, **kwargs):
        webserver_common.plugin = self
        super().__init__(*args, **kwargs)
        self.verbose = True
        self.server = None

    def input_config(self):
        return webserver_common.input_config()

    def log_verbose(self, msg):
        if self.verbose:
            self.log(msg)

    def show_run_button(self):
        return False

    def run_test(self):
        vals = self.get_input()
        host = webserver_common.bind_host(vals)
        port = vals["port"]
        self.log(f"Running pyuscope webserver (bind: {host}) on port {port}")
        self.objectives = self.session.microscope.get_objectives()
        # Keep a reference to this plugin
        app.plugin = self
        self.server = webserver_common.ServerThread(app, host=host, port=port)
        self.server.start()
        # Keep plugin alive while server is running
        while self.server and self.server.is_alive():
            self.sleep(0.1)

    def cleanup(self):
        if self.server:
            self.server.shutdown()
            self.server.join()
            self.server = None


webserver_common.make_app(app)
