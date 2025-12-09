# nodes/database/__init__.py
from .spec import DATABASE_SPEC  # noqa: F401

# legacy shim
from nodes.core import register


def register(core=None):
    from nodes.core import register as _reg
    _reg("database", stripe_color=DATABASE_SPEC.stripe_color,
         augment_infocard_footer=DATABASE_SPEC.augment_infocard_footer,
         build_ports=DATABASE_SPEC.build_ports)
