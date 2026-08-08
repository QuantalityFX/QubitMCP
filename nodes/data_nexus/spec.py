from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from echograph.qt_compat import QtCore, QtGui, QtWidgets
from nodes.core import Spec


DATA_NEXUS_NODE_KIND = "data_nexus"
DATA_NEXUS_ALIASES = {
    "data nexus",
    "data_graph",
    "data graph",
    "nexus",
}
DATA_NEXUS_KINDS = {DATA_NEXUS_NODE_KIND, *DATA_NEXUS_ALIASES}
DATA_NEXUS_BODY_W = 560
DATA_NEXUS_BODY_H = 380
DATA_NEXUS_HIDDEN_PARAM_KEY = "__ui_hidden_params"
DATA_NEXUS_OUTPUT_PARAM = "nexus"
DATA_NEXUS_GRAPH_PARAM = "__data_nexus_graph_json"
DATA_NEXUS_SIDECAR_PARAM = "__data_nexus_sidecar"
DATA_NEXUS_VAULT_PARAM = "__data_nexus_vault"
DATA_NEXUS_STORAGE_VERSION = 1
DATA_NEXUS_INDEX_FILE = "Index.md"
POINT_ID_RE = re.compile(r"^Point ID:\s*`?(?P<id>[^`\r\n]+)`?\s*$", re.IGNORECASE | re.MULTILINE)


def _default_graph() -> dict[str, Any]:
    return {
        "version": DATA_NEXUS_STORAGE_VERSION,
        "nodes": [],
        "edges": [],
    }


def _kind_of_item(node_item) -> str:
    model = getattr(node_item, "model", None)
    return str(getattr(model, "kind", "") or "").strip().lower()


def _is_data_nexus_item(node_item) -> bool:
    return _kind_of_item(node_item) in DATA_NEXUS_KINDS


def _sanitize_folder_name(value: str, fallback: str = "data_nexus") -> str:
    text = str(value or "").strip()
    out = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("_")
    cleaned = "".join(out).strip("._-")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or fallback


def _sanitize_markdown_file_name(value: str, fallback: str = "point") -> str:
    name = Path(str(value or "").strip()).name
    if name.lower().endswith(".md"):
        name = name[:-3]
    stem = _sanitize_folder_name(name, fallback=fallback)
    return f"{stem}.md"


def _point_file_name(entry: dict[str, Any]) -> str:
    raw = str(entry.get("file", "") or "").strip()
    if raw:
        return _sanitize_markdown_file_name(raw, fallback=str(entry.get("id", "") or "point"))
    point_id = str(entry.get("id", "") or "").strip()
    return _sanitize_markdown_file_name(point_id, fallback="point")


def _param_value_from_model(model, name: str, default: str = "") -> str:
    target = str(name or "").strip().lower()
    if not target or model is None:
        return default
    for entry in (getattr(model, "params", None) or []):
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("name", "") or "").strip().lower()
        if key == target:
            return str(entry.get("value", "") or "")
    return default


def _set_param_value_on_model(model, name: str, value: str) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    target = str(name or "").strip().lower()
    found = False
    for entry in params:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "") or "").strip().lower() == target:
            entry["value"] = str(value or "")
            found = True
            break
    if not found:
        params.append({"name": name, "value": str(value or "")})
    model.params = params


def _set_param_value(node_item, name: str, value: str, *, emit: bool = True) -> None:
    model = getattr(node_item, "model", None)
    _set_param_value_on_model(model, name, value)
    if not emit:
        return
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None and hasattr(scene, "paramChanged"):
        try:
            scene.paramChanged.emit(getattr(model, "name", ""), list(getattr(model, "params", None) or []))
        except Exception:
            pass


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    existing = _param_value_from_model(model, name, None)
    if existing is None:
        _set_param_value_on_model(model, name, default)


def _ensure_hidden_params(node_item) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    raw = _param_value_from_model(model, DATA_NEXUS_HIDDEN_PARAM_KEY, "")
    hidden = {part.strip().lower() for part in str(raw or "").split(",") if part.strip()}
    hidden.update(
        {
            DATA_NEXUS_GRAPH_PARAM.lower(),
            DATA_NEXUS_SIDECAR_PARAM.lower(),
            DATA_NEXUS_VAULT_PARAM.lower(),
        }
    )
    _set_param_value_on_model(model, DATA_NEXUS_HIDDEN_PARAM_KEY, ",".join(sorted(hidden)))


def _coerce_float(value, default: float) -> float:
    try:
        out = float(value)
    except Exception:
        out = default
    return max(0.04, min(0.96, out))


def _coerce_graph(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    raw_nodes = data.get("nodes", [])
    raw_edges = data.get("edges", [])
    nodes = []
    seen = set()
    if isinstance(raw_nodes, list):
        for idx, entry in enumerate(raw_nodes):
            if not isinstance(entry, dict):
                continue
            node_id = str(entry.get("id", "") or "").strip()
            if not node_id:
                node_id = f"point_{idx + 1}"
            node_id = _sanitize_folder_name(node_id, fallback=f"point_{idx + 1}")
            base_id = node_id
            counter = 2
            while node_id in seen:
                node_id = f"{base_id}_{counter}"
                counter += 1
            seen.add(node_id)
            label = str(entry.get("label", "") or "").strip() or node_id.replace("_", " ").title()
            note = str(entry.get("note", "") or "")
            file_name = _sanitize_markdown_file_name(
                str(entry.get("file", "") or ""),
                fallback=node_id or f"point_{idx + 1}",
            ) if str(entry.get("file", "") or "").strip() else ""
            nodes.append(
                {
                    "id": node_id,
                    "label": label[:120],
                    "note": note[:5000],
                    "x": _coerce_float(entry.get("x"), 0.5),
                    "y": _coerce_float(entry.get("y"), 0.5),
                    "file": file_name,
                }
            )
    if not nodes:
        return _default_graph()

    ids = {entry["id"] for entry in nodes}
    edge_keys = set()
    edges = []
    if isinstance(raw_edges, list):
        for entry in raw_edges:
            if not isinstance(entry, dict):
                continue
            source = str(entry.get("source", "") or "").strip()
            target = str(entry.get("target", "") or "").strip()
            if source not in ids or target not in ids or source == target:
                continue
            key = (source, target)
            rev = (target, source)
            if key in edge_keys or rev in edge_keys:
                continue
            edge_keys.add(key)
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "label": str(entry.get("label", "") or "").strip()[:120],
                }
            )
    return {"version": DATA_NEXUS_STORAGE_VERSION, "nodes": nodes, "edges": edges}


