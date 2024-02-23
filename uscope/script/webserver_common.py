from flask import current_app, request
from http import HTTPStatus
import json
import base64
from io import BytesIO

import functools

plugin = None


def except_wrap(func):
    @functools.wraps(func)
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
    @except_wrap
    def position():
        position = plugin.position()
        return {
            'data': position,
            'status': HTTPStatus.OK,
        }

    @app.route('/run/move_absolute', methods=['GET', 'POST'])
    def move_absolute():
        @except_wrap
        def wrap():
            block = bool(request.args.get("block", default=True, type=int))
            data = {}
            for axis in "xyz":
                this_pos = request.args.get("axis." + axis,
                                            default=None,
                                            type=float)
                if this_pos is not None:
                    data[axis] = this_pos
            plugin.move_absolute(data, block=block)
            return {'status': HTTPStatus.OK}

        return wrap()

    @app.route('/run/move_relative', methods=['GET', 'POST'])
    def move_relative():
        @except_wrap
        def wrap():
            block = bool(request.args.get("block", default=True, type=int))
            data = {}
            for axis in "xyz":
                this_pos = request.args.get("axis." + axis,
                                            default=None,
                                            type=float)
                if this_pos is not None:
                    data[axis] = this_pos
            plugin.move_relative(data, block=block)
            return {'status': HTTPStatus.OK}

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

    @app.route('/run/wait_imaging_ok', methods=['GET'])
    def wait_imaging_ok():
        @except_wrap
        def wrap():
            plugin.wait_imaging_ok()
            return {
                'status': HTTPStatus.OK,
            }

        return wrap()

    @app.route('/get/image', methods=['GET'])
    def get_image():
        @except_wrap
        def wrap():
            wait_imaging_ok = bool(
                request.args.get("wait_imaging_ok", default=True, type=int))
            raw = bool(request.args.get("raw", default=False, type=int))
            format_ = request.args.get("format", default="JPEG", type=str)
            pil_image = plugin.image(wait_imaging_ok=wait_imaging_ok, raw=raw)
            buffered = BytesIO()
            pil_image.save(buffered, format=format_)
            img_str = base64.b64encode(buffered.getvalue()).decode('ascii')
            return {
                'status': HTTPStatus.OK,
                'data': {
                    "format": format_,
                    "base64": img_str,
                }
            }

        return wrap()
