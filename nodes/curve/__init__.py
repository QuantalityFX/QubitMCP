from .spec import CURVE_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("curve", CURVE_SPEC)
    _core.register_spec("curve_primitive", CURVE_SPEC)
    _core.register_spec("primitive_curve", CURVE_SPEC)
