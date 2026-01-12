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
    try:
        d["pos_z"] = float(getattr(node, "pos_z", 0.0))
    except Exception:
        d["pos_z"] = 0.0

    k = (node.kind or "").lower()
    if k in ("switch", "append"):
        d["switch_inputs"] = list(node.switch_inputs or [])
        if k == "switch":
            d["switch_index"] = int(node.switch_index or 0)

    if k == "note":
        feat = getattr(node, "_featured_params", None)
        if isinstance(feat, set):
            d["featured_params"] = sorted(feat)
        size = getattr(node, "_note_size", None)
        if isinstance(size, (list, tuple)) and len(size) >= 2:
            try:
                w = float(size[0])
                h = float(size[1])
            except Exception:
                w = h = None
            if w is not None and h is not None:
                d["note_size"] = [w, h]
        fheights = getattr(node, "_featured_heights", None)
        if isinstance(fheights, dict):
            clean = {}
            for name, val in fheights.items():
                try:
                    hv = float(val)
                except Exception:
                    continue
                if hv > 0:
                    clean[str(name)] = hv
            if clean:
                d["featured_heights"] = clean
    if k in ("chatbot", "chat bot", "chat_bot"):
        size = getattr(node, "_chatbot_size", None)
        if isinstance(size, (list, tuple)) and len(size) >= 2:
            try:
                w = float(size[0])
                h = float(size[1])
            except Exception:
                w = h = None
            if w is not None and h is not None:
                d["chatbot_size"] = [w, h]

    if k in ("image_collection", "imagecollection"):
        st = getattr(node, "_image_collection_state", None) or {}
        if isinstance(st, dict):
            paths = [str(p) for p in st.get("paths", []) if isinstance(p, str) and p.strip()]
            if paths:
                d["image_collection_paths"] = paths

    return d


def serialize_scene(scene) -> Dict[str, Any]:
    nodes = [_node_to_dict(nitem.model) for nitem in scene._node_items.values()]
    edges = []
    for e in scene._edges:
        entry = {"src": e.src.model.name, "dst": e.dst.model.name}
        dst_port = getattr(e, "dst_port_name", None)
        if dst_port:
            entry["dst_port"] = dst_port
        edges.append(entry)
    comments = []
    if hasattr(scene, "comment_groups_data"):
        try:
            comments = scene.comment_groups_data()
        except Exception:
            comments = []
    llm_scale = float(getattr(scene, "_llm_scale", LLM_SCALE_DEFAULT))
    return {
        "nodes": nodes,
        "edges": edges,
        "comments": comments,
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
            nsize = nd.get("note_size")
            if isinstance(nsize, (list, tuple)) and len(nsize) >= 2:
                try:
                    setattr(n, "_note_size", (float(nsize[0]), float(nsize[1])))
                except Exception:
                    pass
            fheights = nd.get("featured_heights")
            if isinstance(fheights, dict):
                clean = {}
                for name, val in fheights.items():
                    try:
                        hv = float(val)
                    except Exception:
                        continue
                    if hv > 0:
                        clean[str(name)] = hv
                if clean:
                    try:
                        setattr(n, "_featured_heights", clean)
                    except Exception:
                        pass
        if (n.kind or "").lower() in ("chatbot", "chat bot", "chat_bot"):
            csize = nd.get("chatbot_size")
            if isinstance(csize, (list, tuple)) and len(csize) >= 2:
                try:
                    setattr(n, "_chatbot_size", (float(csize[0]), float(csize[1])))
                except Exception:
                    pass
        if (n.kind or "").lower() in ("image_collection", "imagecollection"):
            paths = nd.get("image_collection_paths") or []
            try:
                paths = [str(p) for p in paths if isinstance(p, str) and p.strip()]
            except Exception:
                paths = []
            setattr(n, "_image_collection_state", {"paths": paths})

        # position (Qt-free)
        pos = nd.get("pos", [0.0, 0.0])
        try:
            n.pos_xy = (float(pos[0]), float(pos[1]))
        except Exception:
            n.pos_xy = (0.0, 0.0)
        try:
            n.pos_z = float(nd.get("pos_z", 0.0))
        except Exception:
            n.pos_z = 0.0

        # let GraphScene convert to QPointF as needed
        scene.add_node(n, n.pos_xy)

    # 4) rebuild edges
    for ed in data.get("edges", []):
        try:
            scene._add_edge_and_update_switch(
                ed["src"],
                ed["dst"],
                dst_port_name=ed.get("dst_port"),
            )
        except Exception:
            pass

    # 5) rebuild comment groups
    for cdata in data.get("comments", []):
        try:
            scene._add_comment_group_from_data(cdata)
        except Exception:
            pass

    scene._refresh_all_switch_widgets()
    scene._reframe_to_nodes(margin=8000.0)

    if getattr(scene, "_current_output_name", None) in scene._node_items:
        scene.recompute_active_path(scene._current_output_name)
    else:
        scene._clear_path_highlight()
