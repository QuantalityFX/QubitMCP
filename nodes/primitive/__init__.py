# nodes/primitive/__init__.py
from .spec import PRIMITIVE_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("primitive", PRIMITIVE_SPEC)
