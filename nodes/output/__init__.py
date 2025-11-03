# nodes/output/__init__.py
from .spec import OUTPUT_SPEC

def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    _core.register(
        "output",
        stripe_color=OUTPUT_SPEC.stripe_color,
        augment_infocard_footer=None,
    )
