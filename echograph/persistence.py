from __future__ import annotations
import time
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
        completed = getattr(node, "_completed_params", None)
        if isinstance(completed, (set, list, tuple)):
            names = sorted({str(x) for x in completed if x})
            if names:
                d["completed_params"] = names
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
    if k in ("video_player", "video player", "videoplayer"):
        size = getattr(node, "_video_player_size", None)
        if isinstance(size, (list, tuple)) and len(size) >= 2:
            try:
                w = float(size[0])
                h = float(size[1])
            except Exception:
                w = h = None
            if w is not None and h is not None:
                d["video_player_size"] = [w, h]

    if k in ("image_collection", "imagecollection"):
        st = getattr(node, "_image_collection_state", None) or {}
        if isinstance(st, dict):
            paths = [str(p) for p in st.get("paths", []) if isinstance(p, str) and p.strip()]
            if paths:
                d["image_collection_paths"] = paths
    if k in ("scene", "scene_assembly", "scene_outliner"):
        hidden = getattr(node, "_scene_hidden", None)
        if isinstance(hidden, set):
            names = {str(x) for x in hidden if x}
            if names:
                d["scene_hidden"] = sorted(names)
        elif isinstance(hidden, (list, tuple)):
            names = {str(x) for x in hidden if x}
            if names:
                d["scene_hidden"] = sorted(names)
        xforms = getattr(node, "_scene_xforms", None)
        if isinstance(xforms, dict) and xforms:
            clean_xf = {}
            for name, xf in xforms.items():
                if not name or not isinstance(xf, dict):
                    continue
                try:
                    pos = [float(v) for v in xf.get("pos", (0.0, 0.0, 0.0))]
                    rot = [float(v) for v in xf.get("rot", (0.0, 0.0, 0.0))]
                    scl = [float(v) for v in xf.get("scl", (1.0, 1.0, 1.0))]
                except Exception:
                    continue
                clean_xf[str(name)] = {"pos": pos, "rot": rot, "scl": scl}
            if clean_xf:
                d["scene_xforms"] = clean_xf

    return d


def serialize_scene(scene) -> Dict[str, Any]:
    nodes = [_node_to_dict(nitem.model) for nitem in scene._node_items.values()]
    edges = []
    for e in scene._edges:
        entry = {"src": e.src.model.name, "dst": e.dst.model.name}
        dst_port = getattr(e, "dst_port_name", None)
        if dst_port:
            entry["dst_port"] = dst_port
        try:
            pins = []
            if hasattr(e, "pin_positions"):
                pins = e.pin_positions()
            if pins:
                entry["pins"] = pins
        except Exception:
            pass
        edges.append(entry)
    comments = []
    if hasattr(scene, "comment_groups_data"):
        try:
            comments = scene.comment_groups_data()
        except Exception:
            comments = []
    llm_scale = float(getattr(scene, "_llm_scale", LLM_SCALE_DEFAULT))
    settings = {"llm_scale": llm_scale}
    try:
        view_settings = getattr(scene, "_view_settings", None)
        if isinstance(view_settings, dict):
            for key in ("pan_base", "pan_exp", "pan_boost", "gizmo_zoom_scale", "light_intensity", "fly_speed_mult"):
                if key in view_settings:
                    try:
                        settings[key] = float(view_settings[key])
                    except Exception:
                        pass
            if "wire_color" in view_settings:
                try:
                    raw = view_settings.get("wire_color")
                    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
                        vals = [float(v) for v in raw[:4]]
                        if len(vals) < 4:
                            vals.append(1.0)
                        settings["wire_color"] = [min(1.0, max(0.0, v)) for v in vals[:4]]
                except Exception:
                    pass
    except Exception:
        pass
    return {
        "nodes": nodes,
        "edges": edges,
        "comments": comments,
        "llm_scale": llm_scale,                 # legacy top-level
        "settings": settings,                   # preferred
    }


