# nodes/core/__init__.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Any
import sys

__all__ = [
    "Spec",
    "register",
    "get_spec",
    "register_defaults",
    "apply_spec_to_item",   # helper
    # legacy shims:
    "NodeKindSpec",
    "register_spec",
]

# ---- Core types ----

@dataclass
class Spec:
    """Describes a node-kind’s appearance/behavior in the UI."""
    stripe_color: str = "#64748b"

    # Optional: InfoCard footer hook
    # def augment_infocard_footer(card, footer_layout) -> bool|None
    augment_infocard_footer: Optional[Callable[..., Any]] = None

    # Optional: Node body hook (called from NodeItem._build_widgets)
    # def render_node_body(node_item, y_cursor:int) -> int|None
    render_node_body: Optional[Callable[..., Any]] = None

    # Optional: Port builder hook (called after NodeItem is created)
    # def build_ports(node_item) -> None
    build_ports: Optional[Callable[..., Any]] = None


# Global registry
_REGISTRY: Dict[str, Spec] = {}


# ---- New API ----

def register(kind: str, **kwargs) -> None:
    """
    Example:
        register("librarian",
                 stripe_color="#e11d48",
                 augment_infocard_footer=my_footer_hook,
                 render_node_body=my_body_hook,
                 build_ports=my_build_ports)
    """
    k = (kind or "node").strip().lower()
    spec = _REGISTRY.get(k) or Spec()

    if "stripe_color" in kwargs and kwargs["stripe_color"]:
        spec.stripe_color = str(kwargs["stripe_color"])

    if "augment_infocard_footer" in kwargs:
        spec.augment_infocard_footer = kwargs["augment_infocard_footer"]

    if "render_node_body" in kwargs:
        spec.render_node_body = kwargs["render_node_body"]

    if "build_ports" in kwargs:
        spec.build_ports = kwargs["build_ports"]

    _REGISTRY[k] = spec


def get_spec(kind: str) -> Spec:
    k = (kind or "node").strip().lower()
    return _REGISTRY.get(k) or Spec()


def register_defaults() -> None:
    register("node",      stripe_color="#64748b")
    register("switch",    stripe_color="#f59e0b")
    register("python",    stripe_color="#10b981")
    register("import",    stripe_color="#3b82f6")
    register("html_preview", stripe_color="#f97316")
    register("output",    stripe_color="#a855f7")
    register("llm",       stripe_color="#14b8a6")
    # librarian plugins can override this later
    register("librarian", stripe_color="#74d603")

    # Auto-register optional GPT prompt node so it's available even if loader plugins fail later
    try:
        from nodes import gpt_prompt as _gpt_prompt  # type: ignore
        if hasattr(_gpt_prompt, "register"):
            _gpt_prompt.register(core=sys.modules[__name__])
    except Exception as exc:  # pragma: no cover - optional plugin
        print("[EchoGraph] GPT Prompt auto-register failed:", exc)


def apply_spec_to_item(item: Any, kind: str | Spec, *, debug: bool = False) -> Any:
    """
    Apply the registered Spec to a newly created NodeItem.

    Call this right after you instantiate your NodeItem:
        item = NodeItem(model)
        apply_spec_to_item(item, model.kind)

    Returns the same item for chaining.
    """
    spec = kind if isinstance(kind, Spec) else get_spec(kind)

    # 1) Let spec build named ports (if provided)
    try:
        if callable(spec.build_ports):
            spec.build_ports(item)
            if debug:
                names = getattr(item, "input_port_names", lambda: [])()
                print(f"[EchoGraph] build_ports for '{getattr(item.model,'kind','node')}': {names}")
    except Exception as e:
        if debug:
            print("[EchoGraph] build_ports error:", e)

    # 2) Apply stripe color if NodeItem supports it (optional)
    try:
        if hasattr(item, "set_stripe_color") and spec.stripe_color:
            item.set_stripe_color(spec.stripe_color)
    except Exception as e:
        if debug:
            print("[EchoGraph] set_stripe_color error:", e)

    return item


# ---- Legacy shims (for old plugins) ----

NodeKindSpec = Spec  # old alias

def register_spec(kind: str, spec_or_kwargs: Any) -> None:
    """
    Back-compat: accept Spec or dict (old API).
    """
    if isinstance(spec_or_kwargs, Spec):
        register(
            kind,
            stripe_color=spec_or_kwargs.stripe_color,
            augment_infocard_footer=spec_or_kwargs.augment_infocard_footer,
            render_node_body=spec_or_kwargs.render_node_body,
            build_ports=spec_or_kwargs.build_ports,
        )
    elif isinstance(spec_or_kwargs, dict):
        register(kind, **spec_or_kwargs)
    else:
        register(kind)
