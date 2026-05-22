from .spec import LIGHT_SPEC, LIGHT_TYPES, normalize_light_type


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("light", LIGHT_SPEC)
    _core.register_spec("scene_light", LIGHT_SPEC)
    _core.register_spec("directional_light", LIGHT_SPEC)