def _graph_from_json_text(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return _coerce_graph(json.loads(raw))
    except Exception:
        return None


def _graph_to_json_text(graph: dict[str, Any]) -> str:
    clean = _coerce_graph(graph)
    return json.dumps(clean, ensure_ascii=False, indent=2)


def _workflow_path_for_node(node_item) -> Path | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    workflow_path = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                workflow_path = getattr(views[0].window(), "_current_path", None)
        except Exception:
            pass
        workflow_path = workflow_path or getattr(scene, "_filename", None)
    if not workflow_path:
        return None
    try:
        return Path(workflow_path)
    except Exception:
        return None


def _storage_paths_for(workflow_path: Path, node_name: str) -> tuple[Path, Path, Path]:
    workflow_path = Path(workflow_path)
    workflow_stem = _sanitize_folder_name(workflow_path.stem, "workflow")
    node_slug = _sanitize_folder_name(node_name, DATA_NEXUS_NODE_KIND)
    root = workflow_path.parent / "data_nexus" / workflow_stem / node_slug
    vault = root / "vault"
    sidecar = root / "nexus.json"
    return root, vault, sidecar


def _point_id_from_markdown(text: str) -> str:
    match = POINT_ID_RE.search(str(text or ""))
    if not match:
        return ""
    return _sanitize_folder_name(match.group("id"), fallback="")


def _point_label_from_markdown(text: str, fallback: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:120] or fallback
    return fallback


def _point_note_from_markdown(text: str) -> str:
    lines = []
    skipped_heading = False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not skipped_heading and stripped.startswith("# "):
            skipped_heading = True
            continue
        if POINT_ID_RE.match(stripped):
            continue
        lines.append(line)
    return "\n".join(lines).strip()[:5000]


def _point_markdown(entry: dict[str, Any]) -> str:
    point_id = str(entry.get("id", "") or "").strip()
    label = str(entry.get("label", "") or point_id or "Point").strip()
    note = str(entry.get("note", "") or "").strip()
    body = [
        f"# {label}",
        "",
        f"Point ID: `{point_id}`",
    ]
    if note:
        body.extend(["", note])
    return "\n".join(body).rstrip() + "\n"


def _graph_with_point_files(graph: dict[str, Any]) -> dict[str, Any]:
    clean = _coerce_graph(graph)
    used: set[str] = set()
    nodes = []
    for entry in clean.get("nodes", []) or []:
        next_entry = dict(entry)
        file_name = _point_file_name(next_entry)
        if file_name.lower() == DATA_NEXUS_INDEX_FILE.lower():
            file_name = _sanitize_markdown_file_name(next_entry.get("id", ""), fallback="point")
        stem = Path(file_name).stem
        suffix = 2
        while file_name.lower() in used or file_name.lower() == DATA_NEXUS_INDEX_FILE.lower():
            file_name = _sanitize_markdown_file_name(f"{stem}_{suffix}", fallback=f"point_{suffix}")
            suffix += 1
        next_entry["file"] = file_name
        used.add(file_name.lower())
        nodes.append(next_entry)
    clean["nodes"] = nodes
    return clean


def _graph_from_vault_files(vault: Path, base_graph: dict[str, Any] | None = None) -> dict[str, Any] | None:
    try:
        files = [
            path
            for path in sorted(vault.glob("*.md"), key=lambda item: item.name.lower())
            if path.name.lower() != DATA_NEXUS_INDEX_FILE.lower()
        ]
    except Exception:
        return None
    if not files:
        return None

    base = _graph_with_point_files(base_graph or _default_graph())
    base_by_id = {str(entry.get("id", "") or ""): entry for entry in base.get("nodes", []) or []}
    base_by_file = {
        str(entry.get("file", "") or "").strip().lower(): entry
        for entry in base.get("nodes", []) or []
        if str(entry.get("file", "") or "").strip()
    }
    nodes = []
    seen_ids: set[str] = set()
    for idx, path in enumerate(files):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""
        fallback_id = _sanitize_folder_name(path.stem, fallback=f"point_{idx + 1}")
        point_id = _point_id_from_markdown(text) or fallback_id
        base_id = point_id
        suffix = 2
        while point_id in seen_ids:
            point_id = f"{base_id}_{suffix}"
            suffix += 1
        seen_ids.add(point_id)

        base_entry = base_by_id.get(point_id) or base_by_file.get(path.name.lower()) or {}
        angle = ((idx + 1) * 2.3999632297) % (math.pi * 2.0)
        label_fallback = str(base_entry.get("label", "") or point_id.replace("_", " ").title()).strip()
        nodes.append(
            {
                "id": point_id,
                "label": _point_label_from_markdown(text, label_fallback),
                "note": _point_note_from_markdown(text) or str(base_entry.get("note", "") or ""),
                "x": _coerce_float(base_entry.get("x"), 0.5 + math.cos(angle) * 0.24),
                "y": _coerce_float(base_entry.get("y"), 0.5 + math.sin(angle) * 0.24),
                "file": _sanitize_markdown_file_name(path.name, fallback=point_id),
            }
        )
    return _coerce_graph(
        {
            "version": DATA_NEXUS_STORAGE_VERSION,
            "nodes": nodes,
            "edges": base.get("edges", []),
        }
    )


def _write_vault_readme(root: Path, vault: Path, node_name: str) -> None:
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Data Nexus\n\n"
            f"Node: {node_name}\n\n"
            "This folder belongs to the saved workflow. `nexus.json` stores the point/link graph. "
            "The `vault` folder stores one markdown file per Data Nexus point.\n",
            encoding="utf-8",
        )


