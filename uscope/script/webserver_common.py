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
    @except_wrap
    def move_absolute():
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

    @app.route('/run/move_relative', methods=['GET', 'POST'])
    @except_wrap
    def move_relative():
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

    @app.route('/get/system_status', methods=['GET'])
    @except_wrap
    def system_status():
        system_status = plugin.system_status()
        return {
            'data': system_status,
            'status': HTTPStatus.OK,
        }

    @app.route('/get/is_idle', methods=['GET'])
    @except_wrap
    def is_idle():
        is_idle = plugin.is_idle()
        return {
            'data': is_idle,
            'status': HTTPStatus.OK,
        }

    @app.route('/get/pyuscope_version', methods=['GET'])
    @except_wrap
    def pyuscope_version():
        pyuscope_version = plugin.pyuscope_version()
        return {
            'data': pyuscope_version,
            'status': HTTPStatus.OK,
        }

    @app.route('/get/objectives', methods=['GET'])
    @except_wrap
    def objectives():
        objectives = plugin.get_objectives_config()
        return {
            'data': objectives,
            'status': HTTPStatus.OK,
        }

    @app.route('/get/active_objective', methods=['GET'])
    @except_wrap
    def active_objective():
        objective = plugin.get_active_objective()
        return {
            'data': objective,
            'status': HTTPStatus.OK,
        }

    @app.route('/set/active_objective/<objective>', methods=['GET', 'POST'])
    @except_wrap
    def active_objective_set(objective):
        # Validate objective name before sending request
        if objective not in plugin.objectives.names():
            plugin.log(f"WARNING: objective '{objective}' not found")
            return {'status': HTTPStatus.BAD_REQUEST}
        plugin.set_active_objective(objective)
        return {'status': HTTPStatus.OK}

    @app.route('/get/imager/imager/get_disp_properties', methods=['GET'])
    @except_wrap
    def imager_get_disp_properties():
        properties = plugin.imager_get_disp_properties()
        return {
            'data': properties,
            'status': HTTPStatus.OK,
        }

    @app.route('/get/imager/disp_property/<name>', methods=['GET'])
    @except_wrap
    def imager_get_disp_property(name):
        val = plugin.imager_get_disp_property(name)
        return {
            'data': val,
            'status': HTTPStatus.OK,
        }

    @app.route('/set/imager/disp_properties', methods=['GET', 'POST'])
    @except_wrap
    def imager_set_disp_properties():
        properties = {}
        for k, v in request.args.items():
            # FIXME: not everything is a float
            properties[k] = float(v)
        # Validate objective name before sending request
        plugin.imager_set_disp_properties(properties)
        return {'status': HTTPStatus.OK}

    @app.route('/run/autofocus', methods=['GET', 'POST'])
    @except_wrap
    def autofocus():
        block = bool(request.args.get("block", default=True, type=int))
        plugin.autofocus(block=block)
        return {'status': HTTPStatus.OK}

    @app.route('/get/subsystem_functions', methods=['GET'])
    @except_wrap
    def subsystem_functions():
        return {
            'data': plugin.subsystem_functions_serialized(),
            'status': HTTPStatus.OK,
        }

    @app.route('/run/subsystem/<subsystem>/<function>',
               methods=['GET', 'POST'])
    @except_wrap
    def subsystem_function(subsystem, function):
        kwargs = dict(request.args.items())
        print("debug", subsystem, function, kwargs)
        plugin.subsystem_function_serialized(subsystem_=subsystem,
                                             function_=function,
                                             **kwargs)
        return {'status': HTTPStatus.OK}

    @app.route('/run/wait_imaging_ok', methods=['GET'])
    @except_wrap
    def wait_imaging_ok():
        plugin.wait_imaging_ok()
        return {
            'status': HTTPStatus.OK,
        }

    @app.route('/get/image', methods=['GET'])
    @except_wrap
    def get_image():
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
