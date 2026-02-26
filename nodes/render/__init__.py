from .spec import RENDER_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    _core.register_spec("render", RENDER_SPEC)
    _core.register_spec("render_sequence", RENDER_SPEC)
    _core.register_spec("render node", RENDER_SPEC)

