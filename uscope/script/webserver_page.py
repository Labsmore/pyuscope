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
from uscope.scripting import ScriptingPlugin
from uscope.script import webserver_common

from flask import Flask, current_app, request, render_template, send_from_directory
from flask_cors import CORS
import cv2
from flask_socketio import SocketIO
import base64
import numpy as np
import os

FLUTTER_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "web")
app = Flask(__name__, template_folder=FLUTTER_WEB_DIR)
CORS(app)


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
        if not self.clients:
            self.start_background_task(self.video_feed, current_app.plugin)
        self.clients.add(request.sid)
        plugin = current_app.plugin
        plugin.log_verbose(
            f"Client connected: connections = {len(self.clients)}")
        self.emit('client_connected')

    def on_disconnection(self):
        self.clients.remove(request.sid)
        plugin = current_app.plugin
        plugin.log_verbose(
            f"Client disconnected: connections = {len(self.clients)}")

    def video_feed(self, plugin):
        while plugin.server:
            image = plugin.image()
            string_data = image_to_base64(image)
            self.emit('video_feed_back', string_data)

    def disconnect_clients(self):
        self.emit("disconnect")


class Plugin(ScriptingPlugin):
    def __init__(self, *args, **kwargs):
        webserver_common.plugin = self
        super().__init__(*args, **kwargs)
        self.verbose = True
        self.frame = None
        self.socket = None
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
        self.log(
            f"Running pyuscope web page + API (bind: {host}) on port {port}")
        self.objectives = self.session.microscope.get_objectives()
        if not self.socket:
            self.socket = MySocket(app, cors_allowed_origins="*")

        # Keep a reference to this plugin
        app.plugin = self
        self.server = webserver_common.ServerThread(app,
                                                    host=host,
                                                    port=port,
                                                    threaded=True)
        self.server.start()
        while self.server and self.server.is_alive():
            self.sleep(0.1)

    def cleanup(self):
        if self.server:
            if self.socket:
                self.socket.disconnect_clients()
            server = self.server
            # Stops the video feed loop
            self.server = None
            server.shutdown()
            server.join()


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
    data_list = str(name).split('/')
    dir_name = FLUTTER_WEB_DIR
    if len(data_list) > 1:
        for i in range(0, len(data_list) - 1):
            dir_name += '/' + data_list[i]
    return send_from_directory(dir_name, data_list[-1])
