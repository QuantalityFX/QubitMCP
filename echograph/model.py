"""
we call it model because it’s the data model of your app—the plain, host-agnostic structures that represent nodes, links, settings, etc. Keeping this in a tiny echograph/model.py means “the data lives here, the UI draws it elsewhere.”
Why avoid importing Qt in the model?
Portability / headless use. You can load, serialize, diff, or unit-test graphs on any machine (or CI) without Qt or a DCC host. Your CLI tools and batch exports don’t need PySide installed just to read a JSON.
Faster startup & fewer crashes. Importing Qt pulls a lot of native libs. Keeping the model Qt-free means most modules load instantly and don’t explode if QtWebEngine isn’t present.
Clean serialization. JSON loves basic types. Storing pos_xy: tuple[float, float] avoids leaking QPointF into persistence and keeps file format stable.
No circular imports. UI imports model; model does not import UI. That prevents the “NodeItem ↔ GraphNode” import tangle.
Type hints without runtime cost. The UI can if TYPE_CHECKING: from echograph.model import GraphNode and still run even if model changes later.
Concrete shape:
"""

# echograph/model.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

@dataclass
class GraphNode:
    name: str
    kind: str = "node"
    info: str = ""
    code: str | None = None
    params: List[Dict[str, Any]] = field(default_factory=list)
    switch_inputs: List[str] = field(default_factory=list)
    switch_index: int = 0
    # Qt-free position (canonical for persistence)
    pos_xy: Tuple[float, float] = (0.0, 0.0)

    # Legacy field some code may still touch (kept for compatibility; not used by persistence)
    pos: Any = None
