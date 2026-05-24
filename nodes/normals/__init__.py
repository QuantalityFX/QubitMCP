from .spec import NORMALS_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("normals", NORMALS_SPEC)
    _core.register_spec("normal", NORMALS_SPEC)
    _core.register_spec("smooth_normals", NORMALS_SPEC)
    _core.register_spec("smooth normals", NORMALS_SPEC)
