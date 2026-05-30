from .spec import FX_SPEC
from .music_effects_spec import KIND_ALIASES as MUSIC_EFFECTS_KIND_ALIASES
from .music_effects_spec import MUSIC_EFFECTS_SPEC
from .splat_fx_spec import KIND_ALIASES as SPLAT_FX_KIND_ALIASES
from .splat_fx_spec import SPLAT_FX_SPEC
from .splat_colorize_spec import KIND_ALIASES as SPLAT_COLORIZE_KIND_ALIASES
from .splat_colorize_spec import SPLAT_COLORIZE_SPEC
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
    for kind in SPLAT_FX_KIND_ALIASES:
        _core.register_spec(kind, SPLAT_FX_SPEC)
    for kind in SPLAT_COLORIZE_KIND_ALIASES:
        _core.register_spec(kind, SPLAT_COLORIZE_SPEC)
    for kind in MUSIC_EFFECTS_KIND_ALIASES:
        _core.register_spec(kind, MUSIC_EFFECTS_SPEC)
