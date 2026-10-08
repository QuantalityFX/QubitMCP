from .spec import PATTERN_DESIGN_SPEC


def register(core=None):
    if core is None:
        from nodes import core
    core.register_spec("pattern_design", PATTERN_DESIGN_SPEC)
