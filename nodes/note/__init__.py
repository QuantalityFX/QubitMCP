# nodes/note/__init__.py
from .spec import NOTE_SPEC

def register(core=None):
    """
    Register Note node spec with the central registry.
    The `core` arg is optional; we lazy-import if not provided.
    """
    if core is None:
        from nodes import core as _core
    else:
        _core = core

    _core.register_spec("note", NOTE_SPEC)
