# nodes/uv_unwrap/__init__.py
from .spec import UV_UNWRAP_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("uv_unwrap", UV_UNWRAP_SPEC)
