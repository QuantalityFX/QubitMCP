from .spec import CAMERA_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("camera", CAMERA_SPEC)
    _core.register_spec("scene_camera", CAMERA_SPEC)

