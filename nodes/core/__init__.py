# nodes/core/__init__.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Any

__all__ = [
    "Spec",
    "register",
    "get_spec",
    "register_defaults",
    # legacy shims (so old plugins still work):
    "NodeKindSpec",
    "register_spec",
]

# ---- Core types ----

@dataclass
class Spec:
    """Describes a node-kind’s appearance/behavior in the UI."""
    stripe_color: str = "#64748b"
    # Optional InfoCard hook:
    # def augment_infocard_footer(card, footer_layout) -> bool|None
    augment_infocard_footer: Optional[Callable[..., Any]] = None

# Global registry
_REGISTRY: Dict[str, Spec] = {}

# ---- New API ----

def register(kind: str, **kwargs) -> None:
    """
    Example:
        register("librarian",
                 stripe_color="#e11d48",
                 augment_infocard_footer=my_hook)
    """
    k = (kind or "node").strip().lower()
    spec = _REGISTRY.get(k) or Spec()
    if "stripe_color" in kwargs and kwargs["stripe_color"]:
        spec.stripe_color = str(kwargs["stripe_color"])
    if "augment_infocard_footer" in kwargs:
        spec.augment_infocard_footer = kwargs["augment_infocard_footer"]
    _REGISTRY[k] = spec

def get_spec(kind: str) -> Spec:
    k = (kind or "node").strip().lower()
    return _REGISTRY.get(k) or Spec()

def register_defaults() -> None:
    register("node",      stripe_color="#64748b")
    register("switch",    stripe_color="#f59e0b")
    register("python",    stripe_color="#10b981")
    register("import",    stripe_color="#3b82f6")
    register("output",    stripe_color="#a855f7")
    register("llm",       stripe_color="#14b8a6")
    # librarians can override this later in their plugin
    register("librarian", stripe_color="#74d603")

# ---- Legacy shims (for old plugins) ----

NodeKindSpec = Spec  # old name

def register_spec(kind: str, spec_or_kwargs: Any) -> None:
    """
    Back-compat: accept Spec or dict (old API).
    """
    if isinstance(spec_or_kwargs, Spec):
        register(kind,
                 stripe_color=spec_or_kwargs.stripe_color,
                 augment_infocard_footer=spec_or_kwargs.augment_infocard_footer)
    elif isinstance(spec_or_kwargs, dict):
        register(kind, **spec_or_kwargs)
    else:
        register(kind)
