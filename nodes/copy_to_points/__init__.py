from .spec import COPY_TO_POINTS_SPEC


def register(core=None):
    if core is None:
        from nodes import core as core_mod  # type: ignore
    else:
        core_mod = core
    core_mod.register_spec("copy_to_points", COPY_TO_POINTS_SPEC)
    for alias in ("copy to points", "copy_to_point", "copy to point", "copytopoints"):
        core_mod.register_spec(alias, COPY_TO_POINTS_SPEC)
