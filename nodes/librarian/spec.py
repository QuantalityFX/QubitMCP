# nodes/librarian/spec.py
from nodes.core import NodeKindSpec
try:
    from PySide6 import QtWidgets, QtGui, QtCore
except Exception:
    from PySide2 import QtWidgets, QtGui, QtCore  # type: ignore

def _text_from_input(card, node_item, port_name: str) -> str:
    """
    Try to pull a text value from a specific named input port.
    - If the upstream is an Append, it will merge in UI order.
    - If multiple wires target the same named port, merge in UI order.
    - If nothing wired, return "".
    """
    sc = getattr(card, "_graph_scene", None)
    if not sc or not node_item:
        return ""

    # Collect only edges landing on this node's *named* input
    try:
        all_in = [e for e in sc._in_edges(node_item)]
    except Exception:
        all_in = []

    # Filter to the requested input port name, if your edges expose it
    def _edge_matches_name(e):
        # Support a few possible attributes your graph might use
        for attr in ("dst_port_name", "dst_label", "dst_name"):
            if hasattr(e, attr) and getattr(e, attr) == port_name:
                return True
        # Fallback: if you don't label input ports yet, allow a single unnamed match
        return (port_name == "query") and True

    in_named = [e for e in all_in if _edge_matches_name(e)]
    if not in_named:
        return ""

    # Respect Append-aware ordering if available
    try:
        ordered = [e for e in sc._ordered_in_edges(node_item) if e in in_named]
        if ordered:
            in_named = ordered
    except Exception:
        pass

    parts = []
    for e in in_named:
        try:
            t = sc.resolve_text_value(e.src)  # Note & Append already supported
        except Exception:
            t = ""
        if t:
            parts.append(t.strip())

    return "\n\n".join(parts).strip()

def _build_ports(node_item):
    """
    Ensure Librarian has named input sockets:
      query, docs_dir, mode, top_k, action
    """
    names = ["query", "docs_dir", "mode", "top_k", "action"]
    # Try a few APIs depending on your NodeItem implementation
    for n in names:
        if hasattr(node_item, "ensure_input"):
            node_item.ensure_input(n)                # preferred
        elif hasattr(node_item, "add_input_port"):
            node_item.add_input_port(n)              # alt
        elif hasattr(node_item, "add_input"):
            node_item.add_input(n)                   # legacy

def _send_ipc(card, payload: dict) -> None:
    # Late import to avoid heavy deps when graph is idle
    try:
        from echograph.services import librarian_ipc as ipc
    except Exception:
        # legacy import paths (if your repo layout differs)
        try:
            import echograph.services.librarian_ipc as ipc
        except Exception:
            QtWidgets.QMessageBox.critical(card, "Librarian", "Cannot import librarian_ipc.")
            return

    # If docs_dir provided, set ENV so the running UI/core respects it
    docs_dir = (payload.get("docs_dir") or "").strip()
    if docs_dir:
        import os
        os.environ["LIBRARIAN_DOCS_DIR"] = docs_dir

    # Make sure the Librarian UI is alive (non-blocking)
    ipc.ensure_running(focus_hint=False)

    # Normalize + enqueue
    action = (payload.get("type") or payload.get("action") or "").strip().lower()
    if not action:
        action = "search"
    payload["type"] = payload["action"] = action
    ipc.enqueue(payload)

def augment_infocard_footer(card, footer_layout) -> bool:
    """
    Librarian InfoCard footer:
      Inputs (optional, named sockets):
        - query      : text (Note/Append or any text node)
        - docs_dir   : filesystem path (text)
        - mode       : "tree_summarize" | "compact"
        - top_k      : integer-like text (e.g. "8")
        - action     : "search" | "summarize" | "analyze" | "analyze_with_sources"
    Buttons:
        - Search
        - Analyze
        - Analyze (with Sources)
    """
    sc   = getattr(card, "_graph_scene", None)
    node = getattr(card, "_node_ref", None)
    if not sc or not node:
        return False

    # Get the live NodeItem for this Librarian node
    try:
        node_item = sc._node_items.get(node.name)
    except Exception:
        node_item = None
    if node_item is None:
        return False

    # Small helpers to read inputs
    def _param_value(name: str) -> str:
        target = (name or "").strip().lower()
        for p in (getattr(node, "params", None) or []):
            pname = (p.get("name") or "").strip().lower()
            if pname == target:
                return p.get("value") or ""
        return ""

    def _val(name: str) -> str:
        wired = _text_from_input(card, node_item, name)
        if wired and wired.strip():
            return wired
        return _param_value(name)

    def _kval(name: str, default: int) -> int:
        s = _val(name)
        try:
            return int(s.strip())
        except Exception:
            return int(default)

    def _normalize_action(raw: str, fallback: str) -> str:
        aliases = {
            "analysis": "analyze",
            "analyse": "analyze",
            "analyse_with_sources": "analyze_with_sources",
            "analyze with sources": "analyze_with_sources",
            "analyse with sources": "analyze_with_sources",
            "search with sources": "analyze_with_sources",
        }
        val = (raw or "").strip()
        if not val:
            return fallback
        key = val.lower().replace(" ", "_")
        key = aliases.get(key, key)
        allowed = {"search", "analyze", "analyze_with_sources", "summarize", "summarize_with_sources"}
        if key in allowed:
            return key
        return fallback

    # --- Buttons ---
    def _do(action: str):
        wired_action = _val("action")
        effective_action = _normalize_action(wired_action, action or "search")

        # Collect values with port-first precedence
        query    = _val("query")
        docs_dir = _val("docs_dir")
        mode     = (_val("mode") or "tree_summarize").strip()
        top_k    = _kval("top_k", 5)

        payload = {
            "type": effective_action,
            "query": query,
            "mode": mode,
            "top_k": top_k,
        }
        if docs_dir:
            payload["docs_dir"] = docs_dir

        # For analyze flavors, allow empty query (will use last summary in UI)
        if effective_action in ("search",) and not query:
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "No query wired into 'query' input.", card)
            return

        _send_ipc(card, payload)
        label = effective_action if effective_action == action else f"{action} -> {effective_action}"
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), f"Sent: {label}", card)

    btn_s = QtWidgets.QPushButton("Search")
    btn_s.clicked.connect(lambda: _do("search"))
    footer_layout.addWidget(btn_s)

    btn_a = QtWidgets.QPushButton("Analyze")
    btn_a.clicked.connect(lambda: _do("analyze"))
    footer_layout.addWidget(btn_a)

    btn_as = QtWidgets.QPushButton("Analyze (with Sources)")
    btn_as.clicked.connect(lambda: _do("analyze_with_sources"))
    footer_layout.addWidget(btn_as)

    return True

LIBRARIAN_SPEC = NodeKindSpec(
    stripe_color="#e11d48",
    augment_infocard_footer=augment_infocard_footer,
    build_ports=_build_ports,  # <-- add this
)
