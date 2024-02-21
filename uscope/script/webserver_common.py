from flask import current_app, request
from http import HTTPStatus
import json

plugin = None


def make_app(app):
    @app.route('/get/position', methods=['GET'])
    def position():
        position = plugin.position()
        plugin.log_verbose(f"/get/position")
        return json.dumps({
            'data': {
                'position': position
            },
            'status': HTTPStatus.OK,
        })

    @app.route('/set/position', methods=['GET', 'POST'])
    def pos_set():
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

    @app.route('/get/system_status', methods=['GET'])
    def system_status():
        system_status = plugin.system_status()
        plugin.log_verbose(f"/get/system_status")
        return json.dumps({
            'data': {
                'system_status': system_status
            },
            'status': HTTPStatus.OK,
        })

    @app.route('/get/is_idle', methods=['GET'])
    def is_idle():
        is_idle = plugin.is_idle()
        plugin.log_verbose(f"/get/is_idle")
        return json.dumps({
            'data': {
                'is_idle': is_idle
            },
            'status': HTTPStatus.OK,
        })

    @app.route('/get/pyuscope_version', methods=['GET'])
    def pyuscope_version():
        pyuscope_version = plugin.is_idle()
        plugin.log_verbose(f"/get/pyuscope_version")
        return json.dumps({
            'data': {
                'pyuscope_version': pyuscope_version
            },
            'status': HTTPStatus.OK,
        })

    @app.route('/get/objectives', methods=['GET'])
    def objectives():
        objectives = plugin.get_objectives_config()
        plugin.log_verbose(f"/get/objectives")
        return json.dumps({
            'data': {
                'objectives': objectives
            },
            'status': HTTPStatus.OK,
        })

    @app.route('/get/active_objective', methods=['GET'])
    def active_objective():
        objective = plugin.get_active_objective()
        plugin.log_verbose(f"/get/active_objective: '{objective}'")
        return json.dumps({
            'data': {
                'objective': objective
            },
            'status': HTTPStatus.OK,
        })

    @app.route('/set/active_objective/<objective>', methods=['GET', 'POST'])
    def active_objective_set(objective):
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

    @app.route('/get/imager/imager/get_disp_properties', methods=['GET'])
    def imager_get_disp_properties():
        properties = plugin.imager_get_disp_properties()
        plugin.log_verbose(f"/get/imager/get_disp_properties")
        return json.dumps({
            'data': {
                'properties': properties
            },
            'status': HTTPStatus.OK,
        })

    @app.route('/get/imager/disp_property/<name>', methods=['GET'])
    def imager_get_disp_property(name):
        val = plugin.imager_get_disp_property(name)
        plugin.log_verbose(f"/get/imager/disp_property/<name>: '{val}'")
        return json.dumps({
            'data': {
                'value': val
            },
            'status': HTTPStatus.OK,
        })

    @app.route('/set/imager/disp_properties', methods=['GET', 'POST'])
    def imager_set_disp_properties():
        try:
            properties = {}
            for k, v in request.args.items():
                # FIXME: not everything is a float
                properties[k] = float(v)
            # Validate objective name before sending request
            plugin.log_verbose(f"/set/imager/disp_property/<name>/<value>")
            plugin.imager_set_disp_properties(properties)
            return json.dumps({'status': HTTPStatus.OK})
        except Exception as e:
            print(e)
            return json.dumps({
                'status': HTTPStatus.INTERNAL_SERVER_ERROR,
                'error': f"{type(e)}: {e}"
            })
