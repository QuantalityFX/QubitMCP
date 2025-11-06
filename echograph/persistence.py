from __future__ import annotations
from typing import Callable, Dict, Any
from echograph.constants import LLM_SCALE_DEFAULT

def _node_to_dict(node) -> Dict[str, Any]:
    # Prefer Qt-free position
    try:
        x, y = node.pos_xy
        pos_out = [float(x), float(y)]
    except Exception:
        # legacy fallback if some code still writes QPointF on model.pos
        try:
            pos_out = [float(node.pos.x()), float(node.pos.y())]
        except Exception:
            pos_out = [0.0, 0.0]

    d = {
        "name": node.name,
        "kind": node.kind,
        "info": node.info or "",
        "code": node.code if node.code is not None else None,
        "pos": pos_out,  # <- always plain numbers now
        "params": [{"name": p.get("name",""), "value": p.get("value","")} for p in (node.params or [])],
    }

    k = (node.kind or "").lower()
    if k in ("switch", "append"):
        d["switch_inputs"] = list(node.switch_inputs or [])
        if k == "switch":
            d["switch_index"] = int(node.switch_index or 0)

    if k == "note":
        feat = getattr(node, "_featured_params", None)
        if isinstance(feat, set):
            d["featured_params"] = sorted(feat)

    return d


def serialize_scene(scene) -> Dict[str, Any]:
    nodes = [_node_to_dict(nitem.model) for nitem in scene._node_items.values()]
    edges = [{"src": e.src.model.name, "dst": e.dst.model.name} for e in scene._edges]
    llm_scale = float(getattr(scene, "_llm_scale", LLM_SCALE_DEFAULT))
    return {
        "nodes": nodes,
        "edges": edges,
        "llm_scale": llm_scale,                 # legacy top-level
        "settings": {"llm_scale": llm_scale},   # preferred
    }


def deserialize_scene(
    scene,
    data: Dict[str, Any],
    *,
    GraphNode_ctor: Callable[..., Any],
    set_scale_cb: Callable[[float], None],
) -> None:
    # 1) apply saved scale first
    raw = (data.get("settings", {}) or {}).get("llm_scale", data.get("llm_scale", LLM_SCALE_DEFAULT))
    try:
        set_scale_cb(float(raw))
    except Exception:
        set_scale_cb(LLM_SCALE_DEFAULT)

    # 2) clear
    scene.clear_scene()

    # 3) rebuild nodes
    for nd in data.get("nodes", []):
        n = GraphNode_ctor(
            nd.get("name",""),
            kind=nd.get("kind","node"),
            info=nd.get("info",""),
            code=nd.get("code"),
            params=nd.get("params", []),
            switch_inputs=nd.get("switch_inputs", []),
            switch_index=nd.get("switch_index", 0),
        )

        # featured params (Note)
        if (n.kind or "").lower() == "note":
            feat = nd.get("featured_params") or []
            try:
                setattr(n, "_featured_params", {str(x) for x in feat if x})
            except Exception:
                setattr(n, "_featured_params", set())

        # position (Qt-free)
        pos = nd.get("pos", [0.0, 0.0])
        try:
            n.pos_xy = (float(pos[0]), float(pos[1]))
        except Exception:
            n.pos_xy = (0.0, 0.0)

        # let GraphScene convert to QPointF as needed
        scene.add_node(n, n.pos_xy)

    # 4) rebuild edges
    for ed in data.get("edges", []):
        try:
            scene._add_edge_and_update_switch(ed["src"], ed["dst"])
        except Exception:
            pass

    scene._refresh_all_switch_widgets()
    scene._reframe_to_nodes(margin=8000.0)

    if getattr(scene, "_current_output_name", None) in scene._node_items:
        scene.recompute_active_path(scene._current_output_name)
    else:
        scene._clear_path_highlight()
