from .spec import ANIM_RETARGET_SPEC


ALIASES = (
    "anim_retarget",
    "anim retarget",
    "animretarget",
    "retarget",
)


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    for kind in ALIASES:
        _core.register_spec(kind, ANIM_RETARGET_SPEC)