def _write_vault_graph_notes(vault: Path, graph: dict[str, Any]) -> None:
    clean = _graph_with_point_files(graph)
    nodes = clean.get("nodes", []) or []
    edges = clean.get("edges", []) or []
    by_id = {entry.get("id"): entry for entry in nodes if isinstance(entry, dict)}
    active_files = {str(entry.get("file", "") or "").strip().lower() for entry in nodes}
    active_ids = {str(entry.get("id", "") or "").strip().lower() for entry in nodes}
    active_file_by_id = {
        str(entry.get("id", "") or "").strip().lower(): str(entry.get("file", "") or "").strip().lower()
        for entry in nodes
    }
    lines = [
        "# Data Nexus Index",
        "",
        "## Points",
    ]
    for entry in nodes:
        label = str(entry.get("label", "") or entry.get("id", "") or "Point").strip()
        file_name = _point_file_name(entry)
        lines.append(f"- [[{Path(file_name).stem}]]")
        point_path = vault / file_name
        point_path.write_text(_point_markdown(entry), encoding="utf-8")

    for path in vault.glob("*.md"):
        if path.name.lower() == DATA_NEXUS_INDEX_FILE.lower():
            continue
        if path.name.lower() in active_files:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""
        point_id = _point_id_from_markdown(text).lower()
        if point_id and (point_id not in active_ids or active_file_by_id.get(point_id) != path.name.lower()):
            try:
                path.unlink()
            except Exception:
                pass

    lines.extend(["", "## Links"])
    for edge in edges:
        src = by_id.get(edge.get("source"), {})
        dst = by_id.get(edge.get("target"), {})
        src_label = str(src.get("label", "") or edge.get("source", "")).strip()
        dst_label = str(dst.get("label", "") or edge.get("target", "")).strip()
        label = str(edge.get("label", "") or "").strip()
        lines.append(f"- {src_label} -> {dst_label}" + (f" ({label})" if label else ""))
    (vault / DATA_NEXUS_INDEX_FILE).write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _ensure_project_storage(node_item, workflow_path: Path | None = None) -> tuple[Path, Path, Path] | None:
    model = getattr(node_item, "model", None)
    if model is None:
        return None
    path = workflow_path or _workflow_path_for_node(node_item)
    if path is None:
        return None
    try:
        root, vault, sidecar = _storage_paths_for(path, getattr(model, "name", "") or DATA_NEXUS_NODE_KIND)
        vault.mkdir(parents=True, exist_ok=True)
        _write_vault_readme(root, vault, getattr(model, "name", "") or DATA_NEXUS_NODE_KIND)
        _set_param_value_on_model(model, DATA_NEXUS_VAULT_PARAM, str(vault))
        _set_param_value_on_model(model, DATA_NEXUS_SIDECAR_PARAM, str(sidecar))
        _ensure_hidden_params(node_item)
        return root, vault, sidecar
    except Exception:
        return None


def _unlink_vault_note_for_entry(node_item, entry: dict[str, Any] | None) -> None:
    if not isinstance(entry, dict):
        return
    model = getattr(node_item, "model", None)
    vault_text = _param_value_from_model(model, DATA_NEXUS_VAULT_PARAM, "")
    if not vault_text:
        return
    try:
        vault = Path(vault_text).resolve()
    except Exception:
        return
    if not vault.is_dir():
        return
    point_id = str(entry.get("id", "") or "").strip().lower()
    targets = {vault / _point_file_name(entry)}
    if point_id:
        try:
            for path in vault.glob("*.md"):
                if path.name.lower() == DATA_NEXUS_INDEX_FILE.lower():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    text = ""
                if _point_id_from_markdown(text).lower() == point_id:
                    targets.add(path)
        except Exception:
            pass
    for path in targets:
        try:
            target = path.resolve()
        except Exception:
            continue
        if target.parent != vault or target.name.lower() == DATA_NEXUS_INDEX_FILE.lower():
            continue
        try:
            if target.is_file():
                target.unlink()
        except Exception:
            pass


