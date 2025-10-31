from ..core.registry import NodeKindSpec, register_spec

def register():
    # “Standard” node: same stripe color you used for default
    register_spec("node", NodeKindSpec(
        stripe_color="#64748b",
        build_widgets=None,          # no extra widgets
        augment_infocard_footer=None # no extra footer buttons
    ))
