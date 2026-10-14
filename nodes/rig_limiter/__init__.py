from .spec import RIG_LIMITER_SPEC


def register(core=None):
    if core is None:
        from nodes import core
    core.register_spec('rig_limiter', RIG_LIMITER_SPEC)