def deserialize_scene(
    scene,
    data: Dict[str, Any],
    *,
    GraphNode_ctor: Callable[..., Any],
    set_scale_cb: Callable[[float], None],
) -> Dict[str, Any]:
    t_start = time.perf_counter()
    phase_ms: Dict[str, float] = {}
    node_timings = []
    edge_timings = []
    kind_totals: Dict[str, Dict[str, float]] = {}
    edge_errors = 0
    comment_errors = 0

    def _mark(name: str, t0: float) -> None:
        phase_ms[name] = (time.perf_counter() - t0) * 1000.0

    def _node_top(entries, limit: int = 20):
        return sorted(entries, key=lambda x: float(x.get("ms", 0.0)), reverse=True)[:limit]

    def _edge_top(entries, limit: int = 15):
        return sorted(entries, key=lambda x: float(x.get("ms", 0.0)), reverse=True)[:limit]

    nodes_in = list(data.get("nodes", []) or [])
    edges_in = list(data.get("edges", []) or [])
    comments_in = list(data.get("comments", []) or [])
    prev_bulk_loading = bool(getattr(scene, "_bulk_loading", False))
    scene._bulk_loading = True

    try:
        # 1) apply saved scale first
        t_phase = time.perf_counter()
        raw = (data.get("settings", {}) or {}).get("llm_scale", data.get("llm_scale", LLM_SCALE_DEFAULT))
        try:
            set_scale_cb(float(raw))
        except Exception:
            set_scale_cb(LLM_SCALE_DEFAULT)
        _mark("apply_scale", t_phase)

        t_phase = time.perf_counter()
        try:
            settings = data.get("settings", {}) or {}
            if isinstance(settings, dict):
                view_settings = {}
                for key in ("pan_base", "pan_exp", "pan_boost", "gizmo_zoom_scale", "light_intensity", "fly_speed_mult"):
                    if key in settings:
                        try:
                            view_settings[key] = float(settings[key])
                        except Exception:
                            pass
                raw_wire_color = settings.get("wire_color", None)
                if isinstance(raw_wire_color, (list, tuple)) and len(raw_wire_color) >= 3:
                    try:
                        vals = [float(v) for v in raw_wire_color[:4]]
                        if len(vals) < 4:
                            vals.append(1.0)
                        view_settings["wire_color"] = [min(1.0, max(0.0, v)) for v in vals[:4]]
                    except Exception:
                        pass
                if view_settings:
                    scene._view_settings = view_settings
        except Exception:
            pass
        _mark("apply_view_settings", t_phase)

        # 2) clear
        t_phase = time.perf_counter()
        scene.clear_scene()
        _mark("clear_scene", t_phase)

        # 3) rebuild nodes
        t_phase = time.perf_counter()
        for nd in nodes_in:
            t_node = time.perf_counter()
            node_name = str(nd.get("name", "") or "")
            node_kind = str(nd.get("kind", "node") or "node")
            n = GraphNode_ctor(
                node_name,
                kind=node_kind,
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
                completed = nd.get("completed_params") or []
                try:
                    setattr(n, "_completed_params", {str(x) for x in completed if x})
                except Exception:
                    setattr(n, "_completed_params", set())
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
            if (n.kind or "").lower() in ("video_player", "video player", "videoplayer"):
                vsize = nd.get("video_player_size")
                if isinstance(vsize, (list, tuple)) and len(vsize) >= 2:
                    try:
                        setattr(n, "_video_player_size", (float(vsize[0]), float(vsize[1])))
                    except Exception:
                        pass
            if (n.kind or "").lower() in ("image_collection", "imagecollection"):
                paths = nd.get("image_collection_paths") or []
                try:
                    paths = [str(p) for p in paths if isinstance(p, str) and p.strip()]
                except Exception:
                    paths = []
                setattr(n, "_image_collection_state", {"paths": paths})
            if (n.kind or "").lower() in ("scene", "scene_assembly", "scene_outliner"):
                raw_hidden = nd.get("scene_hidden") or []
                try:
                    hidden = {str(x) for x in raw_hidden if x}
                except Exception:
                    hidden = set()
                setattr(n, "_scene_hidden", hidden)
                raw_xforms = nd.get("scene_xforms") or {}
                if isinstance(raw_xforms, dict):
                    try:
                        setattr(n, "_scene_xforms", raw_xforms)
                    except Exception:
                        pass

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
            dt_ms = (time.perf_counter() - t_node) * 1000.0
            node_timings.append({
                "name": node_name,
                "kind": node_kind,
                "ms": round(dt_ms, 3),
            })
            bucket = kind_totals.setdefault(node_kind.lower(), {"count": 0.0, "ms": 0.0})
            bucket["count"] += 1.0
            bucket["ms"] += dt_ms
        _mark("rebuild_nodes", t_phase)

        # 4) rebuild edges
        t_phase = time.perf_counter()
        for ed in edges_in:
            t_edge = time.perf_counter()
            src = str(ed.get("src", "") or "")
            dst = str(ed.get("dst", "") or "")
            ok = False
            try:
                edge = scene._add_edge_and_update_switch(
                    ed["src"],
                    ed["dst"],
                    dst_port_name=ed.get("dst_port"),
                )
                ok = edge is not None
                if edge is not None:
                    try:
                        pins = ed.get("pins") or []
                        if pins and hasattr(edge, "add_pins_from_positions"):
                            edge.add_pins_from_positions(pins)
                    except Exception:
                        pass
            except Exception:
                edge_errors += 1
            dt_ms = (time.perf_counter() - t_edge) * 1000.0
            edge_timings.append({
                "src": src,
                "dst": dst,
                "ms": round(dt_ms, 3),
                "ok": bool(ok),
            })
        _mark("rebuild_edges", t_phase)

        # 5) rebuild comment groups
        t_phase = time.perf_counter()
        for cdata in comments_in:
            try:
                scene._add_comment_group_from_data(cdata)
            except Exception:
                comment_errors += 1
        _mark("rebuild_comments", t_phase)

        t_phase = time.perf_counter()
        scene._refresh_all_switch_widgets()
        scene._reframe_to_nodes(margin=8000.0)
        _mark("finalize_scene", t_phase)

        t_phase = time.perf_counter()
        if getattr(scene, "_current_output_name", None) in scene._node_items:
            scene.recompute_active_path(scene._current_output_name)
        else:
            scene._clear_path_highlight()
        _mark("refresh_active_path", t_phase)
    finally:
        scene._bulk_loading = prev_bulk_loading
        try:
            scene.linksChanged.emit()
        except Exception:
            pass

    total_ms = (time.perf_counter() - t_start) * 1000.0
    top_kinds = []
    for kind, payload in kind_totals.items():
        count = int(payload.get("count", 0.0))
        ms = float(payload.get("ms", 0.0))
        top_kinds.append({
            "kind": kind,
            "count": count,
            "ms": round(ms, 3),
            "avg_ms": round((ms / float(count)) if count > 0 else 0.0, 3),
        })
    top_kinds.sort(key=lambda x: float(x.get("ms", 0.0)), reverse=True)

    return {
        "total_ms": round(total_ms, 3),
        "phase_ms": {k: round(v, 3) for k, v in phase_ms.items()},
        "counts": {
            "nodes": len(nodes_in),
            "edges": len(edges_in),
            "comments": len(comments_in),
        },
        "errors": {
            "edge_errors": int(edge_errors),
            "comment_errors": int(comment_errors),
        },
        "top_nodes": _node_top(node_timings, limit=25),
        "top_edges": _edge_top(edge_timings, limit=20),
        "top_node_kinds": top_kinds[:20],
    }
