from .spec import SCENE_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("scene", SCENE_SPEC)
    _core.register_spec("scene_assembly", SCENE_SPEC)
    _core.register_spec("scene_outliner", SCENE_SPEC)
