from uscope.app.argus.scripting import ArgusScriptingPlugin
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

    def shutdown(self):
        self.server.shutdown()
        self.server.join()
        super().shutdown()

@app.route('/get/activeobjective', methods=['GET'])
def active_objective():
    objective = current_app.plugin.get_active_objective()
    data = {'objective': objective}
    return json.dumps({
        'data': data,
        'status': HTTPStatus.OK,
    })

@app.route('/set/activeobjective/<objective>', methods=['GET', 'POST'])
def active_objective_set(objective):
    plugin = current_app.plugin
    try:
        # Validate objective name before sending request
        if objective not in plugin.objectives.names():
            raise ValueError  # No objective name found
        plugin.set_active_objective(objective)
        return json.dumps({'status': HTTPStatus.OK})
    except Exception as e:
        print(e)
        return json.dumps({'status': HTTPStatus.CONFLICT, 'error': "Invalid Input Value"})
