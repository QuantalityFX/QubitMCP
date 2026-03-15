# nodes/core.py
# Minimal node-kind registry used by the main app.

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Dict, Optional

__all__ = ["Spec", "register", "get_spec", "register_defaults"]

# Internal global registry of kind -> Spec
_REGISTRY: Dict[str, "Spec"] = {}

@dataclass
class Spec:
    """Describes how a node-kind should look/behave in the UI."""
    stripe_color: str = "#64748b"  # default gray
    # Optional hook: plugins can add buttons into InfoCard footer.
    # Signature: augment_infocard_footer(card: QWidget, footer_layout: QHBoxLayout) -> None
    augment_infocard_footer: Optional[Callable] = None

def register(kind: str, **kwargs) -> None:
    """
    Add or update a node-kind spec.
    Example:
        register("librarian", stripe_color="#74d603", augment_infocard_footer=my_func)
    """
    k = (kind or "node").strip().lower()
    spec = _REGISTRY.get(k) or Spec()
    if "stripe_color" in kwargs and kwargs["stripe_color"]:
        spec.stripe_color = str(kwargs["stripe_color"])
    if "augment_infocard_footer" in kwargs:
        spec.augment_infocard_footer = kwargs["augment_infocard_footer"]
    _REGISTRY[k] = spec

def get_spec(kind: str) -> Spec:
    """Fetch a spec; if missing, return a generic default."""
    k = (kind or "node").strip().lower()
    return _REGISTRY.get(k) or Spec()

def register_defaults() -> None:
    """
    Populate sensible defaults so the app looks familiar even without plugins.
    These match your earlier hardcoded colors.
    """
    register("node",     stripe_color="#64748b")
    register("switch",   stripe_color="#f59e0b")  # orange
    register("python",   stripe_color="#10b981")  # green
    register("import",   stripe_color="#3b82f6")  # blue
    register("output",   stripe_color="#a855f7")  # purple
    register("llm",      stripe_color="#14b8a6")  # teal
    register("local_server", stripe_color="#14b8a6")  # teal (llm alias)
    register("librarian", stripe_color="#74d603") # lime (plugin can override)
