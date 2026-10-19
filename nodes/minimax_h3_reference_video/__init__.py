"""MiniMax H3 reference-to-video node (separate from the legacy frame node)."""


def register(core=None):
    from .spec import KIND, ALIASES, SPEC
    if core is None:
        from nodes import core
    for kind in (KIND, *ALIASES):
        core.register_spec(kind, SPEC)
