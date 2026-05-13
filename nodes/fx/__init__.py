from .spec import FX_SPEC
from .splat_physics_spec import KIND_ALIASES as SPLAT_PHYSICS_KIND_ALIASES
from .splat_physics_spec import SPLAT_PHYSICS_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("fx", FX_SPEC)
    _core.register_spec("fx_trail", FX_SPEC)
    for kind in SPLAT_PHYSICS_KIND_ALIASES:
        _core.register_spec(kind, SPLAT_PHYSICS_SPEC)
