from .spec import INSTANCE_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("instance", INSTANCE_SPEC)
