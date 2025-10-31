# nodes/librarian/__init__.py
from __future__ import annotations

# Import the Spec (with augment_infocard_footer returning False)
from .spec import Spec

def register() -> None:
    """
    Register the Librarian spec with nodes.core.
    Prefers core.register_spec(name, spec); falls back to core.register(name, spec).
    """
    import importlib

    core = importlib.import_module("nodes.core")

    # Prefer newer API
    if hasattr(core, "register_spec") and callable(core.register_spec):
        core.register_spec("librarian", Spec)
        return

    # Fallback
    if hasattr(core, "register") and callable(core.register):
        core.register("librarian", Spec)
        return

    raise AttributeError(
        "[librarian] nodes.core has neither register_spec nor register; "
        "export one that accepts (name, spec)."
    )

__all__ = ["register", "Spec"]
