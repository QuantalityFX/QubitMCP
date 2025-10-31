from dataclasses import dataclass
from typing import Callable, Optional, Dict

@dataclass
class NodeKindSpec:
    stripe_color: str = "#64748b"
    # Optional hooks:
    # NodeItem hook: def build_widgets(node_item, y_cursor:int) -> Optional[int]
    build_widgets: Optional[Callable] = None
    # InfoCard hook: def augment_infocard_footer(card, footer_layout) -> None
    augment_infocard_footer: Optional[Callable] = None

_REG: Dict[str, NodeKindSpec] = {}

def register_spec(kind: str, spec: NodeKindSpec):
    _REG[(kind or "").lower()] = spec

def get_spec(kind: str) -> NodeKindSpec:
    return _REG.get((kind or "").lower(), _REG["node"])

def register_defaults():
    if "node" not in _REG:
        register_spec("node", NodeKindSpec(stripe_color="#64748b"))
