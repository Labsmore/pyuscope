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

# Get the current position
$ curl 'http://localhost:8080/get/pos'; echo

# Move absolute position
$ curl 'http://localhost:8080/set/pos/?x=1&z=-2'; echo
# Move relative position
$ curl -X POST 'http://localhost:8080/set/pos/?y=1&x=-1&relative=1'; echo
"""
from uscope.gui.scripting import ArgusScriptingPlugin
from uscope.script import webserver_common

from flask import Flask, current_app, request, render_template, send_from_directory
import json
import os
from werkzeug.serving import make_server
import cv2
from flask_socketio import SocketIO
import base64
import numpy as np

FLUTTER_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
app = Flask(__name__, template_folder=FLUTTER_WEB_DIR)
SERVER_PORT = 8080
HOST = '127.0.0.1'


def image_to_base64(p_img):
    frame = cv2.cvtColor(np.array(p_img), cv2.COLOR_RGB2BGR)
    img_encode = cv2.imencode('.jpg', frame)[1]
    string_data = base64.b64encode(img_encode).decode('utf-8')
    # b64_src = 'data:image/jpeg;base64,'
    b64_src = ''
    return b64_src + string_data


class MySocket(SocketIO):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.clients = set()
        self.on_event('connect', self.on_connection)
        self.on_event('disconnect', self.on_disconnection)

    def on_connection(self):
        first_client = not self.clients
        self.clients.add(request.sid)
        plugin = current_app.plugin
        if first_client:
            self.start_background_task(self.video_feed, plugin)
        plugin.log_verbose(
            f"Client connected: connections = {len(self.clients)}")
        self.emit('client_connected')

    def on_disconnection(self):
        self.clients.discard(request.sid)
        plugin = current_app.plugin
        plugin.log_verbose(
            f"Client disconnected: connections = {len(self.clients)}")

    def video_feed(self, plugin):
        while plugin.server and self.clients:
            image = plugin.image()
            string_data = image_to_base64(image)
            self.emit('video_feed_back', string_data)
            self.sleep(0.1)

    def disconnect_clients(self):
        self.emit("disconnect")
        self.clients.clear()


class Plugin(ArgusScriptingPlugin):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.verbose = True
        self.frame = None
        self.socket = None

    def log_verbose(self, msg):
        if self.verbose:
            self.log(msg)

    def run_test(self):
        self.log(f"Running Pyuscope Webserver Plugin on port: {SERVER_PORT}")
        self.objectives = self._ac.microscope.get_objectives()
        if not self.socket:
            self.socket = MySocket(app)

        # Keep a reference to this plugin
        app.plugin = self
        webserver_common.plugin = self
        self.server = make_server(host=HOST,
                                  port=SERVER_PORT,
                                  app=app,
                                  threaded=True)
        self.ctx = app.app_context()
        self.ctx.push()
        self.server.serve_forever(0.1)

    def shutdown(self):
        self.socket.disconnect_clients()
        self.server.shutdown()
        self.server.server_close()
        super().shutdown()
        self.server = None


webserver_common.make_app(app)


@app.route('/')
@app.route('/index.html')
def index():
    return render_template('index.html')


@app.route('/<path:name>')
def return_flutter_doc(name):
    """
    Required to serve flutter web docs
    """
    return send_from_directory(FLUTTER_WEB_DIR, name)
