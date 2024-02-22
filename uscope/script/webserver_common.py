from flask import current_app, request
from http import HTTPStatus
import json

plugin = None


def except_wrap(func):
    def wrapper():
        try:
            plugin.log_verbose(f"{request.url}")
            return json.dumps(func())
        except Exception as e:
            print(e)
            return json.dumps({
                'status': HTTPStatus.INTERNAL_SERVER_ERROR,
                'error': f"{type(e)}: {e}"
            })

    return wrapper


def make_app(app):
    @app.route('/get/position', methods=['GET'])
    def position():
        @except_wrap
        def wrap():
            position = plugin.position()
            return {
                'data': position,
                'status': HTTPStatus.OK,
            }

        return wrap()

    @app.route('/set/position', methods=['GET', 'POST'])
    def position_set():
        @except_wrap
        def wrap():
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
            return {'status': HTTPStatus.OK, 'data': plugin.pos()}

        return wrap()

    @app.route('/get/system_status', methods=['GET'])
    def system_status():
        @except_wrap
        def wrap():
            system_status = plugin.system_status()
            return {
                'data': system_status,
                'status': HTTPStatus.OK,
            }

        return wrap()

    @app.route('/get/is_idle', methods=['GET'])
    def is_idle():
        @except_wrap
        def wrap():
            is_idle = plugin.is_idle()
            return {
                'data': is_idle,
                'status': HTTPStatus.OK,
            }

        return wrap()

    @app.route('/get/pyuscope_version', methods=['GET'])
    def pyuscope_version():
        @except_wrap
        def wrap():
            pyuscope_version = plugin.pyuscope_version()
            return {
                'data': pyuscope_version,
                'status': HTTPStatus.OK,
            }

        return wrap()

    @app.route('/get/objectives', methods=['GET'])
    def objectives():
        @except_wrap
        def wrap():
            objectives = plugin.get_objectives_config()
            return {
                'data': objectives,
                'status': HTTPStatus.OK,
            }

        return wrap()

    @app.route('/get/active_objective', methods=['GET'])
    def active_objective():
        @except_wrap
        def wrap():
            objective = plugin.get_active_objective()
            return {
                'data': objective,
                'status': HTTPStatus.OK,
            }

        return wrap()

    @app.route('/set/active_objective/<objective>', methods=['GET', 'POST'])
    def active_objective_set(objective):
        @except_wrap
        def wrap():
            # Validate objective name before sending request
            if objective not in plugin.objectives.names():
                plugin.log(f"WARNING: objective '{objective}' not found")
                return {'status': HTTPStatus.BAD_REQUEST}
            plugin.set_active_objective(objective)
            return {'status': HTTPStatus.OK}

        return wrap()

    @app.route('/get/imager/imager/get_disp_properties', methods=['GET'])
    def imager_get_disp_properties():
        @except_wrap
        def wrap():
            properties = plugin.imager_get_disp_properties()
            return {
                'data': properties,
                'status': HTTPStatus.OK,
            }

        return wrap()

    @app.route('/get/imager/disp_property/<name>', methods=['GET'])
    def imager_get_disp_property(name):
        @except_wrap
        def wrap():
            val = plugin.imager_get_disp_property(name)
            return {
                'data': val,
                'status': HTTPStatus.OK,
            }

        return wrap()

    @app.route('/set/imager/disp_properties', methods=['GET', 'POST'])
    def imager_set_disp_properties():
        @except_wrap
        def wrap():
            properties = {}
            for k, v in request.args.items():
                # FIXME: not everything is a float
                properties[k] = float(v)
            # Validate objective name before sending request
            plugin.imager_set_disp_properties(properties)
            return {'status': HTTPStatus.OK}

        return wrap()

    @app.route('/run/autofocus', methods=['GET', 'POST'])
    def autofocus():
        @except_wrap
        def wrap():
            block = bool(request.args.get("block", default=True, type=int))
            plugin.autofocus(block=block)
            return {'status': HTTPStatus.OK}

        return wrap()

    @app.route('/get/subsystem_functions', methods=['GET'])
    def subsystem_functions():
        @except_wrap
        def wrap():
            return {
                'data': plugin.subsystem_functions_serialized(),
                'status': HTTPStatus.OK,
            }

        return wrap()

    @app.route('/run/subsystem/<subsystem>/<function>',
               methods=['GET', 'POST'])
    def subsystem_function(subsystem, function):
        @except_wrap
        def wrap():
            kwargs = dict(request.args.items())
            print("debug", subsystem, function, kwargs)
            plugin.subsystem_function_serialized(subsystem_=subsystem,
                                                 function_=function,
                                                 **kwargs)
            return {'status': HTTPStatus.OK}

        return wrap()
