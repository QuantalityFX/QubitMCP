from .spec import MASK_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("mask", MASK_SPEC)
    _core.register_spec("paint_mask", MASK_SPEC)
    _core.register_spec("paint mask", MASK_SPEC)
