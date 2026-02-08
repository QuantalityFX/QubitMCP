from __future__ import annotations

from typing import Optional


def param_change_relevant(node_item, changed_name: Optional[str]) -> bool:
    """
    Return True if a param change for `changed_name` should trigger updates on `node_item`.

    Conservative fallback: if we cannot inspect the scene graph, return True.
    """
    if not node_item:
        return True
    if not changed_name:
        return True
    try:
        model = getattr(node_item, "model", None)
        if model is not None and str(getattr(model, "name", "")) == str(changed_name):
            return True
    except Exception:
        return True

    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None:
        return True

    ordered = getattr(scene, "_ordered_in_edges", None)
    in_edges = getattr(scene, "_in_edges", None)
    if not callable(ordered) and not callable(in_edges):
        return True

    seen = set()
    stack = [node_item]
    while stack:
        it = stack.pop()
        it_id = id(it)
        if it_id in seen:
            continue
        seen.add(it_id)

        try:
            if callable(ordered):
                edges = list(ordered(it))
            elif callable(in_edges):
                edges = list(in_edges(it))
            else:
                edges = []
        except Exception:
            edges = []

        for e in edges:
            src = getattr(e, "src", None)
            if src is None:
                continue
            try:
                src_model = getattr(src, "model", None)
                if src_model is not None and str(getattr(src_model, "name", "")) == str(changed_name):
                    return True
            except Exception:
                pass
            if id(src) not in seen:
                stack.append(src)
    return False
