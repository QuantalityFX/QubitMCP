from .spec import LOOK_AT_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    for kind in ("look_at", "lookat", "transform_constraint"):
        _core.register_spec(kind, LOOK_AT_SPEC)
