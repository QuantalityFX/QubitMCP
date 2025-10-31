from dataclasses import dataclass
from typing import Callable, Optional

@dataclass
class NodeKindSpec:
    # return a CSS-like hex string for the top stripe
    stripe_color: str = "#64748b"

    # hook to add extra rows/widgets inside NodeItem after params
    # signature: (node_item, y_cursor) -> new_y_cursor
    build_widgets: Optional[Callable] = None

    # hook to augment InfoCard footer (e.g., add buttons)
    # signature: (info_card, footer_layout) -> None
    augment_infocard_footer: Optional[Callable] = None

_registry = {}

def register_spec(kind: str, spec: NodeKindSpec):
    _registry[kind.strip().lower()] = spec

def get_spec(kind: str) -> NodeKindSpec:
    return _registry.get(kind.strip().lower(), _registry.get("node"))

def register_defaults():
    # standard 'node' fallback — nothing special
    if "node" not in _registry:
        register_spec("node", NodeKindSpec(
            stripe_color="#64748b",
            build_widgets=None,
            augment_infocard_footer=None
        ))
