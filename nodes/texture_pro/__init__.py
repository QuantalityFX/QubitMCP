# nodes/texture_pro/__init__.py
from .spec import TEXTURE_PRO_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("texture_pro", TEXTURE_PRO_SPEC)
