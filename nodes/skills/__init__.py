from .spec import SKILLS_SPEC


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register_spec("skills", SKILLS_SPEC)
    _core.register_spec("skills_library", SKILLS_SPEC)
