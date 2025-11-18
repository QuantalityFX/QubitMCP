from .spec import PROMPT_NODE_SPEC, PROMPT_NODE_KIND, PROMPT_NODE_ALIASES

def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    for kind in {PROMPT_NODE_KIND, *PROMPT_NODE_ALIASES}:
        _core.register(
            kind,
            stripe_color=PROMPT_NODE_SPEC.stripe_color,
            augment_infocard_footer=PROMPT_NODE_SPEC.augment_infocard_footer,
            build_ports=PROMPT_NODE_SPEC.build_ports,
        )
