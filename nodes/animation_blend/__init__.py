from .spec import ANIMATION_BLEND_SPEC, KIND_ALIASES


def register(core=None):
    if core is None:
        from nodes import core
    for kind in KIND_ALIASES:
        core.register_spec(kind, ANIMATION_BLEND_SPEC)
