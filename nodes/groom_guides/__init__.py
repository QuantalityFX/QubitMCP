from .spec import GROOM_GUIDES_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("groom_guides", GROOM_GUIDES_SPEC)
    _core.register_spec("groom guides", GROOM_GUIDES_SPEC)
    _core.register_spec("hair_guides", GROOM_GUIDES_SPEC)
    _core.register_spec("hair guides", GROOM_GUIDES_SPEC)
