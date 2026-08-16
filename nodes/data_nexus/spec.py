from __future__ import annotations

import json
import math
import os
import re
import subprocess
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from echograph.qt_compat import QtCore, QtGui, QtWidgets, _qexec
from nodes.core import Spec


DATA_NEXUS_NODE_KIND = "data_nexus"
DATA_NEXUS_ALIASES = {
    "data nexus",
    "data_graph",
    "data graph",
    "nexus",
}
DATA_NEXUS_KINDS = {DATA_NEXUS_NODE_KIND, *DATA_NEXUS_ALIASES}
DATA_NEXUS_BODY_W = 760
DATA_NEXUS_BODY_H = 400
DATA_NEXUS_HIDDEN_PARAM_KEY = "__ui_hidden_params"
DATA_NEXUS_OUTPUT_PARAM = "nexus"
DATA_NEXUS_GRAPH_PARAM = "__data_nexus_graph_json"
DATA_NEXUS_SIDECAR_PARAM = "__data_nexus_sidecar"
DATA_NEXUS_VAULT_PARAM = "__data_nexus_vault"
DATA_NEXUS_STORAGE_MODE_PARAM = "__data_nexus_storage_mode"
DATA_NEXUS_STORAGE_VERSION = 1
DATA_NEXUS_INDEX_FILE = "Index.md"
DATA_NEXUS_MIN_ZOOM = 0.125
DATA_NEXUS_MAX_ZOOM = 8.0
DATA_NEXUS_WORLD_MIN = -16.0
DATA_NEXUS_WORLD_MAX = 17.0
DATA_NEXUS_REPULSION_GRID_DISTANCE = 0.18
DATA_NEXUS_LINK_MIN_GRID_DISTANCE = 0.22
DATA_NEXUS_LINK_MAX_GRID_DISTANCE = 0.48
DATA_NEXUS_LABEL_MIN_ZOOM = 0.55
DATA_NEXUS_GRID_BASE_PX = 360.0
POINT_ID_RE = re.compile(r"^Point ID:\s*`?(?P<id>[^`\r\n]+)`?\s*$", re.IGNORECASE | re.MULTILINE)
FRONT_MATTER_RE = re.compile(r"\A---\s*\r?\n(?P<front>.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)
AUTO_SUMMARY_RE = re.compile(r"^Contains knowledge for .+\.$", re.IGNORECASE)


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
    stem = _sanitize_folder_name(name, fallback=fallback).lower()
    return f"{stem}.md"


def _clean_inline_text(value: Any, *, limit: int = 500) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit].strip()


def _normalize_point_type(value: Any, fallback: str = "concept") -> str:
    return _sanitize_folder_name(str(value or "").strip().lower(), fallback=fallback)


def _derive_summary(value: Any, fallback_title: str = "") -> str:
    text = str(value or "").strip()
    for line in text.splitlines():
        clean = _clean_inline_text(line, limit=240)
        if clean:
            return clean
    return ""


def _is_auto_summary(value: Any) -> bool:
    text = _clean_inline_text(value, limit=500)
    return bool(text and AUTO_SUMMARY_RE.match(text))


def _yaml_scalar(value: Any) -> str:
    return json.dumps(_clean_inline_text(value, limit=500), ensure_ascii=False)


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    raw = str(text or "")
    match = FRONT_MATTER_RE.match(raw)
    if not match:
        return {}, raw
    metadata: dict[str, str] = {}
    for raw_line in match.group("front").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            if value[0] == '"':
                try:
                    value = str(json.loads(value))
                except Exception:
                    value = value[1:-1]
            else:
                value = value[1:-1]
        metadata[key] = value.strip()
    return metadata, raw[match.end():].lstrip("\r\n")


def _point_metadata_from_markdown(text: str) -> dict[str, str]:
    metadata, _body = _parse_front_matter(text)
    return metadata


def _point_file_name(entry: dict[str, Any]) -> str:
    point_id = str(entry.get("id", "") or "").strip()
    title = str(entry.get("title", "") or entry.get("label", "") or "").strip()
    preferred = _sanitize_markdown_file_name(title or point_id, fallback=point_id or "point")
    raw = str(entry.get("file", "") or "").strip()
    if raw:
        current = _sanitize_markdown_file_name(raw, fallback=point_id or "point")
        id_file = _sanitize_markdown_file_name(point_id, fallback="point")
        if title and current.lower() == id_file.lower() and preferred.lower() != id_file.lower():
            return preferred
        return current
    return preferred


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
            DATA_NEXUS_OUTPUT_PARAM.lower(),
            DATA_NEXUS_GRAPH_PARAM.lower(),
            DATA_NEXUS_SIDECAR_PARAM.lower(),
            DATA_NEXUS_VAULT_PARAM.lower(),
            DATA_NEXUS_STORAGE_MODE_PARAM.lower(),
        }
    )
    _set_param_value_on_model(model, DATA_NEXUS_HIDDEN_PARAM_KEY, ",".join(sorted(hidden)))


