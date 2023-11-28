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
from uscope.app.argus.scripting import ArgusScriptingPlugin
from flask import Flask, current_app, request, render_template, send_from_directory
from http import HTTPStatus
import json
from werkzeug.serving import make_server
from flask_cors import CORS
import cv2
from flask_socketio import SocketIO
import base64
import numpy as np

FLUTTER_WEB_DIR = "web"
app = Flask(__name__, template_folder=FLUTTER_WEB_DIR)
CORS(app)
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
            self.socket = MySocket(app, cors_allowed_origins="*")

        # Keep a reference to this plugin
        app.plugin = self
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
            'status': HTTPStatus.CONFLICT,
            'error': "Invalid Input Value"
        })


@app.route('/get/pos', methods=['GET'])
def pos_get():
    plugin = current_app.plugin
    plugin.log_verbose(f"/get/pos")
    return json.dumps({'status': HTTPStatus.OK, 'data': plugin.pos()})


@app.route('/set/pos', methods=['GET', 'POST'])
def pos_set():
    plugin = current_app.plugin
    try:
        plugin.log_verbose(f"/set/pos?{request.query_string.decode()}")
        # Validate values - default bool value is to handle
        # excluding null values for absolute movement
        x = request.args.get("x", default=False, type=float)
        y = request.args.get("y", default=False, type=float)
        z = request.args.get("z", default=False, type=float)
        data = {}
        if x is not False:
            data["x"] = x
        if y is not False:
            data["y"] = y
        if z is not False:
            data["z"] = z
        move_relative = request.args.get("relative", default=0, type=int)
        # Do not need to move if all axes are zero
        if move_relative == 1 and (data.get("x") or data.get("y")
                                   or data.get("z")):
            plugin.move_relative(data)
        else:
            plugin.move_absolute(data)
        data = {}
        data.update(plugin.pos())
        return json.dumps({'status': HTTPStatus.OK, 'data': plugin.pos()})
    except Exception as e:
        print(e)
        return json.dumps({
            'status': HTTPStatus.CONFLICT,
            'error': "Invalid Input Value"
        })
