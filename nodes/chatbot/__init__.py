from .spec import CHATBOT_SPEC, CHATBOT_NODE_KIND, CHATBOT_NODE_ALIASES


def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    for kind in {CHATBOT_NODE_KIND, *CHATBOT_NODE_ALIASES}:
        _core.register(
            kind,
            stripe_color=CHATBOT_SPEC.stripe_color,
            render_node_body=CHATBOT_SPEC.render_node_body,
            build_ports=CHATBOT_SPEC.build_ports,
        )