def _load_graph_for_node(node_item) -> dict[str, Any]:
    model = getattr(node_item, "model", None)
    loaded = None
    sidecar_text = _param_value_from_model(model, DATA_NEXUS_SIDECAR_PARAM, "")
    if sidecar_text:
        try:
            sidecar = Path(sidecar_text)
            if sidecar.is_file():
                loaded = _graph_from_json_text(sidecar.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass
    if loaded is None:
        loaded = _graph_from_json_text(_param_value_from_model(model, DATA_NEXUS_GRAPH_PARAM, ""))

    vault_text = _param_value_from_model(model, DATA_NEXUS_VAULT_PARAM, "")
    vault = None
    if vault_text:
        try:
            vault = Path(vault_text)
        except Exception:
            vault = None
    if vault is None and sidecar_text:
        try:
            vault = Path(sidecar_text).parent / "vault"
        except Exception:
            vault = None
    if vault is not None and vault.is_dir():
        vault_graph = _graph_from_vault_files(vault, loaded)
        if vault_graph is not None:
            return vault_graph
    if loaded is not None:
        return _graph_with_point_files(loaded)
    return _default_graph()


def _summary_for_graph(graph: dict[str, Any], *, vault_path: str = "") -> str:
    clean = _coerce_graph(graph)
    nodes = clean.get("nodes", []) or []
    edges = clean.get("edges", []) or []
    by_id = {entry.get("id"): entry for entry in nodes if isinstance(entry, dict)}
    lines = [
        f"Data Nexus: {len(nodes)} points, {len(edges)} links.",
        "Judge agent context: read this graph as a connected project memory map.",
    ]
    if vault_path:
        lines.append(f"Vault: {vault_path}")
    if nodes:
        lines.append("Points:")
        for entry in nodes[:30]:
            label = str(entry.get("label", "") or entry.get("id", "") or "Point").strip()
            note = str(entry.get("note", "") or "").strip()
            lines.append(f"- {label}" + (f": {note}" if note else ""))
        if len(nodes) > 30:
            lines.append(f"- ... {len(nodes) - 30} more points")
    if edges:
        lines.append("Links:")
        for edge in edges[:40]:
            src = by_id.get(edge.get("source"), {})
            dst = by_id.get(edge.get("target"), {})
            src_label = str(src.get("label", "") or edge.get("source", "")).strip()
            dst_label = str(dst.get("label", "") or edge.get("target", "")).strip()
            label = str(edge.get("label", "") or "").strip()
            lines.append(f"- {src_label} -> {dst_label}" + (f" ({label})" if label else ""))
        if len(edges) > 40:
            lines.append(f"- ... {len(edges) - 40} more links")
    return "\n".join(lines).strip()


def _sync_model_from_graph(node_item, graph: dict[str, Any], *, workflow_path: Path | None = None, write_sidecar: bool = False) -> bool:
    model = getattr(node_item, "model", None)
    if model is None:
        return False
    clean = _graph_with_point_files(graph)
    paths = _ensure_project_storage(node_item, workflow_path=workflow_path)
    vault_path = ""
    sidecar = None
    if paths is not None:
        _root, vault, sidecar = paths
        vault_path = str(vault)
    graph_text = _graph_to_json_text(clean)
    _set_param_value_on_model(model, DATA_NEXUS_GRAPH_PARAM, graph_text)
    _set_param_value_on_model(model, DATA_NEXUS_OUTPUT_PARAM, _summary_for_graph(clean, vault_path=vault_path))
    _ensure_hidden_params(node_item)
    try:
        model.info = _summary_for_graph(clean, vault_path=vault_path)
    except Exception:
        pass
    if write_sidecar and sidecar is not None:
        try:
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(graph_text + "\n", encoding="utf-8")
            if paths is not None:
                _write_vault_graph_notes(vault, clean)
        except Exception:
            return False
    return True


def _emit_node_params_changed(node_item) -> None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None or not hasattr(scene, "paramChanged"):
        return
    model = getattr(node_item, "model", None)
    try:
        scene.paramChanged.emit(getattr(model, "name", ""), list(getattr(model, "params", None) or []))
    except Exception:
        pass


def _action_text(action: dict[str, Any], *names: str) -> str:
    for name in names:
        raw = action.get(name)
        if raw is not None:
            text = str(raw or "").strip()
            if text:
                return text
    return ""


def _action_bool(action: dict[str, Any], name: str, default: bool) -> bool:
    raw = action.get(name)
    if raw is None:
        return bool(default)
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() not in {"0", "false", "no", "off"}


def _coerce_update_actions(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return []
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    if not isinstance(payload, dict):
        return []
    raw_actions = payload.get("actions")
    if isinstance(raw_actions, list):
        return [entry for entry in raw_actions if isinstance(entry, dict)]
    actions: list[dict[str, Any]] = []
    raw_points = payload.get("points")
    if isinstance(raw_points, list):
        for entry in raw_points:
            if isinstance(entry, dict):
                action = dict(entry)
                action.setdefault("op", "upsert_point")
                actions.append(action)
    raw_links = payload.get("links")
    if isinstance(raw_links, list):
        for entry in raw_links:
            if isinstance(entry, dict):
                action = dict(entry)
                action.setdefault("op", "link")
                actions.append(action)
    if not actions and any(key in payload for key in ("op", "operation", "action", "label", "note", "id")):
        actions.append(payload)
    return actions


def _find_point(nodes: list[dict[str, Any]], ref: str) -> dict[str, Any] | None:
    key = str(ref or "").strip().lower()
    if not key:
        return None
    clean_key = _sanitize_folder_name(ref, fallback="").lower()
    for entry in nodes:
        if str(entry.get("id", "") or "").strip().lower() == clean_key:
            return entry
    for entry in nodes:
        if str(entry.get("label", "") or "").strip().lower() == key:
            return entry
    return None


def _unique_point_id(wanted: str, nodes: list[dict[str, Any]], *, fallback: str = "point") -> str:
    existing = {str(entry.get("id", "") or "").strip().lower() for entry in nodes}
    base = _sanitize_folder_name(wanted, fallback=fallback)
    point_id = base
    suffix = 2
    while point_id.lower() in existing:
        point_id = f"{base}_{suffix}"
        suffix += 1
    return point_id


def _append_note(existing: str, addition: str) -> str:
    current = str(existing or "").strip()
    note = str(addition or "").strip()
    if not note:
        return current
    if note.lower() in current.lower():
        return current
    if not current:
        return note[:5000]
    return f"{current}\n\n{note}"[:5000]


def _new_point(label: str, nodes: list[dict[str, Any]], *, note: str = "", point_id: str = "") -> dict[str, Any]:
    clean_label = str(label or point_id or "Point").strip()[:120] or "Point"
    clean_id = _unique_point_id(point_id or clean_label, nodes)
    idx = len(nodes) + 1
    angle = (idx * 2.3999632297) % (math.pi * 2.0)
    return {
        "id": clean_id,
        "label": clean_label,
        "note": str(note or "").strip()[:5000],
        "x": max(0.08, min(0.92, 0.5 + math.cos(angle) * 0.24)),
        "y": max(0.08, min(0.92, 0.5 + math.sin(angle) * 0.24)),
    }


def _resolve_or_create_point(nodes: list[dict[str, Any]], ref: str, *, create_missing: bool = True) -> dict[str, Any] | None:
    entry = _find_point(nodes, ref)
    if entry is not None or not create_missing:
        return entry
    entry = _new_point(ref, nodes)
    nodes.append(entry)
    return entry


def apply_data_nexus_update_from_item(node_item, payload: Any, *, requester: str = "Mediator") -> tuple[bool, str]:
    actions = _coerce_update_actions(payload)
    if not actions:
        return False, "No valid Data Nexus update actions."

    graph = _graph_with_point_files(_load_graph_for_node(node_item))
    nodes = [dict(entry) for entry in graph.get("nodes", []) or []]
    edges = [dict(edge) for edge in graph.get("edges", []) or []]
    changed = False
    counts = {
        "added": 0,
        "updated": 0,
        "deleted": 0,
        "linked": 0,
        "unlinked": 0,
    }

    for action in actions:
        op = _action_text(action, "op", "operation", "action").lower().replace("-", "_")
        if op in {"add", "add_point", "update", "update_point", "remember", "save", "track"}:
            op = "upsert_point"
        elif op in {"append", "append_point_note", "note"}:
            op = "append_note"
        elif op in {"delete", "remove", "forget", "delete_node", "remove_point"}:
            op = "delete_point"
        elif op in {"connect", "add_link", "link_points"}:
            op = "link"
        elif op in {"disconnect", "remove_link"}:
            op = "unlink"

        if op in {"upsert_point", "append_note"}:
            label = _action_text(action, "label", "name", "title")
            point_id = _action_text(action, "id", "point_id")
            ref = point_id or label
            if not ref:
                continue
            note = _action_text(action, "note", "text", "memory", "summary")
            entry = _find_point(nodes, point_id) if point_id else None
            entry = entry or _find_point(nodes, label)
            was_new = False
            if entry is None:
                entry = _new_point(label or point_id, nodes, note=note, point_id=point_id)
                nodes.append(entry)
                was_new = True
                counts["added"] += 1
                changed = True
            if label and str(entry.get("label", "") or "") != label:
                entry["label"] = label[:120]
                if not was_new:
                    counts["updated"] += 1
                changed = True
            if note:
                mode = _action_text(action, "note_mode", "mode").lower()
                old_note = str(entry.get("note", "") or "")
                if mode == "replace":
                    new_note = note[:5000]
                else:
                    new_note = _append_note(old_note, note)
                if new_note != old_note:
                    entry["note"] = new_note
                    if not was_new:
                        counts["updated"] += 1
                    changed = True
            continue

        if op == "delete_point":
            ref = _action_text(action, "id", "point_id", "label", "name")
            entry = _find_point(nodes, ref)
            if entry is None:
                continue
            point_id = str(entry.get("id", "") or "")
            _unlink_vault_note_for_entry(node_item, entry)
            nodes = [item for item in nodes if item is not entry and item.get("id") != point_id]
            edges = [
                edge
                for edge in edges
                if edge.get("source") != point_id and edge.get("target") != point_id
            ]
            counts["deleted"] += 1
            changed = True
            continue

        if op in {"link", "unlink"}:
            source_ref = _action_text(action, "source", "source_id", "from", "from_id", "source_label")
            target_ref = _action_text(action, "target", "target_id", "to", "to_id", "target_label")
            if not source_ref or not target_ref:
                continue
            create_missing = _action_bool(action, "create_missing", op == "link")
            source = _resolve_or_create_point(nodes, source_ref, create_missing=create_missing)
            target = _resolve_or_create_point(nodes, target_ref, create_missing=create_missing)
            if source is None or target is None or source.get("id") == target.get("id"):
                continue
            source_id = str(source.get("id", "") or "")
            target_id = str(target.get("id", "") or "")
            label = _action_text(action, "label", "relation", "relationship")
            matched = [
                edge
                for edge in edges
                if {edge.get("source"), edge.get("target")} == {source_id, target_id}
            ]
            if op == "unlink":
                if matched:
                    edges = [edge for edge in edges if edge not in matched]
                    counts["unlinked"] += 1
                    changed = True
                continue
            if matched:
                edge = matched[0]
                if label and str(edge.get("label", "") or "") != label:
                    edge["label"] = label[:120]
                    counts["updated"] += 1
                    changed = True
                continue
            edges.append({"source": source_id, "target": target_id, "label": label[:120]})
            counts["linked"] += 1
            changed = True

    if not changed:
        return False, "No Data Nexus changes were applied."

    next_graph = _graph_with_point_files(
        {
            "version": DATA_NEXUS_STORAGE_VERSION,
            "nodes": nodes,
            "edges": edges,
        }
    )
    status_parts = [f"{value} {name}" for name, value in counts.items() if value]
    status = f"Updated by {requester}: " + ", ".join(status_parts) + "."
    widget = getattr(node_item, "_data_nexus_widget", None)
    if widget is not None and hasattr(widget, "_apply_external_graph"):
        try:
            widget._apply_external_graph(next_graph, status)
            return True, status
        except Exception:
            pass
    ok = _sync_model_from_graph(node_item, next_graph, write_sidecar=True)
    _emit_node_params_changed(node_item)
    if not ok:
        return True, f"{status} Save the workflow to create/update vault files."
    return True, status


def ensure_project_storage_for_scene(scene, workflow_path: str | Path) -> None:
    if scene is None or not workflow_path:
        return
    try:
        target = Path(workflow_path)
    except Exception:
        return
    try:
        items = list(getattr(scene, "_node_items", {}).values())
    except Exception:
        items = []
    for item in items:
        if not _is_data_nexus_item(item):
            continue
        graph = _load_graph_for_node(item)
        _sync_model_from_graph(item, graph, workflow_path=target, write_sidecar=True)


def data_nexus_context_from_item(node_item) -> str:
    graph = _load_graph_for_node(node_item)
    model = getattr(node_item, "model", None)
    vault_path = _param_value_from_model(model, DATA_NEXUS_VAULT_PARAM, "")
    return _summary_for_graph(graph, vault_path=vault_path)


class DataNexusCanvas(QtWidgets.QWidget):
    graphChanged = QtCore.Signal()
    selectionChanged = QtCore.Signal(str)
    linkTargetChosen = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._graph = _default_graph()
        self._selected_id = ""
        self._dragging = False
        self._drag_moved = False
        self._link_target_mode = False
        self._drag_offset = QtCore.QPointF(0.0, 0.0)
        self.setMinimumSize(320, 190)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def set_link_target_mode(self, active: bool) -> None:
        self._link_target_mode = bool(active)
        try:
            cursor_group = getattr(QtCore.Qt, "CursorShape", QtCore.Qt)
            cursor_name = "CrossCursor" if self._link_target_mode else "ArrowCursor"
            cursor_shape = getattr(cursor_group, cursor_name)
            self.setCursor(cursor_shape)
        except Exception:
            pass

    def set_graph(self, graph: dict[str, Any]) -> None:
        self._graph = _coerce_graph(graph)
        ids = {entry.get("id") for entry in self._graph.get("nodes", [])}
        if self._selected_id not in ids:
            self._selected_id = ""
            self._dragging = False
            self._drag_moved = False
        self.update()
        self.selectionChanged.emit(self._selected_id)

    def replace_graph(self, graph: dict[str, Any]) -> None:
        self._graph = _coerce_graph(graph)
        ids = {entry.get("id") for entry in self._graph.get("nodes", [])}
        if self._selected_id not in ids:
            self._selected_id = ""
            self._dragging = False
            self._drag_moved = False
        self.update()

    def graph(self) -> dict[str, Any]:
        return _coerce_graph(self._graph)

    def selected_id(self) -> str:
        return self._selected_id

    def select_id(self, point_id: str) -> None:
        ids = {entry.get("id") for entry in self._graph.get("nodes", [])}
        self._selected_id = point_id if point_id in ids else ""
        if not self._selected_id:
            self._dragging = False
            self._drag_moved = False
        self.update()
        self.selectionChanged.emit(self._selected_id)

    def _plot_rect(self) -> QtCore.QRectF:
        return QtCore.QRectF(14.0, 14.0, max(20.0, self.width() - 28.0), max(20.0, self.height() - 28.0))

    def _to_screen(self, x: float, y: float) -> QtCore.QPointF:
        rect = self._plot_rect()
        return QtCore.QPointF(rect.left() + float(x) * rect.width(), rect.top() + float(y) * rect.height())

    def _from_screen(self, point: QtCore.QPointF) -> tuple[float, float]:
        rect = self._plot_rect()
        x = (float(point.x()) - rect.left()) / max(1.0, rect.width())
        y = (float(point.y()) - rect.top()) / max(1.0, rect.height())
        return max(0.04, min(0.96, x)), max(0.04, min(0.96, y))

    def _hit_node(self, point: QtCore.QPointF) -> str:
        best = ""
        best_dist = 999999.0
        for entry in self._graph.get("nodes", []) or []:
            center = self._to_screen(entry.get("x", 0.5), entry.get("y", 0.5))
            dx = center.x() - point.x()
            dy = center.y() - point.y()
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < best_dist and dist <= 14.0:
                best = str(entry.get("id", "") or "")
                best_dist = dist
        return best

    def _node_by_id(self, point_id: str) -> dict[str, Any] | None:
        for entry in self._graph.get("nodes", []) or []:
            if entry.get("id") == point_id:
                return entry
        return None

    @staticmethod
    def _event_posf(event) -> QtCore.QPointF:
        for accessor in ("position", "localPos", "pos"):
            getter = getattr(event, accessor, None)
            if not callable(getter):
                continue
            try:
                point = getter()
            except Exception:
                continue
            try:
                return QtCore.QPointF(float(point.x()), float(point.y()))
            except Exception:
                continue
        return QtCore.QPointF(0.0, 0.0)

    @staticmethod
    def _is_left_button(event) -> bool:
        try:
            button = event.button()
        except Exception:
            return False
        try:
            left_button = QtCore.Qt.MouseButton.LeftButton
        except Exception:
            left_button = QtCore.Qt.LeftButton
        return button == left_button

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QtGui.QColor("#0b1018"))
        plot = self._plot_rect()
        painter.setPen(QtGui.QPen(QtGui.QColor("#1f2937"), 1.0))
        painter.setBrush(QtGui.QColor("#101722"))
        painter.drawRoundedRect(plot, 6.0, 6.0)

        for i in range(1, 4):
            x = plot.left() + plot.width() * i / 4.0
            y = plot.top() + plot.height() * i / 4.0
            painter.setPen(QtGui.QPen(QtGui.QColor("#17202d"), 1.0))
            painter.drawLine(QtCore.QPointF(x, plot.top()), QtCore.QPointF(x, plot.bottom()))
            painter.drawLine(QtCore.QPointF(plot.left(), y), QtCore.QPointF(plot.right(), y))

        nodes = {entry.get("id"): entry for entry in self._graph.get("nodes", []) or []}
        for edge in self._graph.get("edges", []) or []:
            src = nodes.get(edge.get("source"))
            dst = nodes.get(edge.get("target"))
            if not src or not dst:
                continue
            a = self._to_screen(src.get("x", 0.5), src.get("y", 0.5))
            b = self._to_screen(dst.get("x", 0.5), dst.get("y", 0.5))
            painter.setPen(QtGui.QPen(QtGui.QColor("#3b82f6"), 1.8))
            painter.drawLine(a, b)

        font = painter.font()
        font.setPointSize(max(7, font.pointSize()))
        painter.setFont(font)
        for entry in self._graph.get("nodes", []) or []:
            point_id = str(entry.get("id", "") or "")
            center = self._to_screen(entry.get("x", 0.5), entry.get("y", 0.5))
            selected = point_id == self._selected_id
            color = QtGui.QColor("#22d3ee" if selected else "#e5e7eb")
            fill = QtGui.QColor("#123447" if selected else "#16202d")
            painter.setBrush(fill)
            painter.setPen(QtGui.QPen(color, 2.0 if selected else 1.2))
            painter.drawEllipse(center, 7.5, 7.5)
            label = str(entry.get("label", "") or point_id).strip()
            label_rect = QtCore.QRectF(center.x() + 10.0, center.y() - 10.0, 160.0, 22.0)
            painter.setPen(QtGui.QPen(QtGui.QColor("#dbeafe" if selected else "#cbd5e1"), 1.0))
            painter.drawText(label_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, label)

    def mousePressEvent(self, event):
        if self._is_left_button(event):
            point = self._event_posf(event)
            hit = self._hit_node(point)
            if hit:
                self._selected_id = hit
                self.selectionChanged.emit(hit)
                if self._link_target_mode:
                    self._dragging = False
                    self._drag_moved = False
                    self.linkTargetChosen.emit(hit)
                    self.update()
                    event.accept()
                    return
                node = self._node_by_id(hit)
                if node is not None:
                    center = self._to_screen(node.get("x", 0.5), node.get("y", 0.5))
                    self._drag_offset = point - center
                    self._dragging = True
                    self._drag_moved = False
                else:
                    self._dragging = False
                    self._drag_moved = False
                self.update()
                event.accept()
                return
            self._dragging = False
            self._drag_moved = False
            self._selected_id = ""
            self.selectionChanged.emit("")
            self.set_link_target_mode(False)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self._selected_id:
            node = self._node_by_id(self._selected_id)
            if node is not None:
                point = self._event_posf(event) - self._drag_offset
                x, y = self._from_screen(point)
                node["x"] = x
                node["y"] = y
                self._drag_moved = True
                self.graphChanged.emit()
                self.update()
                event.accept()
                return
            self._dragging = False
            self._drag_moved = False
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_left_button(event) and self._dragging:
            self._dragging = False
            if self._drag_moved:
                self.graphChanged.emit()
            self._drag_moved = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DataNexusWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        try:
            setattr(node_item, "_data_nexus_widget", self)
        except Exception:
            pass
        self._link_source_id = ""
        self._commit_pending = False
        self.setMinimumSize(DATA_NEXUS_BODY_W, DATA_NEXUS_BODY_H)
        self.setStyleSheet(
            "QWidget{color:#e5e7eb;}"
            "QLineEdit,QPlainTextEdit{background:#0f1216;color:#e5e7eb;border:1px solid #334155;border-radius:4px;padding:4px;}"
            "QToolButton,QPushButton{background:#1f2937;color:#e5e7eb;border:1px solid #334155;border-radius:4px;padding:4px 8px;}"
            "QToolButton:hover,QPushButton:hover{background:#273548;}"
            "QLabel{color:#cbd5e1;}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(5)
        self._add_btn = self._make_tool_button("Add point", "SP_FileDialogNewFolder")
        self._link_btn = self._make_tool_button("Link selected point", "SP_ArrowRight")
        self._delete_btn = self._make_tool_button("Delete selected point", "SP_TrashIcon")
        self._layout_btn = self._make_tool_button("Arrange points", "SP_BrowserReload")
        self._save_btn = self._make_tool_button("Save nexus sidecar", "SP_DialogSaveButton")
        self._reload_btn = self._make_tool_button("Reload graph from vault files", "SP_DialogResetButton")
        self._open_btn = self._make_tool_button("Open Data Nexus folder", "SP_DirOpenIcon")
        for btn in (
            self._add_btn,
            self._link_btn,
            self._delete_btn,
            self._layout_btn,
            self._save_btn,
            self._reload_btn,
            self._open_btn,
        ):
            toolbar.addWidget(btn, 0)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        body_row = QtWidgets.QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(8)
        self._canvas = DataNexusCanvas()
        body_row.addWidget(self._canvas, 1)

        inspector = QtWidgets.QWidget()
        inspector.setFixedWidth(180)
        inspector_layout = QtWidgets.QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(5)
        self._label_edit = QtWidgets.QLineEdit()
        self._label_edit.setPlaceholderText("Point label")
        self._note_edit = QtWidgets.QPlainTextEdit()
        self._note_edit.setPlaceholderText("Point note")
        self._note_edit.setMaximumHeight(112)
        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("QLabel{color:#94a3b8;}")
        inspector_layout.addWidget(QtWidgets.QLabel("Point"))
        inspector_layout.addWidget(self._label_edit)
        inspector_layout.addWidget(self._note_edit, 1)
        inspector_layout.addWidget(self._status)
        body_row.addWidget(inspector, 0)
        layout.addLayout(body_row, 1)

        self._canvas.set_graph(_load_graph_for_node(node_item))
        self._canvas.graphChanged.connect(self._on_graph_changed)
        self._canvas.selectionChanged.connect(self._on_selection_changed)
        self._canvas.linkTargetChosen.connect(self._on_link_target_chosen)
        self._label_edit.textEdited.connect(self._on_label_edited)
        self._note_edit.textChanged.connect(self._on_note_edited)
        self._add_btn.clicked.connect(self._add_point)
        self._link_btn.clicked.connect(self._begin_link)
        self._delete_btn.clicked.connect(self._delete_selected)
        self._layout_btn.clicked.connect(self._arrange_points)
        self._save_btn.clicked.connect(self._save_now)
        self._reload_btn.clicked.connect(self._reload_from_vault)
        self._open_btn.clicked.connect(self._open_folder)
        self._sync_storage(write_sidecar=True)
        self._on_selection_changed(self._canvas.selected_id())

    def _make_tool_button(self, tooltip: str, standard_pixmap: str) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton()
        btn.setAutoRaise(False)
        btn.setToolTip(tooltip)
        btn.setFixedSize(28, 26)
        try:
            icon = self.style().standardIcon(getattr(QtWidgets.QStyle, standard_pixmap))
            btn.setIcon(icon)
        except Exception:
            btn.setText(tooltip[:1])
        return btn

    def _selected_node(self) -> dict[str, Any] | None:
        selected = self._canvas.selected_id()
        for entry in self._canvas.graph().get("nodes", []) or []:
            if entry.get("id") == selected:
                return entry
        return None

    def _set_status(self, text: str) -> None:
        self._status.setText(text or "")

    def _sync_storage(self, *, write_sidecar: bool = False) -> None:
        ok = _sync_model_from_graph(self._node_item, self._canvas.graph(), write_sidecar=write_sidecar)
        model = getattr(self._node_item, "model", None)
        scene = None
        try:
            scene = self._node_item.scene()
        except Exception:
            scene = None
        if scene is not None and hasattr(scene, "paramChanged"):
            try:
                scene.paramChanged.emit(getattr(model, "name", ""), list(getattr(model, "params", None) or []))
            except Exception:
                pass
        if write_sidecar:
            sidecar = _param_value_from_model(model, DATA_NEXUS_SIDECAR_PARAM, "")
            if sidecar and ok:
                self._set_status(f"Saved {Path(sidecar).name}")
            elif not sidecar:
                self._set_status("Save the workflow to create the Data Nexus folder.")
            else:
                self._set_status("Could not write nexus sidecar.")

    def _apply_external_graph(self, graph: dict[str, Any], status: str = "") -> None:
        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        self._canvas.set_graph(graph)
        self._sync_storage(write_sidecar=True)
        self._set_status(status or "Updated by Mediator.")

    def _on_graph_changed(self) -> None:
        if self._commit_pending:
            return
        self._commit_pending = True
        QtCore.QTimer.singleShot(80, self._commit_graph)

    def _commit_graph(self) -> None:
        self._commit_pending = False
        model = getattr(self._node_item, "model", None)
        has_storage = bool(_param_value_from_model(model, DATA_NEXUS_SIDECAR_PARAM, ""))
        if not has_storage:
            has_storage = _workflow_path_for_node(self._node_item) is not None
        self._sync_storage(write_sidecar=has_storage)

    def _on_selection_changed(self, point_id: str) -> None:
        node = self._selected_node()
        if node is None and self._link_source_id:
            self._link_source_id = ""
            self._canvas.set_link_target_mode(False)
        self._label_edit.blockSignals(True)
        self._note_edit.blockSignals(True)
        try:
            self._label_edit.setText(str(node.get("label", "") if node else ""))
            self._note_edit.setPlainText(str(node.get("note", "") if node else ""))
        finally:
            self._label_edit.blockSignals(False)
            self._note_edit.blockSignals(False)
        self._delete_btn.setEnabled(node is not None)
        self._link_btn.setEnabled(node is not None)
        if node is None:
            self._set_status("Select a point or add a new one.")
        elif self._link_source_id:
            self._set_status("Select another point to link.")
        else:
            links = [
                edge
                for edge in self._canvas.graph().get("edges", []) or []
                if edge.get("source") == point_id or edge.get("target") == point_id
            ]
            self._set_status(f"{len(links)} connected link(s).")

    def _on_label_edited(self, text: str) -> None:
        selected = self._canvas.selected_id()
        graph = self._canvas.graph()
        for entry in graph.get("nodes", []) or []:
            if entry.get("id") == selected:
                entry["label"] = str(text or "").strip()[:120] or entry.get("id", "Point")
                break
        self._canvas.replace_graph(graph)
        self._on_graph_changed()

    def _on_note_edited(self) -> None:
        selected = self._canvas.selected_id()
        graph = self._canvas.graph()
        for entry in graph.get("nodes", []) or []:
            if entry.get("id") == selected:
                entry["note"] = self._note_edit.toPlainText()[:5000]
                break
        self._canvas.replace_graph(graph)
        self._on_graph_changed()

    def _add_point(self) -> None:
        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        graph = self._canvas.graph()
        nodes = list(graph.get("nodes", []) or [])
        ids = {entry.get("id") for entry in nodes}
        idx = len(nodes) + 1
        point_id = f"point_{idx}"
        while point_id in ids:
            idx += 1
            point_id = f"point_{idx}"
        angle = (idx * 2.3999632297) % (math.pi * 2.0)
        radius = 0.24
        nodes.append(
            {
                "id": point_id,
                "label": f"Point {idx}",
                "note": "",
                "x": max(0.08, min(0.92, 0.5 + math.cos(angle) * radius)),
                "y": max(0.08, min(0.92, 0.5 + math.sin(angle) * radius)),
            }
        )
        graph["nodes"] = nodes
        self._canvas.set_graph(graph)
        self._canvas.select_id(point_id)
        self._on_graph_changed()

    def _begin_link(self) -> None:
        selected = self._canvas.selected_id()
        if not selected:
            return
        self._link_source_id = selected
        self._canvas.set_link_target_mode(True)
        self._set_status("Select another point to link.")

    def _on_link_target_chosen(self, target_id: str) -> None:
        if not self._link_source_id or not target_id or target_id == self._link_source_id:
            return
        graph = self._canvas.graph()
        edges = list(graph.get("edges", []) or [])
        exists = any(
            {
                edge.get("source"),
                edge.get("target"),
            }
            == {self._link_source_id, target_id}
            for edge in edges
        )
        if not exists:
            edges.append({"source": self._link_source_id, "target": target_id, "label": ""})
            graph["edges"] = edges
            self._canvas.set_graph(graph)
            self._canvas.select_id(target_id)
            self._on_graph_changed()
        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        self._on_selection_changed(target_id)

    def _delete_selected(self) -> None:
        selected = self._canvas.selected_id()
        if not selected:
            return
        deleted_node = self._selected_node()
        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        _unlink_vault_note_for_entry(self._node_item, deleted_node)
        graph = self._canvas.graph()
        graph["nodes"] = [entry for entry in graph.get("nodes", []) or [] if entry.get("id") != selected]
        graph["edges"] = [
            edge
            for edge in graph.get("edges", []) or []
            if edge.get("source") != selected and edge.get("target") != selected
        ]
        self._canvas.set_graph(graph)
        self._sync_storage(write_sidecar=True)

    def _arrange_points(self) -> None:
        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        graph = self._canvas.graph()
        nodes = list(graph.get("nodes", []) or [])
        total = max(1, len(nodes))
        for idx, entry in enumerate(nodes):
            angle = (idx / total) * math.pi * 2.0 - math.pi / 2.0
            entry["x"] = 0.5 + math.cos(angle) * 0.34
            entry["y"] = 0.5 + math.sin(angle) * 0.34
        graph["nodes"] = nodes
        self._canvas.set_graph(graph)
        self._on_graph_changed()

    def _save_now(self) -> None:
        self._sync_storage(write_sidecar=True)

    def _reload_from_vault(self) -> None:
        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        graph = _load_graph_for_node(self._node_item)
        self._canvas.set_graph(graph)
        self._sync_storage(write_sidecar=True)
        self._set_status("Reloaded vault files.")

    def _open_folder(self) -> None:
        model = getattr(self._node_item, "model", None)
        folder = _param_value_from_model(model, DATA_NEXUS_VAULT_PARAM, "")
        if not folder:
            paths = _ensure_project_storage(self._node_item)
            if paths is not None:
                _root, vault, _sidecar = paths
                folder = str(vault)
        if not folder:
            self._set_status("Save the workflow to create the Data Nexus folder.")
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            self._set_status(f"Open folder failed: {exc}")


def build_ports(node_item) -> None:
    try:
        setattr(node_item, "_hide_default_output_with_named", True)
    except Exception:
        pass
    _ensure_param(node_item, DATA_NEXUS_OUTPUT_PARAM, "Data Nexus output")
    _ensure_param(node_item, DATA_NEXUS_GRAPH_PARAM, _graph_to_json_text(_default_graph()))
    _ensure_param(node_item, DATA_NEXUS_SIDECAR_PARAM, "")
    _ensure_param(node_item, DATA_NEXUS_VAULT_PARAM, "")
    _ensure_hidden_params(node_item)
    if hasattr(node_item, "ensure_output"):
        node_item.ensure_output(DATA_NEXUS_OUTPUT_PARAM)


def render_node_body(node_item, y_cursor: int) -> int:
    try:
        body = DataNexusWidget(node_item, None)
    except Exception as exc:
        print("[EchoGraph] Data Nexus UI init failed:", exc)
        body = QtWidgets.QFrame()
        body.setStyleSheet(
            "QFrame{background:#0f1216;color:#e5e7eb;border:1px solid #334;border-radius:6px;}"
        )
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(10, 8, 10, 8)
        msg = QtWidgets.QLabel("Data Nexus UI failed to load. Check console output for details.")
        msg.setWordWrap(True)
        msg.setStyleSheet("QLabel{color:#e5e7eb;}")
        lay.addWidget(msg, 1)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    h = max(DATA_NEXUS_BODY_H, int(hint.height()))
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    bottom_y = int(y_cursor + h)
    try:
        pad = float(getattr(node_item, "_PADDING", 0))
        required_height = float(bottom_y) + pad
        if float(getattr(node_item, "height", 0.0) or 0.0) < required_height:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = required_height
            try:
                node_item.update()
            except Exception:
                pass
    except Exception:
        pass
    return bottom_y


DATA_NEXUS_SPEC = Spec(
    stripe_color="#22d3ee",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
