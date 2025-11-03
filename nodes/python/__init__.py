# nodes/python/__init__.py
from .spec import PYTHON_SPEC

def register(core=None):
    if core is None:
        from nodes import core as _core
    else:
        _core = core
    _core.register(
        "python",
        stripe_color=PYTHON_SPEC.stripe_color,
        augment_infocard_footer=PYTHON_SPEC.augment_infocard_footer,
    )