def _coerce_float(value, default: float) -> float:
    try:
        out = float(value)
    except Exception:
        out = default
    return max(DATA_NEXUS_WORLD_MIN, min(DATA_NEXUS_WORLD_MAX, out))


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
            label = str(entry.get("label", "") or entry.get("title", "") or "").strip() or node_id.replace("_", " ").title()
            title = str(entry.get("title", "") or label).strip()[:120]
            note = str(entry.get("note", "") or entry.get("content", "") or "")
            point_type = _normalize_point_type(entry.get("type", "concept"))
            summary = _clean_inline_text(entry.get("summary", ""), limit=500) or _derive_summary(note, title)
            file_name = _sanitize_markdown_file_name(
                str(entry.get("file", "") or ""),
                fallback=node_id or f"point_{idx + 1}",
            ) if str(entry.get("file", "") or "").strip() else ""
            nodes.append(
                {
                    "id": node_id,
                    "label": label[:120],
                    "title": title,
                    "type": point_type,
                    "summary": summary,
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


def _graph_to_sidecar_json_text(graph: dict[str, Any]) -> str:
    clean = _graph_with_point_files(graph)
    nodes = []
    for entry in clean.get("nodes", []) or []:
        if not isinstance(entry, dict):
            continue
        nodes.append(
            {
                "id": str(entry.get("id", "") or "").strip(),
                "x": _coerce_float(entry.get("x"), 0.5),
                "y": _coerce_float(entry.get("y"), 0.5),
                "file": _point_file_name(entry),
            }
        )
    sidecar = {
        "version": DATA_NEXUS_STORAGE_VERSION,
        "nodes": nodes,
        "edges": clean.get("edges", []) or [],
    }
    return json.dumps(sidecar, ensure_ascii=False, indent=2)


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


def _dialog_parent_for_node(node_item) -> QtWidgets.QWidget | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                return views[0].window()
        except Exception:
            pass
    try:
        parent = node_item.window()
        if parent is not None:
            return parent
    except Exception:
        pass
    try:
        return QtWidgets.QApplication.activeWindow()
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
    metadata = _point_metadata_from_markdown(text)
    point_id = str(metadata.get("id", "") or "").strip()
    if point_id:
        return _sanitize_folder_name(point_id, fallback="")
    match = POINT_ID_RE.search(str(text or ""))
    if not match:
        return ""
    return _sanitize_folder_name(match.group("id"), fallback="")


def _point_label_from_markdown(text: str, fallback: str) -> str:
    metadata, body = _parse_front_matter(text)
    title = str(metadata.get("title", "") or "").strip()
    if title:
        return title[:120]
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:120] or fallback
    return fallback


def _point_note_from_markdown(text: str) -> str:
    _metadata, body = _parse_front_matter(text)
    lines = []
    skipped_heading = False
    for line in body.splitlines():
        stripped = line.strip()
        if not skipped_heading and stripped.startswith("# "):
            skipped_heading = True
            continue
        if POINT_ID_RE.match(stripped):
            continue
        lines.append(line)
    return "\n".join(lines).strip()[:5000]


def _point_type_from_markdown(text: str) -> str:
    metadata = _point_metadata_from_markdown(text)
    return _normalize_point_type(metadata.get("type", ""), fallback="")


def _point_summary_from_markdown(text: str, *, title: str = "", note: str = "") -> str:
    metadata = _point_metadata_from_markdown(text)
    summary = _clean_inline_text(metadata.get("summary", ""), limit=500)
    return summary or _derive_summary(note, title)


def _point_markdown(entry: dict[str, Any]) -> str:
    point_id = str(entry.get("id", "") or "").strip()
    label = str(entry.get("title", "") or entry.get("label", "") or point_id or "Point").strip()
    point_type = _normalize_point_type(entry.get("type", "concept"))
    note = str(entry.get("note", "") or "").strip()
    raw_summary = _clean_inline_text(entry.get("summary", ""), limit=500)
    summary = _derive_summary(note, label) if _is_auto_summary(raw_summary) else raw_summary
    summary = summary or _derive_summary(note, label)
    body = [
        "---",
        f"id: {_yaml_scalar(point_id)}",
        f"type: {_yaml_scalar(point_type)}",
        f"title: {_yaml_scalar(label)}",
        f"summary: {_yaml_scalar(summary)}",
        "---",
        "",
        f"# {label}",
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
        title = str(next_entry.get("title", "") or next_entry.get("label", "") or next_entry.get("id", "") or "").strip()
        summary = str(next_entry.get("summary", "") or "").strip()
        if not summary or _is_auto_summary(summary):
            next_entry["summary"] = _derive_summary(str(next_entry.get("note", "") or ""), title)
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
        label = _point_label_from_markdown(text, label_fallback)
        note = _point_note_from_markdown(text) or str(base_entry.get("note", "") or "")
        nodes.append(
            {
                "id": point_id,
                "label": label,
                "title": label,
                "type": _point_type_from_markdown(text) or str(base_entry.get("type", "") or "concept"),
                "summary": _point_summary_from_markdown(text, title=label, note=note)
                or str(base_entry.get("summary", "") or ""),
                "note": note,
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
        label = str(entry.get("title", "") or entry.get("label", "") or entry.get("id", "") or "Point").strip()
        point_type = str(entry.get("type", "") or "").strip()
        summary = str(entry.get("summary", "") or "").strip()
        file_name = _point_file_name(entry)
        type_text = f" [{point_type}]" if point_type else ""
        summary_text = f" - {summary}" if summary else ""
        lines.append(f"- [[{Path(file_name).stem}]] {label}{type_text}{summary_text}")
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
    mode = str(_param_value_from_model(model, DATA_NEXUS_STORAGE_MODE_PARAM, "") or "").strip().lower()
    if mode in {"external", "vault", "opened_vault"}:
        vault_text = _param_value_from_model(model, DATA_NEXUS_VAULT_PARAM, "")
        sidecar_text = _param_value_from_model(model, DATA_NEXUS_SIDECAR_PARAM, "")
        if not vault_text and not sidecar_text:
            return None
        try:
            vault = Path(vault_text).expanduser().resolve() if vault_text else Path(sidecar_text).expanduser().resolve().parent / "vault"
            sidecar = Path(sidecar_text).expanduser().resolve() if sidecar_text else vault.parent / "nexus.json"
            root = sidecar.parent
            vault.mkdir(parents=True, exist_ok=True)
            _write_vault_readme(root, vault, getattr(model, "name", "") or DATA_NEXUS_NODE_KIND)
            _set_param_value_on_model(model, DATA_NEXUS_VAULT_PARAM, str(vault))
            _set_param_value_on_model(model, DATA_NEXUS_SIDECAR_PARAM, str(sidecar))
            _ensure_hidden_params(node_item)
            return root, vault, sidecar
        except Exception:
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


def _graph_from_data_nexus_folder(folder: str | Path) -> tuple[bool, str, dict[str, Any] | None, Path | None, Path | None]:
    try:
        selected = Path(folder).expanduser().resolve()
    except Exception as exc:
        return False, f"Invalid Data Nexus folder: {exc}", None, None, None
    if not selected.is_dir():
        return False, f"Folder does not exist: {selected}", None, None, None

    root = selected
    vault = selected / "vault"
    sidecar = selected / "nexus.json"
    if selected.name.lower() == "vault":
        vault = selected
        root = selected.parent
        sidecar = root / "nexus.json"
    elif not vault.is_dir() and any(path.suffix.lower() == ".md" for path in selected.glob("*.md")):
        vault = selected
        sidecar = selected.parent / "nexus.json"
        root = selected.parent if sidecar.is_file() else selected

    if not vault.is_dir():
        return False, "Choose a Data Nexus folder containing `nexus.json` and `vault/`, or choose the `vault` folder itself.", None, None, None

    base_graph = None
    if sidecar.is_file():
        try:
            base_graph = _graph_from_json_text(sidecar.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            base_graph = None

    graph = _graph_from_vault_files(vault, base_graph)
    if graph is None and base_graph is not None:
        graph = _graph_with_point_files(base_graph)
    if graph is None:
        return False, f"No Data Nexus markdown point files found in: {vault}", None, root, vault

    nodes = graph.get("nodes", []) or []
    return True, f"Loaded {len(nodes)} point(s) from {vault}.", graph, root, vault


def _set_opened_vault_on_item(node_item, root: Path, vault: Path, graph: dict[str, Any]) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    root = Path(root).expanduser().resolve()
    vault = Path(vault).expanduser().resolve()
    sidecar = root / "nexus.json"
    clean = _graph_with_point_files(graph)
    graph_text = _graph_to_json_text(clean)
    compact_output = _compact_output_for_graph(clean, vault_path=str(vault))
    _set_param_value_on_model(model, DATA_NEXUS_STORAGE_MODE_PARAM, "external")
    _set_param_value_on_model(model, DATA_NEXUS_VAULT_PARAM, str(vault))
    _set_param_value_on_model(model, DATA_NEXUS_SIDECAR_PARAM, str(sidecar))
    _set_param_value_on_model(model, DATA_NEXUS_GRAPH_PARAM, graph_text)
    _set_param_value_on_model(model, DATA_NEXUS_OUTPUT_PARAM, compact_output)
    _ensure_hidden_params(node_item)
    try:
        model.info = compact_output
    except Exception:
        pass


def import_data_nexus_folder_into_item(node_item, folder: str | Path) -> tuple[bool, str]:
    ok, message, graph, _root, _vault = _graph_from_data_nexus_folder(folder)
    if not ok or graph is None:
        return False, message
    synced = _sync_model_from_graph(node_item, graph, write_sidecar=True)
    count = len(graph.get("nodes", []) or [])
    if synced:
        return True, f"Imported {count} Data Nexus point(s)."
    return True, f"Imported {count} Data Nexus point(s). Save the workflow to create/update the active vault folder."


def open_data_nexus_folder_on_item(node_item, folder: str | Path) -> tuple[bool, str]:
    ok, message, graph, root, vault = _graph_from_data_nexus_folder(folder)
    if not ok or graph is None or root is None or vault is None:
        return False, message
    _set_opened_vault_on_item(node_item, root, vault, graph)
    return True, f"Opened Data Nexus vault with {len(graph.get('nodes', []) or [])} point(s)."


def merge_data_nexus_folder_into_item(node_item, folder: str | Path) -> tuple[bool, str]:
    ok, message, graph, _root, _vault = _graph_from_data_nexus_folder(folder)
    if not ok or graph is None:
        return False, message
    merged, added_nodes, added_edges = _merge_graphs(_load_graph_for_node(node_item), graph)
    synced = _sync_model_from_graph(node_item, merged, write_sidecar=True)
    detail = f"Added {added_nodes} point(s) and {added_edges} link(s)."
    if synced:
        return True, detail
    return True, f"{detail} Save the workflow to create/update the active vault folder."


def _merge_graphs(base_graph: dict[str, Any], import_graph: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
    base = _graph_with_point_files(base_graph)
    incoming = _graph_with_point_files(import_graph)
    nodes = [dict(entry) for entry in base.get("nodes", []) or [] if isinstance(entry, dict)]
    edges = [dict(edge) for edge in base.get("edges", []) or [] if isinstance(edge, dict)]
    existing_ids = {str(entry.get("id", "") or "").strip().lower() for entry in nodes}
    id_map: dict[str, str] = {}
    added_nodes = 0

    for entry in incoming.get("nodes", []) or []:
        if not isinstance(entry, dict):
            continue
        original_id = str(entry.get("id", "") or "").strip()
        next_entry = dict(entry)
        wanted_id = original_id or str(next_entry.get("title", "") or next_entry.get("label", "") or "point")
        next_id = _sanitize_folder_name(wanted_id, fallback="point")
        if next_id.lower() in existing_ids:
            next_id = _unique_point_id(next_id, nodes)
        next_entry["id"] = next_id
        if original_id:
            id_map[original_id] = next_id
        existing_ids.add(next_id.lower())
        nodes.append(next_entry)
        added_nodes += 1

    existing_edge_keys = {
        (
            str(edge.get("source", "") or "").strip().lower(),
            str(edge.get("target", "") or "").strip().lower(),
            str(edge.get("label", "") or "").strip().lower(),
        )
        for edge in edges
    }
    added_edges = 0
    for edge in incoming.get("edges", []) or []:
        if not isinstance(edge, dict):
            continue
        source = id_map.get(str(edge.get("source", "") or "").strip(), "")
        target = id_map.get(str(edge.get("target", "") or "").strip(), "")
        if not source or not target or source == target:
            continue
        label = str(edge.get("label", "") or "").strip()
        key = (source.lower(), target.lower(), label.lower())
        if key in existing_edge_keys:
            continue
        existing_edge_keys.add(key)
        edges.append({"source": source, "target": target, "label": label})
        added_edges += 1

    return _graph_with_point_files({"version": DATA_NEXUS_STORAGE_VERSION, "nodes": nodes, "edges": edges}), added_nodes, added_edges


def _summary_for_graph(graph: dict[str, Any], *, vault_path: str = "") -> str:
    clean = _coerce_graph(graph)
    nodes = clean.get("nodes", []) or []
    edges = clean.get("edges", []) or []
    by_id = {entry.get("id"): entry for entry in nodes if isinstance(entry, dict)}
    lines = [
        f"Data Nexus: {len(nodes)} points, {len(edges)} links.",
        "Mediator Planner context: read this graph as a connected project memory map.",
    ]
    if vault_path:
        lines.append(f"Vault: {vault_path}")
    if nodes:
        lines.append("Points:")
        for entry in nodes[:30]:
            label = str(entry.get("title", "") or entry.get("label", "") or entry.get("id", "") or "Point").strip()
            point_type = str(entry.get("type", "") or "").strip()
            summary = str(entry.get("summary", "") or "").strip()
            note = str(entry.get("note", "") or "").strip()
            body = summary or note
            type_text = f" [{point_type}]" if point_type else ""
            lines.append(f"- {label}{type_text}" + (f": {body}" if body else ""))
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


def _compact_output_for_graph(graph: dict[str, Any], *, vault_path: str = "") -> str:
    clean = _coerce_graph(graph)
    nodes = clean.get("nodes", []) or []
    edges = clean.get("edges", []) or []
    lines = [f"Data Nexus: {len(nodes)} points, {len(edges)} links."]
    if vault_path:
        lines.append(f"Vault: {vault_path}")
    lines.append("Full point content is stored in the Data Nexus vault and graph state.")
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
    compact_output = _compact_output_for_graph(clean, vault_path=vault_path)
    _set_param_value_on_model(model, DATA_NEXUS_GRAPH_PARAM, graph_text)
    _set_param_value_on_model(model, DATA_NEXUS_OUTPUT_PARAM, compact_output)
    _ensure_hidden_params(node_item)
    try:
        model.info = compact_output
    except Exception:
        pass
    if write_sidecar and sidecar is not None:
        try:
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(_graph_to_sidecar_json_text(clean) + "\n", encoding="utf-8")
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


def _action_list(action: dict[str, Any], *names: str) -> list[str]:
    out: list[str] = []
    for name in names:
        raw = action.get(name)
        if raw is None:
            continue
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            text = str(value or "").strip()
            if text and text not in out:
                out.append(text)
    return out


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
    if not actions and any(key in payload for key in ("op", "operation", "action", "label", "note", "description", "comment", "id")):
        actions.append(payload)
    return actions


def _normalized_action_op(action: dict[str, Any]) -> str:
    op = _action_text(action, "op", "operation", "action").lower().replace("-", "_")
    if op in {"add", "add_point", "update", "update_point", "remember", "save", "track"}:
        return "upsert_point"
    if op in {"append", "append_point_note", "note"}:
        return "append_note"
    if op in {"delete", "remove", "forget", "delete_node", "remove_point"}:
        return "delete_point"
    if op in {"connect", "add_link", "link_points"}:
        return "link"
    if op in {"disconnect", "remove_link"}:
        return "unlink"
    if op in {"prune", "prune_points", "keep_only", "keep_only_points", "delete_unrelated"}:
        return "prune_points"
    if op in {"delete_all", "delete_all_points", "remove_all", "remove_all_points", "clear_all", "clear_graph", "clear_points"}:
        return "delete_all_points"
    return op


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
    for entry in nodes:
        if str(entry.get("title", "") or "").strip().lower() == key:
            return entry
    return None


def _lookup_text(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _lookup_tokens(value: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "called",
        "can",
        "contains",
        "data",
        "does",
        "for",
        "graph",
        "is",
        "me",
        "nexus",
        "note",
        "point",
        "read",
        "say",
        "says",
        "show",
        "tell",
        "the",
        "to",
        "vault",
        "what",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _lookup_text(value))
        if len(token) > 1 and token not in stopwords
    }


def _lookup_token_family(token: str) -> str:
    text = str(token or "").strip().lower()
    if text.startswith("compet"):
        return "compet"
    if text.startswith("differ") or text.startswith("difer"):
        return "differ"
    return ""


def _lookup_tokens_match(query_token: str, content_token: str) -> bool:
    query = str(query_token or "").strip().lower()
    content = str(content_token or "").strip().lower()
    if not query or not content:
        return False
    if query == content:
        return True
    if len(query) < 4 or len(content) < 4:
        return False
    query_family = _lookup_token_family(query)
    if query_family and query_family == _lookup_token_family(content):
        return True
    return SequenceMatcher(None, query, content).ratio() >= 0.88


def _fuzzy_token_match_count(query_tokens: set[str], content_tokens: set[str]) -> int:
    if not query_tokens or not content_tokens:
        return 0
    matched = 0
    for query_token in query_tokens:
        if any(_lookup_tokens_match(query_token, content_token) for content_token in content_tokens):
            matched += 1
    return matched


def _point_lookup_score(entry: dict[str, Any], query: str) -> int:
    q = _lookup_text(query)
    if not q:
        return 0
    point_id = _lookup_text(str(entry.get("id", "") or ""))
    label = _lookup_text(str(entry.get("label", "") or entry.get("title", "") or ""))
    title = _lookup_text(str(entry.get("title", "") or ""))
    file_stem = _lookup_text(Path(_point_file_name(entry)).stem)
    point_type = _lookup_text(str(entry.get("type", "") or ""))
    summary = _lookup_text(str(entry.get("summary", "") or ""))
    note = _lookup_text(str(entry.get("note", "") or ""))
    content = " ".join(part for part in (point_id, label, title, file_stem, point_type, summary, note) if part)
    if q in {point_id, label, title, file_stem}:
        return 100
    if label.startswith(q) or title.startswith(q) or point_id.startswith(q) or file_stem.startswith(q):
        return 92
    if q in label or q in title or q in point_id or q in file_stem:
        return 86
    query_tokens = _lookup_tokens(query)
    if not query_tokens:
        return 0
    label_tokens = _lookup_tokens(label)
    id_tokens = _lookup_tokens(point_id)
    file_tokens = _lookup_tokens(file_stem)
    content_tokens = _lookup_tokens(content)
    key_tokens = label_tokens | id_tokens | file_tokens
    if query_tokens and query_tokens.issubset(key_tokens):
        return 78 + len(query_tokens)
    if _fuzzy_token_match_count(query_tokens, key_tokens) == len(query_tokens):
        return 74 + len(query_tokens)
    overlap = query_tokens & content_tokens
    fuzzy_count = _fuzzy_token_match_count(query_tokens, content_tokens)
    match_count = max(len(overlap), fuzzy_count)
    if not match_count:
        return 0
    return int((match_count / max(1, len(query_tokens))) * 70)


def _find_point_for_query(nodes: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    best_entry = None
    best_score = 0
    for entry in nodes:
        score = _point_lookup_score(entry, query)
        if score > best_score:
            best_score = score
            best_entry = entry
    if best_score < 45:
        return None
    return best_entry


def read_data_nexus_point_from_item(node_item, query: str) -> tuple[bool, str]:
    graph = _graph_with_point_files(_load_graph_for_node(node_item))
    nodes = [dict(entry) for entry in graph.get("nodes", []) or [] if isinstance(entry, dict)]
    if not nodes:
        return False, "The connected Data Nexus has no points."
    entry = _find_point_for_query(nodes, query)
    if entry is None:
        return False, f"I could not find a Data Nexus point matching '{str(query or '').strip()}'."
    label = str(entry.get("title", "") or entry.get("label", "") or entry.get("id", "") or "Point").strip()
    point_id = str(entry.get("id", "") or "").strip()
    point_type = str(entry.get("type", "") or "").strip()
    summary = str(entry.get("summary", "") or "").strip()
    file_name = _point_file_name(entry)
    note = str(entry.get("note", "") or "").strip()
    lines = [f"Data Nexus point: {label}"]
    if point_type:
        lines.extend(["", f"Type: {point_type}"])
    if summary:
        lines.extend(["", f"Summary: {summary}"])
    if note:
        lines.extend(["", "Content:", note])
    else:
        lines.extend(["", "This point has no note body. Its title is the only saved text."])
    details = []
    if point_id:
        details.append(f"id: {point_id}")
    if file_name:
        details.append(f"file: {file_name}")
    if details:
        lines.extend(["", "(" + ", ".join(details) + ")"])
    return True, "\n".join(lines).strip()


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


def _new_point(
    label: str,
    nodes: list[dict[str, Any]],
    *,
    note: str = "",
    point_id: str = "",
    point_type: str = "",
    summary: str = "",
) -> dict[str, Any]:
    clean_label = str(label or point_id or "Point").strip()[:120] or "Point"
    clean_id = _unique_point_id(point_id or clean_label, nodes)
    clean_note = str(note or "").strip()[:5000]
    clean_summary = _clean_inline_text(summary, limit=500) or _derive_summary(clean_note, clean_label)
    idx = len(nodes) + 1
    angle = (idx * 2.3999632297) % (math.pi * 2.0)
    return {
        "id": clean_id,
        "label": clean_label,
        "title": clean_label,
        "type": _normalize_point_type(point_type, fallback="concept"),
        "summary": clean_summary,
        "note": clean_note,
        "file": _sanitize_markdown_file_name(clean_label, fallback=clean_id),
        "x": max(0.08, min(0.92, 0.5 + math.cos(angle) * 0.24)),
        "y": max(0.08, min(0.92, 0.5 + math.sin(angle) * 0.24)),
    }


def _resolve_or_create_point(nodes: list[dict[str, Any]], ref: str, *, create_missing: bool = True) -> dict[str, Any] | None:
    entry = _find_point(nodes, ref)
    if entry is None and not create_missing:
        entry = _find_point_for_query(nodes, ref)
    if entry is not None or not create_missing:
        return entry
    entry = _new_point(ref, nodes)
    nodes.append(entry)
    return entry


def _arrange_graph_wide_angles(graph: dict[str, Any]) -> dict[str, Any]:
    clean = _coerce_graph(graph)
    nodes = [dict(entry) for entry in clean.get("nodes", []) or [] if isinstance(entry, dict)]
    edges = [dict(edge) for edge in clean.get("edges", []) or [] if isinstance(edge, dict)]
    if len(nodes) < 2:
        clean["nodes"] = nodes
        clean["edges"] = edges
        return clean

    by_id = {str(entry.get("id", "") or ""): entry for entry in nodes if str(entry.get("id", "") or "")}
    adjacency: dict[str, set[str]] = {point_id: set() for point_id in by_id}
    for edge in edges:
        source_id = str(edge.get("source", "") or "")
        target_id = str(edge.get("target", "") or "")
        if source_id in adjacency and target_id in adjacency and source_id != target_id:
            adjacency[source_id].add(target_id)
            adjacency[target_id].add(source_id)

    def sort_key(point_id: str) -> tuple[str, str]:
        entry = by_id.get(point_id, {})
        label = str(entry.get("label", "") or point_id).strip().lower()
        return label, point_id.lower()

    components: list[list[str]] = []
    visited: set[str] = set()
    for point_id in sorted(by_id, key=sort_key):
        if point_id in visited:
            continue
        stack = [point_id]
        component: list[str] = []
        visited.add(point_id)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor_id in sorted(adjacency.get(current, set()), key=sort_key, reverse=True):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                stack.append(neighbor_id)
        components.append(sorted(component, key=sort_key))

    arm_length = max(DATA_NEXUS_LINK_MIN_GRID_DISTANCE, DATA_NEXUS_LINK_MAX_GRID_DISTANCE)
    placed_ids: set[str] = set()

    def clamp(value: float) -> float:
        return max(DATA_NEXUS_WORLD_MIN, min(DATA_NEXUS_WORLD_MAX, float(value)))

    def point_xy(point_id: str) -> tuple[float, float]:
        entry = by_id.get(point_id, {})
        return (
            float(entry.get("x", 0.5) or 0.5),
            float(entry.get("y", 0.5) or 0.5),
        )

    def placed_positions() -> list[tuple[float, float]]:
        return [point_xy(point_id) for point_id in placed_ids if point_id in by_id]

    def component_radius(component: list[str]) -> float:
        if len(component) <= 1:
            return 0.0
        return arm_length * max(1.0, math.sqrt(float(len(component))) * 0.55)

    def compact_component_center(component: list[str], ordinal: int) -> tuple[float, float]:
        positions = placed_positions()
        if not positions:
            return 0.5, 0.5
        cluster_x = sum(x for x, _y in positions) / float(len(positions))
        cluster_y = sum(y for _x, y in positions) / float(len(positions))
        anchors = sorted(
            positions,
            key=lambda pos: (
                math.hypot(pos[0] - cluster_x, pos[1] - cluster_y),
                pos[1],
                pos[0],
            ),
        )[: max(1, min(10, len(positions)))]
        angles = [
            0.0,
            math.pi,
            -math.pi / 2.0,
            math.pi / 2.0,
            math.pi / 4.0,
            -math.pi / 4.0,
            math.pi * 3.0 / 4.0,
            -math.pi * 3.0 / 4.0,
        ]
        required_gap = arm_length * 0.92 + component_radius(component)
        best: tuple[float, float] | None = None
        best_score = float("inf")
        for ring in range(1, max(6, len(positions) + 3)):
            distance = max(arm_length * 1.05, required_gap) * float(ring)
            for anchor_x, anchor_y in anchors:
                for offset in range(len(angles)):
                    angle = angles[(offset + ordinal) % len(angles)]
                    cx = clamp(anchor_x + math.cos(angle) * distance)
                    cy = clamp(anchor_y + math.sin(angle) * distance)
                    nearest = min(math.hypot(cx - px, cy - py) for px, py in positions)
                    shortfall = max(0.0, required_gap - nearest)
                    score = (shortfall * 1000.0) + math.hypot(cx - cluster_x, cy - cluster_y) + (ring * 0.02)
                    if score < best_score:
                        best_score = score
                        best = (cx, cy)
        return best if best is not None else (cluster_x, cluster_y)

    def arrange_component(component: list[str], center_x: float, center_y: float) -> None:
        if len(component) == 1:
            entry = by_id.get(component[0])
            if entry is not None:
                entry["x"] = clamp(center_x)
                entry["y"] = clamp(center_y)
                placed_ids.add(component[0])
            return

        root_id = max(component, key=lambda point_id: (len(adjacency.get(point_id, set())), sort_key(point_id)[0]))
        root = by_id.get(root_id)
        if root is None:
            return
        root["x"] = clamp(center_x)
        root["y"] = clamp(center_y)
        placed = {root_id}
        placed_ids.add(root_id)
        queue: list[tuple[str, str, float]] = [(root_id, "", -math.pi / 2.0)]

        while queue:
            point_id, parent_id, incoming_angle = queue.pop(0)
            entry = by_id.get(point_id)
            if entry is None:
                continue
            children = [
                neighbor_id
                for neighbor_id in sorted(adjacency.get(point_id, set()), key=sort_key)
                if neighbor_id not in placed
            ]
            if not children:
                continue

            if parent_id:
                step = (math.pi * 2.0) / float(len(children) + 1)
                first_angle = incoming_angle - (((len(children) - 1) * step) * 0.5)
                angles = [first_angle + idx * step for idx in range(len(children))]
            else:
                step = (math.pi * 2.0) / float(len(children))
                first_angle = -math.pi / 2.0 - (((len(children) - 1) * step) * 0.5)
                angles = [first_angle + idx * step for idx in range(len(children))]

            parent_x = float(entry.get("x", center_x) or center_x)
            parent_y = float(entry.get("y", center_y) or center_y)
            for child_id, angle in zip(children, angles):
                child = by_id.get(child_id)
                if child is None:
                    continue
                child["x"] = clamp(parent_x + math.cos(angle) * arm_length)
                child["y"] = clamp(parent_y + math.sin(angle) * arm_length)
                placed.add(child_id)
                placed_ids.add(child_id)
                queue.append((child_id, point_id, angle))

    components.sort(
        key=lambda component: (
            -max((len(adjacency.get(point_id, set())) for point_id in component), default=0),
            -len(component),
            sort_key(component[0]) if component else ("", ""),
        )
    )
    for comp_index, component in enumerate(components):
        center_x, center_y = compact_component_center(component, comp_index)
        arrange_component(component, center_x, center_y)

    clean["nodes"] = nodes
    clean["edges"] = edges
    return clean


def _prune_keep_filters(action: dict[str, Any]) -> tuple[set[str], set[str], list[str]]:
    keep_refs = {
        str(value or "").strip().lower()
        for value in _action_list(action, "keep", "keep_ids", "keep_labels", "protected", "protected_ids")
        if str(value or "").strip()
    }
    keep_ids = {_sanitize_folder_name(value, fallback="").lower() for value in keep_refs if value}
    keywords = [
        str(value or "").strip().lower()
        for value in _action_list(action, "keep_keywords", "keywords", "topic_terms", "terms")
        if str(value or "").strip()
    ]
    if not keywords:
        topic = _action_text(action, "topic", "query", "subject")
        keywords = [
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", topic)
            if len(token) > 2
        ]
    keywords = [
        token
        for token in keywords
        if token not in {"the", "and", "for", "that", "this", "with", "only", "keep", "point", "points"}
    ]
    return keep_refs, keep_ids, keywords


def _entry_matches_prune_keep(entry: dict[str, Any], keep_refs: set[str], keep_ids: set[str], keywords: list[str]) -> bool:
    point_id = str(entry.get("id", "") or "").strip()
    label = str(entry.get("label", "") or "").strip()
    content = f"{point_id} {label} {entry.get('note', '')}".lower()
    return (
        point_id.lower() in keep_ids
        or point_id.lower() in keep_refs
        or label.lower() in keep_refs
        or any(keyword in content for keyword in keywords)
    )


def _deletion_preview_entry(entry: dict[str, Any]) -> dict[str, str]:
    note = str(entry.get("note", "") or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    note = re.sub(r"\s+", " ", note).strip()
    return {
        "id": str(entry.get("id", "") or "").strip(),
        "label": str(entry.get("label", "") or entry.get("id", "") or "Point").strip(),
        "file": _point_file_name(entry),
        "note": note[:220],
    }


def preview_data_nexus_deletions_from_item(node_item, payload: Any) -> list[dict[str, str]]:
    actions = _coerce_update_actions(payload)
    if not actions:
        return []
    graph = _graph_with_point_files(_load_graph_for_node(node_item))
    nodes = [dict(entry) for entry in graph.get("nodes", []) or []]
    previews: list[dict[str, str]] = []
    seen_ids: set[str] = set()

    def _remember_deleted(entry: dict[str, Any]) -> None:
        point_id = str(entry.get("id", "") or "").strip()
        key = point_id.lower()
        if not key or key in seen_ids:
            return
        seen_ids.add(key)
        previews.append(_deletion_preview_entry(entry))

    for action in actions:
        op = _normalized_action_op(action)
        if op == "delete_all_points":
            for entry in nodes:
                _remember_deleted(entry)
            nodes = []
            continue

        if op == "delete_point":
            ref = _action_text(action, "id", "point_id", "label", "name")
            entry = _find_point(nodes, ref)
            if entry is None:
                entry = _find_point_for_query(nodes, ref)
            if entry is None:
                continue
            point_id = str(entry.get("id", "") or "")
            _remember_deleted(entry)
            nodes = [item for item in nodes if item is not entry and item.get("id") != point_id]
            continue

        if op == "prune_points":
            keep_refs, keep_ids, keywords = _prune_keep_filters(action)
            if not keep_refs and not keywords:
                continue
            remaining: list[dict[str, Any]] = []
            for entry in nodes:
                if _entry_matches_prune_keep(entry, keep_refs, keep_ids, keywords):
                    remaining.append(entry)
                    continue
                _remember_deleted(entry)
            nodes = remaining

    return previews


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
        op = _normalized_action_op(action)

        if op in {"upsert_point", "append_note"}:
            label = _action_text(action, "label", "name", "title")
            point_id = _action_text(action, "id", "point_id")
            ref = point_id or label
            if not ref:
                continue
            point_type = _action_text(action, "type", "point_type", "kind")
            summary = _action_text(action, "summary")
            note = _action_text(action, "note", "description", "comment", "text", "memory", "content")
            if not note and summary:
                # Backward compatibility: older planner payloads used `summary` as note text.
                note = summary
            entry = _find_point(nodes, point_id) if point_id else None
            entry = entry or _find_point(nodes, label)
            was_new = False
            if entry is None:
                entry = _new_point(
                    label or point_id,
                    nodes,
                    note=note,
                    point_id=point_id,
                    point_type=point_type,
                    summary=summary,
                )
                nodes.append(entry)
                was_new = True
                counts["added"] += 1
                changed = True
            if label and str(entry.get("label", "") or "") != label:
                entry["label"] = label[:120]
                entry["title"] = label[:120]
                entry["file"] = _sanitize_markdown_file_name(label, fallback=str(entry.get("id", "") or "point"))
                if not was_new:
                    counts["updated"] += 1
                changed = True
            if point_type:
                normalized_type = _normalize_point_type(point_type)
                if str(entry.get("type", "") or "") != normalized_type:
                    entry["type"] = normalized_type
                    if not was_new:
                        counts["updated"] += 1
                    changed = True
            if summary:
                clean_summary = _clean_inline_text(summary, limit=500)
                if str(entry.get("summary", "") or "") != clean_summary:
                    entry["summary"] = clean_summary
                    if not was_new:
                        counts["updated"] += 1
                    changed = True
            if note:
                mode = _action_text(action, "note_mode", "mode").lower()
                old_note = str(entry.get("note", "") or "")
                old_summary = str(entry.get("summary", "") or "")
                if mode == "replace":
                    new_note = note[:5000]
                else:
                    new_note = _append_note(old_note, note)
                if new_note != old_note:
                    entry["note"] = new_note
                    if not old_summary.strip() or _is_auto_summary(old_summary):
                        entry["summary"] = _derive_summary(
                            new_note,
                            str(entry.get("title", "") or entry.get("label", "") or ""),
                        )
                    if not was_new:
                        counts["updated"] += 1
                    changed = True
            continue

        if op == "delete_point":
            ref = _action_text(action, "id", "point_id", "label", "name")
            entry = _find_point(nodes, ref)
            if entry is None:
                entry = _find_point_for_query(nodes, ref)
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

        if op == "delete_all_points":
            if not nodes:
                continue
            for entry in nodes:
                _unlink_vault_note_for_entry(node_item, entry)
            counts["deleted"] += len(nodes)
            nodes = []
            edges = []
            changed = True
            continue

        if op == "prune_points":
            keep_refs, keep_ids, keywords = _prune_keep_filters(action)
            if not keep_refs and not keywords:
                continue

            remaining: list[dict[str, Any]] = []
            deleted_ids: set[str] = set()
            for entry in nodes:
                point_id = str(entry.get("id", "") or "").strip()
                if _entry_matches_prune_keep(entry, keep_refs, keep_ids, keywords):
                    remaining.append(entry)
                    continue
                _unlink_vault_note_for_entry(node_item, entry)
                deleted_ids.add(point_id)
            if deleted_ids:
                nodes = remaining
                edges = [
                    edge
                    for edge in edges
                    if edge.get("source") not in deleted_ids and edge.get("target") not in deleted_ids
                ]
                counts["deleted"] += len(deleted_ids)
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


def data_nexus_point_bundle_from_item(node_item) -> dict[str, Any]:
    graph = _graph_with_point_files(_load_graph_for_node(node_item))
    model = getattr(node_item, "model", None)
    vault_path = _param_value_from_model(model, DATA_NEXUS_VAULT_PARAM, "")
    nodes = [dict(entry) for entry in graph.get("nodes", []) or [] if isinstance(entry, dict)]
    edges = [dict(edge) for edge in graph.get("edges", []) or [] if isinstance(edge, dict)]
    links_by_point: dict[str, list[dict[str, str]]] = {}
    for edge in edges:
        source = str(edge.get("source", "") or "").strip()
        target = str(edge.get("target", "") or "").strip()
        label = str(edge.get("label", "") or "").strip()
        if source:
            links_by_point.setdefault(source, []).append(
                {"direction": "out", "target": target, "label": label}
            )
        if target:
            links_by_point.setdefault(target, []).append(
                {"direction": "in", "source": source, "label": label}
            )
    points = []
    for entry in nodes:
        title = str(entry.get("title", "") or entry.get("label", "") or entry.get("id", "") or "Point").strip()
        content = str(entry.get("note", "") or "").strip()
        points.append(
            {
                "id": str(entry.get("id", "") or "").strip(),
                "type": _normalize_point_type(entry.get("type", "concept")),
                "title": title,
                "summary": str(entry.get("summary", "") or "").strip() or _derive_summary(content, title),
                "content": content,
                "file": _point_file_name(entry),
                "links": links_by_point.get(str(entry.get("id", "") or "").strip(), []),
            }
        )
    return {
        "version": 2,
        "vault": vault_path,
        "points": points,
        "edges": edges,
    }


def normalized_data_nexus_points_from_item(node_item) -> dict[str, Any]:
    return data_nexus_point_bundle_from_item(node_item)


class DataNexusCanvas(QtWidgets.QWidget):
    graphChanged = QtCore.Signal()
    selectionChanged = QtCore.Signal(str)
    linkTargetChosen = QtCore.Signal(str)
    zoomChanged = QtCore.Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._graph = _default_graph()
        self._selected_id = ""
        self._dragging = False
        self._drag_moved = False
        self._panning = False
        self._pan_button = ""
        self._link_target_mode = False
        self._hover_id = ""
        self._drag_offset = QtCore.QPointF(0.0, 0.0)
        self._pan_start = QtCore.QPointF(0.0, 0.0)
        self._pan_start_center = QtCore.QPointF(0.5, 0.5)
        self._zoom = 1.0
        self._view_center = QtCore.QPointF(0.5, 0.5)
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
            self._panning = False
            self._pan_button = ""
        if self._hover_id not in ids:
            self._hover_id = ""
        self.update()
        self.selectionChanged.emit(self._selected_id)

    def replace_graph(self, graph: dict[str, Any]) -> None:
        self._graph = _coerce_graph(graph)
        ids = {entry.get("id") for entry in self._graph.get("nodes", [])}
        if self._selected_id not in ids:
            self._selected_id = ""
            self._dragging = False
            self._drag_moved = False
            self._panning = False
            self._pan_button = ""
        if self._hover_id not in ids:
            self._hover_id = ""
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
            self._panning = False
            self._pan_button = ""
            self._hover_id = ""
        self.update()
        self.selectionChanged.emit(self._selected_id)

    def _plot_rect(self) -> QtCore.QRectF:
        return QtCore.QRectF(14.0, 14.0, max(20.0, self.width() - 28.0), max(20.0, self.height() - 28.0))

    def _view_span(self, zoom: float | None = None) -> float:
        raw_zoom = self._zoom if zoom is None else zoom
        return 1.0 / max(DATA_NEXUS_MIN_ZOOM, min(DATA_NEXUS_MAX_ZOOM, float(raw_zoom or 1.0)))

    def _view_scale(self, zoom: float | None = None) -> float:
        raw_zoom = self._zoom if zoom is None else zoom
        clamped_zoom = max(DATA_NEXUS_MIN_ZOOM, min(DATA_NEXUS_MAX_ZOOM, float(raw_zoom or 1.0)))
        return max(1.0, DATA_NEXUS_GRID_BASE_PX * clamped_zoom)

    def _view_metrics(
        self,
        *,
        zoom: float | None = None,
        center: QtCore.QPointF | None = None,
    ) -> tuple[QtCore.QRectF, float, float, float, float, float]:
        rect = self._plot_rect()
        scale = self._view_scale(zoom)
        world_w = max(0.0001, float(rect.width()) / scale)
        world_h = max(0.0001, float(rect.height()) / scale)
        view_center = center if center is not None else self._view_center
        left = float(view_center.x()) - (world_w * 0.5)
        top = float(view_center.y()) - (world_h * 0.5)
        return rect, scale, left, top, world_w, world_h

    def _clamp_view_center(self) -> None:
        _rect, _scale, _left, _top, world_w, world_h = self._view_metrics()
        world_span = DATA_NEXUS_WORLD_MAX - DATA_NEXUS_WORLD_MIN
        world_mid = DATA_NEXUS_WORLD_MIN + (world_span * 0.5)

        def clamp_axis(value: float, visible_span: float) -> float:
            if visible_span >= world_span:
                return world_mid
            half = visible_span * 0.5
            return max(DATA_NEXUS_WORLD_MIN + half, min(DATA_NEXUS_WORLD_MAX - half, value))

        x = clamp_axis(float(self._view_center.x()), world_w)
        y = clamp_axis(float(self._view_center.y()), world_h)
        self._view_center = QtCore.QPointF(x, y)

    def zoom(self) -> float:
        return float(self._zoom or 1.0)

    def set_zoom(self, zoom: float, anchor_screen: QtCore.QPointF | None = None) -> None:
        old_zoom = float(self._zoom or 1.0)
        next_zoom = max(DATA_NEXUS_MIN_ZOOM, min(DATA_NEXUS_MAX_ZOOM, float(zoom or 1.0)))
        if abs(next_zoom - old_zoom) < 0.001:
            return
        anchor_world = None
        if anchor_screen is not None:
            anchor_world = self._from_screen_unclamped(anchor_screen)
        self._zoom = next_zoom
        if anchor_screen is not None and anchor_world is not None:
            rect, scale, _left, _top, _world_w, _world_h = self._view_metrics()
            rect_center = rect.center()
            self._view_center = QtCore.QPointF(
                float(anchor_world[0]) - ((float(anchor_screen.x()) - float(rect_center.x())) / scale),
                float(anchor_world[1]) - ((float(anchor_screen.y()) - float(rect_center.y())) / scale),
            )
        self._clamp_view_center()
        self.update()
        self.zoomChanged.emit(self._zoom)

    def zoom_by(self, factor: float, anchor_screen: QtCore.QPointF | None = None) -> None:
        self.set_zoom(float(self._zoom or 1.0) * float(factor or 1.0), anchor_screen=anchor_screen)

    def reset_zoom(self) -> None:
        self._view_center = QtCore.QPointF(0.5, 0.5)
        if abs(float(self._zoom or 1.0) - 1.0) < 0.001:
            self.update()
            self.zoomChanged.emit(self._zoom)
            return
        self.set_zoom(1.0)

    def _to_screen(self, x: float, y: float) -> QtCore.QPointF:
        rect, scale, left, top, _world_w, _world_h = self._view_metrics()
        return QtCore.QPointF(
            rect.left() + ((float(x) - left) * scale),
            rect.top() + ((float(y) - top) * scale),
        )

    def _from_screen_unclamped(self, point: QtCore.QPointF) -> tuple[float, float]:
        rect, scale, left, top, _world_w, _world_h = self._view_metrics()
        x = left + ((float(point.x()) - rect.left()) / scale)
        y = top + ((float(point.y()) - rect.top()) / scale)
        return x, y

    def _from_screen(self, point: QtCore.QPointF) -> tuple[float, float]:
        x, y = self._from_screen_unclamped(point)
        return (
            max(DATA_NEXUS_WORLD_MIN, min(DATA_NEXUS_WORLD_MAX, x)),
            max(DATA_NEXUS_WORLD_MIN, min(DATA_NEXUS_WORLD_MAX, y)),
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._clamp_view_center()

    def _hit_node(self, point: QtCore.QPointF) -> str:
        best = ""
        best_dist = 999999.0
        hit_radius = max(9.0, self._point_radius(selected=False) + 7.0)
        for entry in self._graph.get("nodes", []) or []:
            center = self._to_screen(entry.get("x", 0.5), entry.get("y", 0.5))
            dx = center.x() - point.x()
            dy = center.y() - point.y()
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < best_dist and dist <= hit_radius:
                best = str(entry.get("id", "") or "")
                best_dist = dist
        return best

    def _set_hover_id(self, point_id: str) -> None:
        next_id = str(point_id or "")
        if next_id == self._hover_id:
            return
        self._hover_id = next_id
        self.update()

    def _node_by_id(self, point_id: str) -> dict[str, Any] | None:
        for entry in self._graph.get("nodes", []) or []:
            if entry.get("id") == point_id:
                return entry
        return None

    def _repulsion_distance(self) -> float:
        nodes = self._graph.get("nodes", []) or []
        base = DATA_NEXUS_REPULSION_GRID_DISTANCE
        if len(nodes) > 8:
            base *= max(0.55, min(1.0, math.sqrt(8.0 / float(len(nodes)))))
        return max(0.12, min(0.5, base))

    @staticmethod
    def _clamp_world_coord(value: float) -> float:
        return max(DATA_NEXUS_WORLD_MIN, min(DATA_NEXUS_WORLD_MAX, float(value)))

    @staticmethod
    def _entry_coord(entry: dict[str, Any], key: str, default: float = 0.5) -> float:
        try:
            value = float(entry.get(key, default))
        except Exception:
            value = default
        if not math.isfinite(value):
            value = default
        return DataNexusCanvas._clamp_world_coord(value)

    def _apply_link_elasticity(self, anchor_id: str = "", *, passes: int = 4, strength: float = 0.55) -> None:
        nodes = [entry for entry in self._graph.get("nodes", []) or [] if isinstance(entry, dict)]
        edges = [edge for edge in self._graph.get("edges", []) or [] if isinstance(edge, dict)]
        if len(nodes) < 2 or not edges:
            return
        by_id = {str(entry.get("id", "") or ""): entry for entry in nodes}
        anchor = str(anchor_id or "")
        min_dist = DATA_NEXUS_LINK_MIN_GRID_DISTANCE
        max_dist = max(min_dist + 0.01, DATA_NEXUS_LINK_MAX_GRID_DISTANCE)
        stiffness = max(0.05, min(1.0, float(strength)))
        changed = False

        for _pass in range(max(1, int(passes))):
            for edge in edges:
                source_id = str(edge.get("source", "") or "")
                target_id = str(edge.get("target", "") or "")
                a = by_id.get(source_id)
                b = by_id.get(target_id)
                if a is None or b is None or a is b:
                    continue

                ax = self._entry_coord(a, "x")
                ay = self._entry_coord(a, "y")
                bx = self._entry_coord(b, "x")
                by = self._entry_coord(b, "y")
                dx = bx - ax
                dy = by - ay
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < 0.0001:
                    angle = (len(source_id) * 1.6180339887 + len(target_id)) % (math.pi * 2.0)
                    dx = math.cos(angle) * 0.0001
                    dy = math.sin(angle) * 0.0001
                    dist = 0.0001

                if dist > max_dist:
                    correction = (dist - max_dist) * stiffness
                    direction_a = 1.0
                    direction_b = -1.0
                elif dist < min_dist:
                    correction = (min_dist - dist) * stiffness
                    direction_a = -1.0
                    direction_b = 1.0
                else:
                    continue

                nx = dx / dist
                ny = dy / dist
                move_a = 0.5
                move_b = 0.5
                if source_id == anchor:
                    move_a = 0.0
                    move_b = 1.0
                elif target_id == anchor:
                    move_a = 1.0
                    move_b = 0.0

                if move_a:
                    next_ax = self._clamp_world_coord(ax + nx * correction * move_a * direction_a)
                    next_ay = self._clamp_world_coord(ay + ny * correction * move_a * direction_a)
                    if abs(next_ax - ax) > 0.0001 or abs(next_ay - ay) > 0.0001:
                        a["x"] = next_ax
                        a["y"] = next_ay
                        changed = True
                if move_b:
                    next_bx = self._clamp_world_coord(bx + nx * correction * move_b * direction_b)
                    next_by = self._clamp_world_coord(by + ny * correction * move_b * direction_b)
                    if abs(next_bx - bx) > 0.0001 or abs(next_by - by) > 0.0001:
                        b["x"] = next_bx
                        b["y"] = next_by
                        changed = True

        if changed:
            self._graph["nodes"] = nodes

    def _apply_repulsion(self, anchor_id: str = "", *, passes: int = 2, strength: float = 0.65) -> None:
        nodes = [entry for entry in self._graph.get("nodes", []) or [] if isinstance(entry, dict)]
        if len(nodes) < 2:
            return
        min_dist = self._repulsion_distance()
        anchor = str(anchor_id or "")
        changed = False
        for _pass in range(max(1, int(passes))):
            for i in range(len(nodes)):
                a = nodes[i]
                aid = str(a.get("id", "") or "")
                ax = self._entry_coord(a, "x")
                ay = self._entry_coord(a, "y")
                for j in range(i + 1, len(nodes)):
                    b = nodes[j]
                    bid = str(b.get("id", "") or "")
                    bx = self._entry_coord(b, "x")
                    by = self._entry_coord(b, "y")
                    dx = bx - ax
                    dy = by - ay
                    dist = math.sqrt(dx * dx + dy * dy)
                    if dist >= min_dist:
                        continue
                    if dist < 0.0001:
                        angle = ((i + 1) * 2.3999632297 + (j + 1)) % (math.pi * 2.0)
                        dx = math.cos(angle) * 0.0001
                        dy = math.sin(angle) * 0.0001
                        dist = 0.0001
                    nx = dx / dist
                    ny = dy / dist
                    push = (min_dist - dist) * max(0.05, min(1.0, float(strength)))
                    move_a = 0.5
                    move_b = 0.5
                    if aid == anchor:
                        move_a = 0.0
                        move_b = 1.0
                    elif bid == anchor:
                        move_a = 1.0
                        move_b = 0.0
                    if move_a:
                        next_ax = max(DATA_NEXUS_WORLD_MIN, min(DATA_NEXUS_WORLD_MAX, ax - nx * push * move_a))
                        next_ay = max(DATA_NEXUS_WORLD_MIN, min(DATA_NEXUS_WORLD_MAX, ay - ny * push * move_a))
                        if abs(next_ax - ax) > 0.0001 or abs(next_ay - ay) > 0.0001:
                            a["x"] = next_ax
                            a["y"] = next_ay
                            ax = next_ax
                            ay = next_ay
                            changed = True
                    if move_b:
                        next_bx = max(DATA_NEXUS_WORLD_MIN, min(DATA_NEXUS_WORLD_MAX, bx + nx * push * move_b))
                        next_by = max(DATA_NEXUS_WORLD_MIN, min(DATA_NEXUS_WORLD_MAX, by + ny * push * move_b))
                        if abs(next_bx - bx) > 0.0001 or abs(next_by - by) > 0.0001:
                            b["x"] = next_bx
                            b["y"] = next_by
                            changed = True
        if changed:
            self._graph["nodes"] = nodes

    def _point_radius(self, *, selected: bool) -> float:
        base = 8.5 if selected else 7.5
        return max(1.6, min(32.0, base * max(DATA_NEXUS_MIN_ZOOM, min(DATA_NEXUS_MAX_ZOOM, float(self._zoom or 1.0)))))

    def _content_pen_width(self, base: float) -> float:
        return max(0.35, min(6.0, float(base) * max(DATA_NEXUS_MIN_ZOOM, min(DATA_NEXUS_MAX_ZOOM, float(self._zoom or 1.0)))))

    def _label_font_size(self, base_size: float) -> float:
        return max(5.0, min(22.0, float(base_size) * max(0.45, min(2.6, float(self._zoom or 1.0)))))

    def _should_draw_point_labels(self) -> bool:
        return float(self._zoom or 1.0) >= DATA_NEXUS_LABEL_MIN_ZOOM

    def _world_rect_screen(self) -> QtCore.QRectF:
        top_left = self._to_screen(DATA_NEXUS_WORLD_MIN, DATA_NEXUS_WORLD_MIN)
        bottom_right = self._to_screen(DATA_NEXUS_WORLD_MAX, DATA_NEXUS_WORLD_MAX)
        return QtCore.QRectF(top_left, bottom_right).normalized()

    def _draw_world_grid(self, painter: QtGui.QPainter, plot: QtCore.QRectF) -> None:
        visible_left, visible_top = self._from_screen_unclamped(plot.topLeft())
        visible_right, visible_bottom = self._from_screen_unclamped(plot.bottomRight())
        left = min(visible_left, visible_right)
        right = max(visible_left, visible_right)
        top = min(visible_top, visible_bottom)
        bottom = max(visible_top, visible_bottom)

        if self._zoom < 0.2:
            step = 0.5
        elif self._zoom < 2.0:
            step = 0.25
        elif self._zoom < 3.0:
            step = 0.125
        else:
            step = 0.0625

        start_x = math.floor(left / step) * step
        end_x = math.ceil(right / step) * step
        start_y = math.floor(top / step) * step
        end_y = math.ceil(bottom / step) * step
        count_x = int(round((end_x - start_x) / step)) + 1
        count_y = int(round((end_y - start_y) / step)) + 1

        for idx in range(max(0, min(count_x, 500))):
            value = start_x + (idx * step)
            major = abs(value - round(value)) < 0.001
            axis = abs(value) < 0.001
            color = QtGui.QColor("#3b475c" if axis else ("#263244" if major else "#1a2432"))
            width = 1.5 if axis else (1.05 if major else 0.7)
            painter.setPen(QtGui.QPen(color, width))
            painter.drawLine(self._to_screen(value, top), self._to_screen(value, bottom))

        for idx in range(max(0, min(count_y, 500))):
            value = start_y + (idx * step)
            major = abs(value - round(value)) < 0.001
            axis = abs(value) < 0.001
            color = QtGui.QColor("#3b475c" if axis else ("#263244" if major else "#1a2432"))
            width = 1.5 if axis else (1.05 if major else 0.7)
            painter.setPen(QtGui.QPen(color, width))
            painter.drawLine(self._to_screen(left, value), self._to_screen(right, value))

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

    @staticmethod
    def _is_middle_button(event) -> bool:
        try:
            button = event.button()
        except Exception:
            return False
        try:
            middle_button = QtCore.Qt.MouseButton.MiddleButton
        except Exception:
            middle_button = QtCore.Qt.MiddleButton
        return button == middle_button

    def _begin_pan(self, point: QtCore.QPointF, button_name: str) -> None:
        self._panning = True
        self._pan_button = str(button_name or "")
        self._dragging = False
        self._drag_moved = False
        self._set_hover_id("")
        self._pan_start = QtCore.QPointF(point)
        self._pan_start_center = QtCore.QPointF(
            float(self._view_center.x()),
            float(self._view_center.y()),
        )
        try:
            cursor_group = getattr(QtCore.Qt, "CursorShape", QtCore.Qt)
            self.setCursor(getattr(cursor_group, "SizeAllCursor"))
        except Exception:
            pass

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QtGui.QColor("#0b1018"))
        plot = self._plot_rect()
        painter.setPen(QtGui.QPen(QtGui.QColor("#1f2937"), 1.0))
        painter.setBrush(QtGui.QColor("#0f1621"))
        painter.drawRoundedRect(plot, 6.0, 6.0)

        painter.save()
        painter.setClipRect(plot.adjusted(1.0, 1.0, -1.0, -1.0))
        self._draw_world_grid(painter, plot)
        nodes = {entry.get("id"): entry for entry in self._graph.get("nodes", []) or []}
        for edge in self._graph.get("edges", []) or []:
            src = nodes.get(edge.get("source"))
            dst = nodes.get(edge.get("target"))
            if not src or not dst:
                continue
            a = self._to_screen(src.get("x", 0.5), src.get("y", 0.5))
            b = self._to_screen(dst.get("x", 0.5), dst.get("y", 0.5))
            painter.setPen(QtGui.QPen(QtGui.QColor("#3b82f6"), self._content_pen_width(1.8)))
            painter.drawLine(a, b)

        draw_labels = self._should_draw_point_labels()
        if draw_labels:
            font = painter.font()
            try:
                font.setPointSizeF(self._label_font_size(max(7.0, float(font.pointSizeF() or font.pointSize()))))
            except Exception:
                font.setPointSize(max(6, int(round(self._label_font_size(max(7, font.pointSize()))))))
            painter.setFont(font)
        for entry in self._graph.get("nodes", []) or []:
            point_id = str(entry.get("id", "") or "")
            center = self._to_screen(entry.get("x", 0.5), entry.get("y", 0.5))
            selected = point_id == self._selected_id
            hovered = point_id == self._hover_id
            radius = self._point_radius(selected=selected)
            glow_radius = radius * (2.8 if selected or hovered else 2.25)
            glow = QtGui.QRadialGradient(center, glow_radius)
            outer = QtGui.QColor("#67e8f9" if selected or hovered else "#60a5fa")
            outer.setAlpha(0)
            mid = QtGui.QColor("#67e8f9" if selected or hovered else "#93c5fd")
            mid.setAlpha(112 if selected or hovered else 76)
            glow.setColorAt(0.0, QtGui.QColor("#ffffff"))
            glow.setColorAt(0.2, QtGui.QColor("#cffafe" if selected or hovered else "#dbeafe"))
            glow.setColorAt(0.58, mid)
            glow.setColorAt(1.0, outer)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QBrush(glow))
            painter.drawEllipse(center, glow_radius, glow_radius)

            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#ffffff"))
            core_radius = max(1.2, radius * 0.34)
            painter.drawEllipse(center, core_radius, core_radius)
            if not draw_labels and not hovered:
                continue
            label = str(entry.get("label", "") or point_id).strip()
            label_rect = QtCore.QRectF(
                center.x() + (radius + 4.0),
                center.y() - max(11.0, radius * 0.85),
                max(120.0, 160.0 * max(0.75, min(1.8, self._zoom))),
                max(18.0, 22.0 * max(0.75, min(1.6, self._zoom))),
            )
            painter.setPen(QtGui.QPen(QtGui.QColor("#dbeafe" if selected else "#cbd5e1"), 1.0))
            painter.drawText(label_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, label)
        painter.restore()

    def mousePressEvent(self, event):
        if self._is_middle_button(event):
            self._begin_pan(self._event_posf(event), "middle")
            event.accept()
            return
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
            self._begin_pan(point, "left")
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        point = self._event_posf(event)
        if self._panning:
            self._set_hover_id("")
            delta = point - self._pan_start
            _rect, scale, _left, _top, _world_w, _world_h = self._view_metrics()
            self._view_center = QtCore.QPointF(
                float(self._pan_start_center.x()) - (float(delta.x()) / scale),
                float(self._pan_start_center.y()) - (float(delta.y()) / scale),
            )
            self._clamp_view_center()
            self.update()
            event.accept()
            return
        if self._dragging and self._selected_id:
            node = self._node_by_id(self._selected_id)
            if node is not None:
                self._set_hover_id(self._selected_id)
                drag_point = point - self._drag_offset
                x, y = self._from_screen(drag_point)
                node["x"] = x
                node["y"] = y
                self._apply_repulsion(self._selected_id, passes=1, strength=0.55)
                self._apply_link_elasticity(self._selected_id, passes=10, strength=0.85)
                self._drag_moved = True
                self.graphChanged.emit()
                self.update()
                event.accept()
                return
            self._dragging = False
            self._drag_moved = False
        self._set_hover_id(self._hit_node(point))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._set_hover_id("")
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning and (
            (self._pan_button == "middle" and self._is_middle_button(event))
            or (self._pan_button == "left" and self._is_left_button(event))
        ):
            self._panning = False
            self._pan_button = ""
            self.set_link_target_mode(self._link_target_mode)
            self.update()
            event.accept()
            return
        if self._is_left_button(event) and self._dragging:
            self._dragging = False
            if self._drag_moved:
                self._apply_repulsion(self._selected_id, passes=2, strength=0.45)
                self._apply_link_elasticity(self._selected_id, passes=16, strength=0.75)
                self.graphChanged.emit()
                self.update()
            self._drag_moved = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        delta = 0
        try:
            delta = int(event.angleDelta().y())
        except Exception:
            delta = 0
        if not delta:
            super().wheelEvent(event)
            return
        factor = 1.15 if delta > 0 else 1.0 / 1.15
        self.zoom_by(factor, anchor_screen=self._event_posf(event))
        event.accept()


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
        self._syncing_point_list = False
        self.setMinimumSize(DATA_NEXUS_BODY_W, DATA_NEXUS_BODY_H)
        self.setStyleSheet(
            "QWidget{color:#e5e7eb;}"
            "QLineEdit,QPlainTextEdit{background:#0f1216;color:#e5e7eb;border:1px solid #334155;border-radius:4px;padding:4px;}"
            "QListWidget{background:#0b1018;color:#e5e7eb;border:1px solid #334155;border-radius:4px;outline:0;}"
            "QListWidget::item{padding:1px 5px;border-bottom:1px solid #111827;}"
            "QListWidget::item:selected{background:#164e63;color:#f8fafc;}"
            "QListWidget::item:hover{background:#1f2937;}"
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
        self._layout_btn = self._make_tool_button("Arrange linked points", "SP_BrowserReload")
        self._zoom_out_btn = self._make_text_tool_button("-", "Zoom out")
        self._zoom_reset_btn = self._make_text_tool_button("1:1", "Reset zoom")
        self._zoom_in_btn = self._make_text_tool_button("+", "Zoom in")
        self._save_btn = self._make_tool_button("Save nexus sidecar", "SP_DialogSaveButton")
        self._open_vault_btn = self._make_tool_button("Open/Switch Data Nexus vault", "SP_DialogOpenButton")
        self._merge_vault_btn = self._make_tool_button("Merge another Data Nexus vault into this one", "SP_FileDialogNewFolder")
        self._reload_btn = self._make_tool_button("Reload graph from vault files", "SP_DialogResetButton")
        self._open_btn = self._make_tool_button("Open Data Nexus folder", "SP_DirOpenIcon")
        for btn in (
            self._add_btn,
            self._link_btn,
            self._delete_btn,
            self._layout_btn,
            self._zoom_out_btn,
            self._zoom_reset_btn,
            self._zoom_in_btn,
            self._save_btn,
            self._open_vault_btn,
            self._merge_vault_btn,
            self._reload_btn,
            self._open_btn,
        ):
            toolbar.addWidget(btn, 0)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        body_row = QtWidgets.QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(8)

        point_panel = QtWidgets.QWidget()
        point_panel.setFixedWidth(190)
        point_panel_layout = QtWidgets.QVBoxLayout(point_panel)
        point_panel_layout.setContentsMargins(0, 0, 0, 0)
        point_panel_layout.setSpacing(5)
        point_header = QtWidgets.QHBoxLayout()
        point_header.setContentsMargins(0, 0, 0, 0)
        point_header.setSpacing(4)
        point_title = QtWidgets.QLabel("Vault Points")
        point_title.setStyleSheet("QLabel{color:#e5e7eb;font-weight:600;}")
        self._point_count = QtWidgets.QLabel("0")
        self._point_count.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._point_count.setStyleSheet("QLabel{color:#94a3b8;}")
        point_header.addWidget(point_title, 1)
        point_header.addWidget(self._point_count, 0)
        self._point_filter = QtWidgets.QLineEdit()
        self._point_filter.setPlaceholderText("Filter points")
        self._point_list = QtWidgets.QListWidget()
        self._point_list.setUniformItemSizes(True)
        self._point_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._point_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        try:
            self._point_list.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        except Exception:
            self._point_list.setTextElideMode(QtCore.Qt.ElideRight)
        point_panel_layout.addLayout(point_header)
        point_panel_layout.addWidget(self._point_filter)
        point_panel_layout.addWidget(self._point_list, 1)
        body_row.addWidget(point_panel, 0)

        self._canvas = DataNexusCanvas()
        body_row.addWidget(self._canvas, 1)

        inspector = QtWidgets.QWidget()
        inspector.setFixedWidth(180)
        inspector.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Expanding)
        inspector_layout = QtWidgets.QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(5)
        self._label_edit = QtWidgets.QLineEdit()
        self._label_edit.setPlaceholderText("Point label")
        self._note_edit = QtWidgets.QPlainTextEdit()
        self._note_edit.setPlaceholderText("Point note")
        self._note_edit.setMinimumHeight(120)
        self._note_edit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("QLabel{color:#94a3b8;}")
        inspector_layout.addWidget(QtWidgets.QLabel("Point"))
        inspector_layout.addWidget(self._label_edit)
        inspector_layout.addWidget(self._status)
        inspector_layout.addWidget(self._note_edit, 1)
        body_row.addWidget(inspector, 0)
        layout.addLayout(body_row, 1)

        self._canvas.set_graph(_load_graph_for_node(node_item))
        self._canvas.graphChanged.connect(self._on_graph_changed)
        self._canvas.selectionChanged.connect(self._on_selection_changed)
        self._canvas.linkTargetChosen.connect(self._on_link_target_chosen)
        self._canvas.zoomChanged.connect(self._on_zoom_changed)
        self._point_filter.textChanged.connect(self._on_point_filter_changed)
        self._point_list.currentItemChanged.connect(self._on_point_list_current_item_changed)
        self._label_edit.textEdited.connect(self._on_label_edited)
        self._note_edit.textChanged.connect(self._on_note_edited)
        self._add_btn.clicked.connect(self._add_point)
        self._link_btn.clicked.connect(self._begin_link)
        self._delete_btn.clicked.connect(self._delete_selected)
        self._layout_btn.clicked.connect(self._arrange_points)
        self._zoom_out_btn.clicked.connect(self._zoom_out)
        self._zoom_reset_btn.clicked.connect(self._zoom_reset)
        self._zoom_in_btn.clicked.connect(self._zoom_in)
        self._save_btn.clicked.connect(self._save_now)
        self._open_vault_btn.clicked.connect(self._open_vault_folder)
        self._merge_vault_btn.clicked.connect(self._merge_vault_folder)
        self._reload_btn.clicked.connect(self._reload_from_vault)
        self._open_btn.clicked.connect(self._open_folder)
        self._sync_storage(write_sidecar=True)
        self._refresh_point_list()
        self._on_selection_changed(self._canvas.selected_id())
        self._on_zoom_changed(self._canvas.zoom())

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

    def _make_text_tool_button(self, text: str, tooltip: str) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton()
        btn.setAutoRaise(False)
        btn.setToolTip(tooltip)
        btn.setText(text)
        btn.setFixedSize(34 if len(text) > 1 else 28, 26)
        return btn

    @staticmethod
    def _item_user_role():
        try:
            return QtCore.Qt.ItemDataRole.UserRole
        except Exception:
            return QtCore.Qt.UserRole

    def _point_filter_text(self) -> str:
        return str(self._point_filter.text() or "").strip().lower()

    def _point_matches_filter(self, entry: dict[str, Any], needle: str) -> bool:
        if not needle:
            return True
        haystack = " ".join(
            [
                str(entry.get("id", "") or ""),
                str(entry.get("label", "") or ""),
                str(entry.get("title", "") or ""),
                str(entry.get("type", "") or ""),
                str(entry.get("summary", "") or ""),
                str(entry.get("note", "") or ""),
                str(entry.get("file", "") or ""),
            ]
        ).lower()
        return needle in haystack

    def _point_row_text(self, entry: dict[str, Any]) -> str:
        label = str(entry.get("title", "") or entry.get("label", "") or entry.get("id", "") or "Point").strip()
        return label

    def _point_row_tooltip(self, entry: dict[str, Any]) -> str:
        label = str(entry.get("title", "") or entry.get("label", "") or entry.get("id", "") or "Point").strip()
        file_name = _point_file_name(entry)
        point_type = str(entry.get("type", "") or "").strip()
        summary = str(entry.get("summary", "") or "").strip()
        note = re.sub(r"\s+", " ", str(entry.get("note", "") or "")).strip()
        parts = [label, file_name]
        if point_type:
            parts.append(f"type: {point_type}")
        if summary:
            parts.append(f"summary: {summary}")
        if note:
            parts.append(note[:260])
        return "\n".join(parts)

    def _refresh_point_list(self) -> None:
        graph = self._canvas.graph()
        nodes = [
            entry
            for entry in graph.get("nodes", []) or []
            if isinstance(entry, dict)
        ]
        needle = self._point_filter_text()
        visible = [entry for entry in nodes if self._point_matches_filter(entry, needle)]
        selected = self._canvas.selected_id()
        role = self._item_user_role()
        self._syncing_point_list = True
        try:
            self._point_list.clear()
            for entry in sorted(visible, key=lambda item: str(item.get("label", "") or item.get("id", "")).lower()):
                point_id = str(entry.get("id", "") or "")
                item = QtWidgets.QListWidgetItem(self._point_row_text(entry))
                item.setData(role, point_id)
                item.setToolTip(self._point_row_tooltip(entry))
                item.setSizeHint(QtCore.QSize(160, 24))
                self._point_list.addItem(item)
                if point_id == selected:
                    self._point_list.setCurrentItem(item)
            count_text = f"{len(visible)}/{len(nodes)}" if needle else str(len(nodes))
            self._point_count.setText(count_text)
        finally:
            self._syncing_point_list = False

    def _sync_point_list_selection(self, point_id: str) -> None:
        role = self._item_user_role()
        self._syncing_point_list = True
        try:
            if not point_id:
                self._point_list.setCurrentRow(-1)
                return
            for row in range(self._point_list.count()):
                item = self._point_list.item(row)
                if str(item.data(role) or "") == point_id:
                    self._point_list.setCurrentItem(item)
                    self._point_list.scrollToItem(item, QtWidgets.QAbstractItemView.PositionAtCenter)
                    return
            self._point_list.setCurrentRow(-1)
        finally:
            self._syncing_point_list = False

    def _on_point_filter_changed(self, _text: str) -> None:
        self._refresh_point_list()

    def _on_point_list_current_item_changed(self, current, _previous) -> None:
        if self._syncing_point_list or current is None:
            return
        point_id = str(current.data(self._item_user_role()) or "")
        if point_id:
            self._canvas.select_id(point_id)

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
        self._refresh_point_list()
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
        self._sync_point_list_selection(point_id)
        self._label_edit.blockSignals(True)
        self._note_edit.blockSignals(True)
        try:
            self._label_edit.setText(str((node.get("title", "") or node.get("label", "")) if node else ""))
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

    def _on_zoom_changed(self, zoom: float) -> None:
        self._zoom_out_btn.setEnabled(float(zoom or 1.0) > DATA_NEXUS_MIN_ZOOM + 0.001)
        self._zoom_in_btn.setEnabled(float(zoom or 1.0) < DATA_NEXUS_MAX_ZOOM - 0.001)
        self._set_status(f"Zoom {int(round(float(zoom or 1.0) * 100.0))}%")

    def _zoom_in(self) -> None:
        self._canvas.zoom_by(1.18)

    def _zoom_out(self) -> None:
        self._canvas.zoom_by(1.0 / 1.18)

    def _zoom_reset(self) -> None:
        self._canvas.reset_zoom()

    def _on_label_edited(self, text: str) -> None:
        selected = self._canvas.selected_id()
        graph = self._canvas.graph()
        for entry in graph.get("nodes", []) or []:
            if entry.get("id") == selected:
                old_summary = str(entry.get("summary", "") or "")
                label = str(text or "").strip()[:120] or entry.get("id", "Point")
                entry["label"] = label
                entry["title"] = label
                entry["file"] = _sanitize_markdown_file_name(label, fallback=str(entry.get("id", "") or "point"))
                if not old_summary.strip() or _is_auto_summary(old_summary):
                    entry["summary"] = _derive_summary(str(entry.get("note", "") or ""), label)
                break
        self._canvas.replace_graph(graph)
        self._refresh_point_list()
        self._on_graph_changed()

    def _on_note_edited(self) -> None:
        selected = self._canvas.selected_id()
        graph = self._canvas.graph()
        for entry in graph.get("nodes", []) or []:
            if entry.get("id") == selected:
                note = self._note_edit.toPlainText()[:5000]
                entry["note"] = note
                summary = str(entry.get("summary", "") or "")
                if not summary.strip() or _is_auto_summary(summary):
                    entry["summary"] = _derive_summary(note, str(entry.get("title", "") or entry.get("label", "") or ""))
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
                "title": f"Point {idx}",
                "type": "concept",
                "summary": "",
                "note": "",
                "file": f"point_{idx}.md",
                "x": max(0.08, min(0.92, 0.5 + math.cos(angle) * radius)),
                "y": max(0.08, min(0.92, 0.5 + math.sin(angle) * radius)),
            }
        )
        graph["nodes"] = nodes
        self._canvas.set_graph(graph)
        self._canvas.select_id(point_id)
        self._refresh_point_list()
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
        matched = [
            edge
            for edge in edges
            if {edge.get("source"), edge.get("target")} == {self._link_source_id, target_id}
        ]
        if matched:
            graph["edges"] = [edge for edge in edges if edge not in matched]
            self._canvas.set_graph(graph)
            self._canvas.select_id(target_id)
            self._on_graph_changed()
            self._set_status("Link removed.")
        else:
            edges.append({"source": self._link_source_id, "target": target_id, "label": ""})
            graph["edges"] = edges
            self._canvas.set_graph(graph)
            self._canvas.select_id(target_id)
            self._on_graph_changed()
            self._set_status("Link added.")
        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)

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
        self._refresh_point_list()
        self._sync_storage(write_sidecar=True)

    def _arrange_points(self) -> None:
        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        graph = _arrange_graph_wide_angles(self._canvas.graph())
        self._canvas.set_graph(graph)
        self._on_graph_changed()
        self._set_status("Arranged linked points with wider angles.")

    def _save_now(self) -> None:
        self._sync_storage(write_sidecar=True)

    def _reload_from_vault(self) -> None:
        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        graph = _load_graph_for_node(self._node_item)
        self._canvas.set_graph(graph)
        self._refresh_point_list()
        self._sync_storage(write_sidecar=True)
        self._set_status("Reloaded vault files.")

    def _choose_vault_folder(self, start: str) -> str:
        parent = _dialog_parent_for_node(self._node_item) or self.window() or self
        dlg = QtWidgets.QFileDialog(parent, "Open Data Nexus Vault", start or str(Path.cwd()))
        dlg.setStyleSheet(
            "QFileDialog{background:#1f232a;color:#e5e7eb;}"
            "QWidget{background:#1f232a;color:#e5e7eb;}"
            "QLabel,QTreeView,QListView,QComboBox,QLineEdit{color:#e5e7eb;background:#111827;}"
            "QTreeView,QListView,QLineEdit,QComboBox{border:1px solid #334155;border-radius:4px;}"
            "QPushButton{background:#1f2937;color:#e5e7eb;border:1px solid #334155;border-radius:4px;padding:4px 8px;}"
            "QPushButton:hover{background:#273548;}"
        )
        try:
            dlg.setFileMode(QtWidgets.QFileDialog.FileMode.Directory)
        except Exception:
            dlg.setFileMode(QtWidgets.QFileDialog.Directory)
        try:
            dlg.setOption(QtWidgets.QFileDialog.Option.ShowDirsOnly, True)
            dlg.setOption(QtWidgets.QFileDialog.Option.DontUseNativeDialog, True)
        except Exception:
            dlg.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
            dlg.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, True)
        try:
            dlg.setModal(True)
        except Exception:
            pass
        if _qexec(dlg) != QtWidgets.QDialog.Accepted:
            return ""
        try:
            selected = dlg.selectedFiles()
        except Exception:
            selected = []
        return str(selected[0] if selected else "").strip()

    def _open_vault_folder(self) -> None:
        model = getattr(self._node_item, "model", None)
        start = _param_value_from_model(model, DATA_NEXUS_VAULT_PARAM, "")
        if start:
            try:
                start = str(Path(start).expanduser().resolve().parent)
            except Exception:
                pass
        folder = self._choose_vault_folder(start)
        if not folder:
            return

        ok, message, graph, _root, vault = _graph_from_data_nexus_folder(folder)
        if not ok or graph is None:
            self._set_status(message)
            return
        if _root is None or vault is None:
            self._set_status("Could not resolve selected Data Nexus vault folder.")
            return

        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        _set_opened_vault_on_item(self._node_item, _root, vault, graph)
        self._canvas.set_graph(graph)
        self._refresh_point_list()
        _emit_node_params_changed(self._node_item)
        self._set_status(f"Opened vault: {vault}")

    def _merge_vault_folder(self) -> None:
        model = getattr(self._node_item, "model", None)
        start = _param_value_from_model(model, DATA_NEXUS_VAULT_PARAM, "")
        if start:
            try:
                start = str(Path(start).expanduser().resolve().parent)
            except Exception:
                pass
        folder = self._choose_vault_folder(start)
        if not folder:
            return

        ok, message, import_graph, _root, vault = _graph_from_data_nexus_folder(folder)
        if not ok or import_graph is None:
            self._set_status(message)
            return

        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        merged, added_nodes, added_edges = _merge_graphs(self._canvas.graph(), import_graph)
        self._canvas.set_graph(merged)
        self._refresh_point_list()
        self._sync_storage(write_sidecar=True)
        self._set_status(f"Merged {added_nodes} point(s) and {added_edges} link(s) from {vault or folder}.")

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
    _ensure_param(node_item, DATA_NEXUS_STORAGE_MODE_PARAM, "workflow")
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
    pad = float(getattr(node_item, "_PADDING", 0.0) or 0.0)
    current_w = float(getattr(node_item, "width", DATA_NEXUS_BODY_W) or DATA_NEXUS_BODY_W)
    current_h = float(getattr(node_item, "height", DATA_NEXUS_BODY_H) or DATA_NEXUS_BODY_H)
    min_w = max(DATA_NEXUS_BODY_W, int(hint.width()))
    min_h = max(DATA_NEXUS_BODY_H, int(hint.height()))
    w = max(min_w, int(current_w))
    available_h = int(max(0.0, current_h - float(y_cursor) - pad))
    h = max(min_h, available_h)
    try:
        if float(getattr(node_item, "width", 0.0) or 0.0) < float(w):
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.width = float(w)
    except Exception:
        pass
    try:
        proxy.setMinimumSize(min_w, min_h)
        proxy.setPreferredSize(w, h)
    except Exception:
        pass
    proxy.resize(w, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    bottom_y = int(y_cursor + h)
    try:
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
    stripe_color="#1e3a8a",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
