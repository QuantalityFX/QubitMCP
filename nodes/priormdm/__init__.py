from .spec import PRIORMDM_SPEC


def register(core=None):
    if core is None:
        from nodes import core
    core.register_spec("priormdm", PRIORMDM_SPEC)
