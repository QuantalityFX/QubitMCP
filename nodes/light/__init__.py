from .spec import LIGHT_SPEC, LIGHT_TYPES, normalize_light_type


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    for kind in ("light", "scene_light", "directional_light", "point_light", "spot_light", "area_light"):
        _core.register_spec(kind, LIGHT_SPEC)
