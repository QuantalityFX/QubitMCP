from .spec import MODELER_SPEC


def register(core=None):
    if core is None:
        from nodes import core as core_mod  # type: ignore
    else:
        core_mod = core
    core_mod.register_spec("modeler", MODELER_SPEC)
