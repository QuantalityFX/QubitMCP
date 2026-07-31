from .spec import FBX_ANIMATION_IMPORT_SPEC, FBX_ANIMATION_KIND_ALIASES


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    for kind in FBX_ANIMATION_KIND_ALIASES:
        _core.register_spec(kind, FBX_ANIMATION_IMPORT_SPEC)
