from .spec import ANIMATION_CORRECTION_SPEC


def register(core=None):
    if core is None:
        from nodes import core
    core.register_spec('animation_correction',ANIMATION_CORRECTION_SPEC)
