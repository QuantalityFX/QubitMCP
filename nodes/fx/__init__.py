from .spec import FX_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("fx", FX_SPEC)
    _core.register_spec("fx_trail", FX_SPEC)
