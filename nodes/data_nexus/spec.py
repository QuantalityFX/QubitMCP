from __future__ import annotations

import json
import math
import os
import re
import subprocess
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
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
DATA_NEXUS_VIEW_ZOOM_PARAM = "__data_nexus_view_zoom"
DATA_NEXUS_SPLITTER_SIZES_PARAM = "__data_nexus_splitter_sizes"
DATA_NEXUS_SHOW_NEEDS_STRONGER_EDGES_PARAM = "__data_nexus_show_needs_stronger_answer_edges"
DATA_NEXUS_SHOW_REFINES_EDGES_PARAM = "__data_nexus_show_refines_answer_edges"
DATA_NEXUS_ACTIVE_QUESTION_PARAM = "__data_nexus_active_question_id"
DATA_NEXUS_LINK_PULL_ENABLED_PARAM = "__data_nexus_link_pull_enabled"
DATA_NEXUS_LINK_MAX_STRETCH_PARAM = "__data_nexus_link_max_stretch"
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
DATA_NEXUS_LINK_STRETCH_MIN = 0.35
DATA_NEXUS_LINK_STRETCH_MAX = 4.0
DATA_NEXUS_LINK_STRETCH_DEFAULT = 1.25
DATA_NEXUS_LABEL_MIN_ZOOM = 0.55
DATA_NEXUS_GRID_BASE_PX = 360.0
DATA_NEXUS_QUESTION_REFERENCE_EDGE_LABELS = {"needs_stronger_answer", "refines_answer"}
DATA_NEXUS_ANSWER_EDGE_LABEL = "answers_question"
POINT_ID_RE = re.compile(r"^Point ID:\s*`?(?P<id>[^`\r\n]+)`?\s*$", re.IGNORECASE | re.MULTILINE)
FRONT_MATTER_RE = re.compile(r"\A---\s*\r?\n(?P<front>.*?)\r?\n---\s*(?:\r?\n|\Z)", re.DOTALL)
AUTO_SUMMARY_RE = re.compile(r"^Contains knowledge for .+\.$", re.IGNORECASE)


def _default_graph() -> dict[str, Any]:
    return {
        "version": DATA_NEXUS_STORAGE_VERSION,
        "nodes": [],
        "edges": [],
        "folders": [],
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


def _sanitize_point_folder(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    index_stem = Path(DATA_NEXUS_INDEX_FILE).stem.lower()
    parts: list[str] = []
    for raw_part in text.split("/"):
        part = raw_part.strip()
        if not part or part in {".", ".."}:
            continue
        clean = _sanitize_folder_name(part, fallback="")
        if clean and clean.lower() != index_stem:
            parts.append(clean)
    return "/".join(parts)


def _folder_from_file_value(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if "/" not in text:
        return ""
    return _sanitize_point_folder("/".join(text.split("/")[:-1]))


def _entry_folder(entry: dict[str, Any]) -> str:
    return _sanitize_point_folder(entry.get("folder") or _folder_from_file_value(entry.get("file")))


def _point_file_key(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    parts = [part for part in text.split("/") if part and part not in {".", ".."}]
    return "/".join(parts).lower()


def _point_file_base_name(value: Any, fallback: str = "point") -> str:
    text = str(value or "").strip().replace("\\", "/")
    name = PurePosixPath(text).name if text else ""
    return _sanitize_markdown_file_name(name, fallback=fallback)


def _join_point_file(folder: str, file_name: str) -> str:
    clean_folder = _sanitize_point_folder(folder)
    clean_file = _point_file_base_name(file_name)
    return f"{clean_folder}/{clean_file}" if clean_folder else clean_file


def _vault_path_for_point_file(vault: Path, file_name: str) -> Path:
    text = str(file_name or "").strip().replace("\\", "/")
    raw_parts = [part for part in text.split("/") if part and part not in {".", ".."}]
    if not raw_parts:
        raw_parts = ["point.md"]
    safe_parts = [
        _sanitize_folder_name(part, fallback="folder")
        for part in raw_parts[:-1]
    ]
    safe_parts.append(_sanitize_markdown_file_name(raw_parts[-1], fallback="point"))
    return Path(vault).joinpath(*safe_parts)


def _graph_folder_values(graph: dict[str, Any]) -> list[str]:
    folders: set[str] = set()
    raw_folders = graph.get("folders", []) if isinstance(graph, dict) else []
    if isinstance(raw_folders, list):
        for value in raw_folders:
            folder = _sanitize_point_folder(value)
            if folder:
                folders.add(folder)
    raw_nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    if isinstance(raw_nodes, list):
        for entry in raw_nodes:
            if isinstance(entry, dict):
                folder = _entry_folder(entry)
                if folder:
                    folders.add(folder)
    return sorted(folders, key=lambda item: item.lower())


def _clean_inline_text(value: Any, *, limit: int = 500) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit].strip()


def _coerce_string_list(value: Any, *, limit: int = 120) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                value = parsed
            else:
                value = re.split(r"[,;\n]+", text.strip("[]"))
        else:
            value = re.split(r"[,;\n]+", text)
    elif not isinstance(value, (list, tuple, set)):
        value = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip().strip("\"'")
        if not text:
            continue
        text = _clean_inline_text(text, limit=limit)
        key = text.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _normalize_point_type(value: Any, fallback: str = "concept") -> str:
    return _sanitize_folder_name(str(value or "").strip().lower(), fallback=fallback)


def _is_question_point(entry: dict[str, Any]) -> bool:
    point_type = _normalize_point_type(entry.get("type", ""), fallback="")
    if point_type in {"prep_question", "question", "refinement_question"}:
        return True
    folder = _entry_folder(entry).lower()
    return folder in {"prep_questions", "refinement_questions"}


def _is_slide_point(entry: dict[str, Any]) -> bool:
    point_type = _normalize_point_type(entry.get("type", ""), fallback="")
    if point_type == "slide":
        return True
    return _entry_folder(entry).lower() == "slides"


def _is_archived_slide_point(entry: dict[str, Any]) -> bool:
    point_type = _normalize_point_type(entry.get("type", ""), fallback="")
    if point_type in {"old_slide", "previous_slide", "deprecated_slide"}:
        return True
    return _entry_folder(entry).lower() in {"previous slides", "old slides", "deprecated slides"}


def _is_answer_point(entry: dict[str, Any], answer_ids: set[str] | None = None) -> bool:
    point_id = str((entry or {}).get("id", "") or "").strip()
    if answer_ids is not None and point_id and point_id in answer_ids:
        return True
    point_type = _normalize_point_type((entry or {}).get("type", ""), fallback="")
    if point_type in {"answer", "prep_answer"}:
        return True
    return _entry_folder(entry or {}).lower() in {"answers", "prep_answers"}


def _edge_label(edge: dict[str, Any]) -> str:
    return str((edge or {}).get("label", "") or "").strip().lower()


def _answer_point_ids_from_edges(nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for edge in edges:
        if _edge_label(edge) != DATA_NEXUS_ANSWER_EDGE_LABEL:
            continue
        source_id = str(edge.get("source", "") or "").strip()
        target_id = str(edge.get("target", "") or "").strip()
        source = nodes.get(source_id)
        target = nodes.get(target_id)
        if source is not None and target is not None and _is_question_point(target) and not _is_question_point(source):
            out.add(source_id)
        elif source is not None and target is not None and _is_question_point(source) and not _is_question_point(target):
            out.add(target_id)
    return out


def _is_question_reference_edge(edge: dict[str, Any]) -> bool:
    return _edge_label(edge) in DATA_NEXUS_QUESTION_REFERENCE_EDGE_LABELS


def _expected_answer_point_type(entry: dict[str, Any]) -> str:
    note = str((entry or {}).get("note", "") or "")
    match = re.search(
        r"^Expected answer point type:\s*`?(?P<type>[^`\r\n]+)`?\s*$",
        note,
        re.IGNORECASE | re.MULTILINE,
    )
    if match:
        return _normalize_point_type(match.group("type"), fallback="")
    return ""


def _question_text_from_entry(entry: dict[str, Any]) -> str:
    note = str((entry or {}).get("note", "") or "").replace("\r\n", "\n").replace("\r", "\n")
    match = re.search(r"^Question:\s*\n(?P<text>.*?)(?:\n\s*\n|$)", note, re.IGNORECASE | re.MULTILINE | re.DOTALL)
    if match:
        return _clean_inline_text(match.group("text"), limit=500)
    clean_note = _clean_inline_text(note, limit=500)
    if clean_note.endswith("?"):
        return clean_note
    return _clean_inline_text((entry or {}).get("summary", ""), limit=500)


def _question_source_point_ids(entry: dict[str, Any]) -> list[str]:
    return _coerce_string_list(
        (entry or {}).get("source_point_ids")
        or (entry or {}).get("matched_point_ids")
        or (entry or {}).get("source_ids")
    )


def _question_kind(entry: dict[str, Any]) -> str:
    point_type = _normalize_point_type((entry or {}).get("type", ""), fallback="")
    if point_type == "refinement_question":
        return "refinement"
    note = str((entry or {}).get("note", "") or "")
    match = re.search(
        r"^Question kind:\s*(?P<kind>[^\r\n]+)\s*$",
        note,
        re.IGNORECASE | re.MULTILINE,
    )
    if match:
        return _sanitize_folder_name(match.group("kind"), fallback="").lower()
    if _entry_folder(entry).lower() == "refinement_questions":
        return "refinement"
    return ""


def _question_reference_edge_label(entry: dict[str, Any]) -> str:
    if _question_kind(entry) == "refinement":
        return "refines_answer"
    return "needs_stronger_answer"


def _question_display_color(entry: dict[str, Any]) -> str:
    if _question_reference_edge_label(entry) == "refines_answer":
        return "#facc15"
    return "#f97316"


def _answer_label_for_question(entry: dict[str, Any]) -> str:
    label = str((entry or {}).get("title", "") or (entry or {}).get("label", "") or (entry or {}).get("id", "") or "Question").strip()
    label = re.sub(r"\s+(?:Prep|Refinement)\s+Question\s*$", "", label, flags=re.IGNORECASE).strip()
    return (label or "Data Nexus")[:92].rstrip() + " Answer"


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


def _yaml_string_list(value: Any) -> str:
    return json.dumps(_coerce_string_list(value), ensure_ascii=False)


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
    folder = _entry_folder(entry)
    raw = str(entry.get("file", "") or "").strip().replace("\\", "/")
    if raw:
        if not folder:
            folder = _folder_from_file_value(raw)
        current = _point_file_base_name(raw, fallback=point_id or "point")
        id_file = _sanitize_markdown_file_name(point_id, fallback="point")
        if title and current.lower() == id_file.lower() and preferred.lower() != id_file.lower():
            return _join_point_file(folder, preferred)
        return _join_point_file(folder, current)
    return _join_point_file(folder, preferred)


def _set_point_folder(entry: dict[str, Any], folder: str) -> None:
    clean_folder = _sanitize_point_folder(folder)
    point_id = str(entry.get("id", "") or "point").strip()
    label = str(entry.get("title", "") or entry.get("label", "") or point_id or "point").strip()
    raw_file = str(entry.get("file", "") or "").strip().replace("\\", "/")
    file_name = _point_file_base_name(raw_file or label, fallback=point_id or "point")
    if clean_folder:
        entry["folder"] = clean_folder
        entry["file"] = f"{clean_folder}/{file_name}"
    else:
        entry.pop("folder", None)
        entry["file"] = file_name


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


def _param_bool_from_model(model, name: str, default: bool = False) -> bool:
    raw = _param_value_from_model(model, name, None)
    if raw is None:
        return bool(default)
    text = str(raw or "").strip().lower()
    if not text:
        return bool(default)
    return text not in {"0", "false", "no", "off", "hidden", "hide"}


def _coerce_link_max_stretch(value: Any, default: float = DATA_NEXUS_LINK_STRETCH_DEFAULT) -> float:
    try:
        out = float(value)
    except Exception:
        out = default
    if not math.isfinite(out):
        out = default
    return max(DATA_NEXUS_LINK_STRETCH_MIN, min(DATA_NEXUS_LINK_STRETCH_MAX, out))


def _param_link_max_stretch_from_model(model) -> float:
    raw = _param_value_from_model(model, DATA_NEXUS_LINK_MAX_STRETCH_PARAM, "")
    if not str(raw or "").strip():
        return DATA_NEXUS_LINK_STRETCH_DEFAULT
    return _coerce_link_max_stretch(raw)


def _splitter_sizes_from_model(model) -> list[int]:
    raw = _param_value_from_model(model, DATA_NEXUS_SPLITTER_SIZES_PARAM, "")
    parts = [part.strip() for part in str(raw or "").split(",") if part.strip()]
    if len(parts) != 3:
        return []
    sizes: list[int] = []
    for part in parts:
        try:
            size = int(round(float(part)))
        except Exception:
            return []
        if size <= 0:
            return []
        sizes.append(max(60, size))
    return sizes if sum(sizes) > 0 else []


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
            DATA_NEXUS_VIEW_ZOOM_PARAM.lower(),
            DATA_NEXUS_SPLITTER_SIZES_PARAM.lower(),
            DATA_NEXUS_SHOW_NEEDS_STRONGER_EDGES_PARAM.lower(),
            DATA_NEXUS_SHOW_REFINES_EDGES_PARAM.lower(),
            DATA_NEXUS_ACTIVE_QUESTION_PARAM.lower(),
            DATA_NEXUS_LINK_PULL_ENABLED_PARAM.lower(),
            DATA_NEXUS_LINK_MAX_STRETCH_PARAM.lower(),
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
    if not math.isfinite(out):
        out = default
    return max(DATA_NEXUS_WORLD_MIN, min(DATA_NEXUS_WORLD_MAX, out))


def _coerce_zoom(value, default: float = 1.0) -> float:
    try:
        out = float(value)
    except Exception:
        out = default
    if not math.isfinite(out):
        out = default
    return max(DATA_NEXUS_MIN_ZOOM, min(DATA_NEXUS_MAX_ZOOM, out))


def _coerce_graph(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        data = {}
    raw_nodes = data.get("nodes", [])
    raw_edges = data.get("edges", [])
    raw_folders = data.get("folders", [])
    folders: list[str] = []
    folder_seen: set[str] = set()
    if isinstance(raw_folders, list):
        for value in raw_folders:
            folder = _sanitize_point_folder(value)
            if not folder or folder.lower() in folder_seen:
                continue
            folder_seen.add(folder.lower())
            folders.append(folder)
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
            folder = _entry_folder(entry)
            source_point_ids = _question_source_point_ids(entry)
            if folder and folder.lower() not in folder_seen:
                folder_seen.add(folder.lower())
                folders.append(folder)
            file_name = ""
            if str(entry.get("file", "") or "").strip():
                file_name = _point_file_name(
                    {
                        **entry,
                        "id": node_id,
                        "label": label,
                        "title": title,
                        "folder": folder,
                    }
                )
            node = {
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
            if folder:
                node["folder"] = folder
            if source_point_ids:
                node["source_point_ids"] = source_point_ids
            nodes.append(node)
    if not nodes:
        return {
            "version": DATA_NEXUS_STORAGE_VERSION,
            "nodes": [],
            "edges": [],
            "folders": sorted(folders, key=lambda item: item.lower()),
        }

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
    return {
        "version": DATA_NEXUS_STORAGE_VERSION,
        "nodes": nodes,
        "edges": edges,
        "folders": sorted(folders, key=lambda item: item.lower()),
    }


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
                "folder": _entry_folder(entry),
            }
        )
    sidecar = {
        "version": DATA_NEXUS_STORAGE_VERSION,
        "nodes": nodes,
        "edges": clean.get("edges", []) or [],
        "folders": clean.get("folders", []) or [],
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


def _point_folder_from_markdown(text: str) -> str:
    metadata = _point_metadata_from_markdown(text)
    return _sanitize_point_folder(metadata.get("folder", ""))


def _point_source_ids_from_markdown(text: str) -> list[str]:
    metadata = _point_metadata_from_markdown(text)
    return _coerce_string_list(
        metadata.get("source_point_ids")
        or metadata.get("matched_point_ids")
        or metadata.get("source_ids")
    )


def _point_markdown(entry: dict[str, Any]) -> str:
    point_id = str(entry.get("id", "") or "").strip()
    label = str(entry.get("title", "") or entry.get("label", "") or point_id or "Point").strip()
    point_type = _normalize_point_type(entry.get("type", "concept"))
    folder = _entry_folder(entry)
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
    ]
    if folder:
        body.append(f"folder: {_yaml_scalar(folder)}")
    source_point_ids = _question_source_point_ids(entry)
    if source_point_ids:
        body.append(f"source_point_ids: {_yaml_string_list(source_point_ids)}")
    body.extend(["---", "", f"# {label}"])
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
        folder = _entry_folder(next_entry)
        if folder:
            next_entry["folder"] = folder
        else:
            next_entry.pop("folder", None)
        file_name = _point_file_name(next_entry)
        if PurePosixPath(file_name).name.lower() == DATA_NEXUS_INDEX_FILE.lower():
            file_name = _sanitize_markdown_file_name(next_entry.get("id", ""), fallback="point")
            file_name = _join_point_file(folder, file_name)
        stem = PurePosixPath(file_name).stem
        suffix = 2
        while _point_file_key(file_name) in used or PurePosixPath(file_name).name.lower() == DATA_NEXUS_INDEX_FILE.lower():
            file_name = _join_point_file(folder, f"{stem}_{suffix}.md")
            suffix += 1
        next_entry["file"] = file_name
        used.add(_point_file_key(file_name))
        nodes.append(next_entry)
    clean["nodes"] = nodes
    clean["folders"] = _graph_folder_values(clean)
    return clean


def _graph_from_vault_files(vault: Path, base_graph: dict[str, Any] | None = None) -> dict[str, Any] | None:
    try:
        files = [
            path
            for path in sorted(vault.rglob("*.md"), key=lambda item: item.relative_to(vault).as_posix().lower())
            if path.name.lower() != DATA_NEXUS_INDEX_FILE.lower()
        ]
    except Exception:
        return None
    if not files:
        return None

    base = _graph_with_point_files(base_graph or _default_graph())
    base_by_id = {str(entry.get("id", "") or ""): entry for entry in base.get("nodes", []) or []}
    base_by_file = {
        _point_file_key(entry.get("file", "")): entry
        for entry in base.get("nodes", []) or []
        if str(entry.get("file", "") or "").strip()
    }
    nodes = []
    folders = set(_graph_folder_values(base))
    seen_ids: set[str] = set()
    for idx, path in enumerate(files):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""
        try:
            relative_path = path.relative_to(vault)
            relative_file = relative_path.as_posix()
            relative_folder = "" if str(relative_path.parent) == "." else _sanitize_point_folder(relative_path.parent.as_posix())
        except Exception:
            relative_file = path.name
            relative_folder = ""
        fallback_id = _sanitize_folder_name(path.stem, fallback=f"point_{idx + 1}")
        point_id = _point_id_from_markdown(text) or fallback_id
        base_id = point_id
        suffix = 2
        while point_id in seen_ids:
            point_id = f"{base_id}_{suffix}"
            suffix += 1
        seen_ids.add(point_id)

        base_entry = (
            base_by_id.get(point_id)
            or base_by_file.get(_point_file_key(relative_file))
            or base_by_file.get(_point_file_key(path.name))
            or {}
        )
        angle = ((idx + 1) * 2.3999632297) % (math.pi * 2.0)
        label_fallback = str(base_entry.get("label", "") or point_id.replace("_", " ").title()).strip()
        label = _point_label_from_markdown(text, label_fallback)
        note = _point_note_from_markdown(text) or str(base_entry.get("note", "") or "")
        folder = _point_folder_from_markdown(text) or relative_folder or _entry_folder(base_entry)
        if folder:
            folders.add(folder)
        file_name = _join_point_file(folder, path.name)
        source_point_ids = _point_source_ids_from_markdown(text) or _question_source_point_ids(base_entry)
        node = {
            "id": point_id,
            "label": label,
            "title": label,
            "type": _point_type_from_markdown(text) or str(base_entry.get("type", "") or "concept"),
            "summary": _point_summary_from_markdown(text, title=label, note=note)
            or str(base_entry.get("summary", "") or ""),
            "note": note,
            "x": _coerce_float(base_entry.get("x"), 0.5 + math.cos(angle) * 0.24),
            "y": _coerce_float(base_entry.get("y"), 0.5 + math.sin(angle) * 0.24),
            "file": file_name,
        }
        if folder:
            node["folder"] = folder
        if source_point_ids:
            node["source_point_ids"] = source_point_ids
        nodes.append(node)
    return _coerce_graph(
        {
            "version": DATA_NEXUS_STORAGE_VERSION,
            "nodes": nodes,
            "edges": base.get("edges", []),
            "folders": sorted(folders, key=lambda item: item.lower()),
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
    active_files = {_point_file_key(entry.get("file", "")) for entry in nodes}
    active_ids = {str(entry.get("id", "") or "").strip().lower() for entry in nodes}
    active_file_by_id = {
        str(entry.get("id", "") or "").strip().lower(): _point_file_key(entry.get("file", ""))
        for entry in nodes
    }
    for folder in _graph_folder_values(clean):
        try:
            vault.joinpath(*folder.split("/")).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
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
        link_name = PurePosixPath(file_name).with_suffix("").as_posix()
        lines.append(f"- [[{link_name}]] {label}{type_text}{summary_text}")
        point_path = _vault_path_for_point_file(vault, file_name)
        point_path.parent.mkdir(parents=True, exist_ok=True)
        point_path.write_text(_point_markdown(entry), encoding="utf-8")

    vault_root = vault.resolve()
    for path in vault.rglob("*.md"):
        if path.name.lower() == DATA_NEXUS_INDEX_FILE.lower():
            continue
        try:
            relative_key = _point_file_key(path.resolve().relative_to(vault_root).as_posix())
        except Exception:
            continue
        if relative_key in active_files:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""
        point_id = _point_id_from_markdown(text).lower()
        if point_id and (point_id not in active_ids or active_file_by_id.get(point_id) != relative_key):
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
    targets = {_vault_path_for_point_file(vault, _point_file_name(entry))}
    if point_id:
        try:
            for path in vault.rglob("*.md"):
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
    vault_root = vault.resolve()
    for path in targets:
        try:
            target = path.resolve()
        except Exception:
            continue
        try:
            target.relative_to(vault_root)
        except Exception:
            continue
        if target.name.lower() == DATA_NEXUS_INDEX_FILE.lower():
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
    elif not vault.is_dir() and any(path.suffix.lower() == ".md" for path in selected.rglob("*.md")):
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
    folders = set(_graph_folder_values(base))
    folders.update(_graph_folder_values(incoming))
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

    return _graph_with_point_files(
        {
            "version": DATA_NEXUS_STORAGE_VERSION,
            "nodes": nodes,
            "edges": edges,
            "folders": sorted(folders, key=lambda item: item.lower()),
        }
    ), added_nodes, added_edges


def _summary_for_graph(graph: dict[str, Any], *, vault_path: str = "") -> str:
    clean = _coerce_graph(graph)
    nodes = clean.get("nodes", []) or []
    edges = clean.get("edges", []) or []
    by_id = {entry.get("id"): entry for entry in nodes if isinstance(entry, dict)}
    active_question_id = str(graph.get("active_question_id", "") or "").strip()
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
    prep_questions = [entry for entry in nodes if isinstance(entry, dict) and _is_question_point(entry)]
    if prep_questions:
        lines.append("Prep Questions:")
        for entry in prep_questions[:20]:
            label = str(entry.get("title", "") or entry.get("label", "") or entry.get("id", "") or "Question").strip()
            point_id = str(entry.get("id", "") or "").strip()
            expected_type = _expected_answer_point_type(entry)
            question_text = _question_text_from_entry(entry)
            expected = f" expects `{expected_type}`" if expected_type else ""
            identity = f" [id: {point_id}]" if point_id else ""
            answered = _find_answer_for_question(nodes, edges, point_id) if point_id else None
            status = "answered" if answered is not None else "open"
            if active_question_id and point_id == active_question_id:
                status = f"ACTIVE, {status}"
            source_ids = ", ".join(_question_source_point_ids(entry)[:8])
            source_text = f" sources: {source_ids}" if source_ids else ""
            lines.append(
                f"- {label}{identity} [{status}]{expected}{source_text}"
                + (f": {question_text}" if question_text else "")
            )
        if len(prep_questions) > 20:
            lines.append(f"- ... {len(prep_questions) - 20} more prep questions")
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
    if op in {"answer", "answer_question", "answer_prep_question", "resolve_question", "satisfy_question"}:
        return "answer_question"
    if op in {"delete", "remove", "forget", "delete_node", "remove_point"}:
        return "delete_point"
    if op in {"connect", "add_link", "link_points"}:
        return "link"
    if op in {"disconnect", "remove_link"}:
        return "unlink"
    if op in {"prune", "prune_points", "keep_only", "keep_only_points", "delete_unrelated"}:
        return "prune_points"
    if op in {"sync_task_questions", "replace_task_questions", "sync_prep_questions"}:
        return "sync_task_questions"
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
    folder: str = "",
) -> dict[str, Any]:
    clean_label = str(label or point_id or "Point").strip()[:120] or "Point"
    clean_id = _unique_point_id(point_id or clean_label, nodes)
    clean_note = str(note or "").strip()[:5000]
    clean_summary = _clean_inline_text(summary, limit=500) or _derive_summary(clean_note, clean_label)
    idx = len(nodes) + 1
    angle = (idx * 2.3999632297) % (math.pi * 2.0)
    entry = {
        "id": clean_id,
        "label": clean_label,
        "title": clean_label,
        "type": _normalize_point_type(point_type, fallback="concept"),
        "summary": clean_summary,
        "note": clean_note,
        "x": max(0.08, min(0.92, 0.5 + math.cos(angle) * 0.24)),
        "y": max(0.08, min(0.92, 0.5 + math.sin(angle) * 0.24)),
    }
    _set_point_folder(entry, folder)
    return entry


def _resolve_or_create_point(nodes: list[dict[str, Any]], ref: str, *, create_missing: bool = True) -> dict[str, Any] | None:
    entry = _find_point(nodes, ref)
    if entry is None and not create_missing:
        entry = _find_point_for_query(nodes, ref)
    if entry is not None or not create_missing:
        return entry
    entry = _new_point(ref, nodes)
    nodes.append(entry)
    return entry


def _find_question_point(nodes: list[dict[str, Any]], ref: str) -> dict[str, Any] | None:
    questions = [entry for entry in nodes if isinstance(entry, dict) and _is_question_point(entry)]
    entry = _find_point(questions, ref)
    if entry is None:
        entry = _find_point_for_query(questions, ref)
    return entry


def _find_answer_for_question(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    question_id: str,
) -> dict[str, Any] | None:
    by_id = {str(entry.get("id", "") or ""): entry for entry in nodes if isinstance(entry, dict)}
    for edge in edges:
        if _edge_label(edge) != DATA_NEXUS_ANSWER_EDGE_LABEL:
            continue
        if str(edge.get("target", "") or "") != question_id:
            continue
        source_id = str(edge.get("source", "") or "")
        entry = by_id.get(source_id)
        if entry is not None and not _is_question_point(entry):
            return entry
    return None


def _next_open_question_id(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    exclude: set[str] | None = None,
) -> str:
    skipped = {str(item or "").strip() for item in (exclude or set()) if str(item or "").strip()}
    for entry in nodes:
        if not isinstance(entry, dict) or not _is_question_point(entry):
            continue
        point_id = str(entry.get("id", "") or "").strip()
        if not point_id or point_id in skipped:
            continue
        if _find_answer_for_question(nodes, edges, point_id) is None:
            return point_id
    return ""


def _set_active_question_on_item(node_item, question_id: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    _set_param_value_on_model(model, DATA_NEXUS_ACTIVE_QUESTION_PARAM, str(question_id or "").strip())
    _ensure_hidden_params(node_item)


def set_active_data_nexus_question_from_item(node_item, question_id: str = "") -> tuple[bool, str]:
    graph = _load_graph_for_node(node_item)
    nodes = [dict(entry) for entry in graph.get("nodes", []) or []]
    edges = [dict(edge) for edge in graph.get("edges", []) or []]
    clean_question_id = str(question_id or "").strip()
    if not clean_question_id:
        clean_question_id = _next_open_question_id(nodes, edges)
    if clean_question_id and _find_question_point(nodes, clean_question_id) is None:
        return False, f"Prep question not found: {clean_question_id}"
    _set_active_question_on_item(node_item, clean_question_id)
    _emit_node_params_changed(node_item)
    if clean_question_id:
        return True, f"Active prep question set to `{clean_question_id}`."
    return True, "No open prep question is active."


def _ensure_edge(
    edges: list[dict[str, Any]],
    source_id: str,
    target_id: str,
    label: str,
) -> bool:
    clean_label = str(label or "").strip()[:120]
    for edge in edges:
        if str(edge.get("source", "") or "") == source_id and str(edge.get("target", "") or "") == target_id:
            if clean_label and str(edge.get("label", "") or "") != clean_label:
                edge["label"] = clean_label
                return True
            return False
    edges.append({"source": source_id, "target": target_id, "label": clean_label})
    return True


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

    arm_length = max(
        DATA_NEXUS_LINK_MIN_GRID_DISTANCE * 1.1,
        (DATA_NEXUS_LINK_MIN_GRID_DISTANCE + DATA_NEXUS_LINK_MAX_GRID_DISTANCE) * 0.5,
    )
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
        return arm_length * max(0.85, math.sqrt(float(len(component))) * 0.42)

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
        required_gap = arm_length * 0.78 + component_radius(component)
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


def _arrange_graph_question_clusters(graph: dict[str, Any]) -> dict[str, Any]:
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

    question_ids = sorted(
        [point_id for point_id, entry in by_id.items() if _is_question_point(entry)],
        key=sort_key,
    )
    slide_ids = sorted(
        [
            point_id
            for point_id, entry in by_id.items()
            if not _is_question_point(entry) and _is_slide_point(entry)
        ],
        key=sort_key,
    )
    archived_slide_ids = sorted(
        [
            point_id
            for point_id, entry in by_id.items()
            if not _is_question_point(entry) and _is_archived_slide_point(entry)
        ],
        key=sort_key,
    )
    other_ids = sorted(
        [
            point_id
            for point_id, entry in by_id.items()
            if not _is_question_point(entry) and not _is_slide_point(entry) and not _is_archived_slide_point(entry)
        ],
        key=sort_key,
    )

    def regular_cluster_key(point_id: str) -> str:
        entry = by_id.get(point_id, {})
        point_type = _normalize_point_type(entry.get("type", ""), fallback="")
        folder = _entry_folder(entry).strip().lower()
        if point_type and point_type not in {"concept", "point", "answer", "prep_answer"}:
            return f"type:{point_type}"
        if folder:
            return f"folder:{folder}"
        return f"type:{point_type or 'concept'}"

    regular_by_cluster: dict[str, list[str]] = {}
    for point_id in other_ids:
        regular_by_cluster.setdefault(regular_cluster_key(point_id), []).append(point_id)
    regular_group_specs = [
        (f"regular:{cluster_key}", sorted(ids, key=sort_key))
        for cluster_key, ids in sorted(
            regular_by_cluster.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )
    ]

    has_groups = any(ids for ids in (question_ids, slide_ids, archived_slide_ids)) or bool(regular_group_specs)
    if not has_groups:
        clean["nodes"] = nodes
        clean["edges"] = edges
        return clean

    spacing = max(DATA_NEXUS_REPULSION_GRID_DISTANCE * 1.35, 0.24)
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))

    def clamp(value: float) -> float:
        return max(DATA_NEXUS_WORLD_MIN, min(DATA_NEXUS_WORLD_MAX, float(value)))

    def group_extent(count: int) -> float:
        if count <= 1:
            return 0.26
        return max(0.32, spacing * (math.sqrt(float(count - 1)) + 0.45))

    center_by_group = {
        "questions": (-0.62, -0.48),
        "slides": (1.58, -0.48),
        "previous_slides": (1.58, 1.12),
        "regular": (0.5, 1.1),
    }
    if regular_group_specs:
        regular_count = len(regular_group_specs)
        if regular_count == 1:
            center_by_group[regular_group_specs[0][0]] = center_by_group["regular"]
        else:
            max_regular_size = max(len(ids) for _name, ids in regular_group_specs)
            columns = max(1, min(3, math.ceil(math.sqrt(float(regular_count)))))
            step_x = max(0.72, group_extent(max_regular_size) + 0.32)
            step_y = max(0.54, group_extent(max_regular_size) + 0.24)
            base_x = -0.05 - ((columns - 1) * step_x * 0.5)
            base_y = 1.24
            for index, (name, _ids) in enumerate(regular_group_specs):
                row = index // columns
                col = index % columns
                center_by_group[name] = (base_x + (col * step_x), base_y + (row * step_y))
    group_specs = [
        ("questions", question_ids),
        ("slides", slide_ids),
        ("previous_slides", archived_slide_ids),
        *regular_group_specs,
    ]
    groups = [(name, ids) for name, ids in group_specs if ids]
    if len(groups) == 1:
        center_by_group[groups[0][0]] = (0.5, 0.5)

    for group_name, ids in groups:
        center_x, center_y = center_by_group.get(group_name, (0.5, 0.5))
        group_set = set(ids)
        ordered = sorted(
            ids,
            key=lambda point_id: (
                -len(adjacency.get(point_id, set()) & group_set),
                sort_key(point_id),
            ),
        )
        for index, point_id in enumerate(ordered):
            entry = by_id.get(point_id)
            if entry is None:
                continue
            if index == 0:
                x = center_x
                y = center_y
            else:
                radius = spacing * math.sqrt(float(index))
                angle = golden_angle * float(index)
                x = center_x + math.cos(angle) * radius
                y = center_y + math.sin(angle) * radius
            entry["x"] = clamp(x)
            entry["y"] = clamp(y)

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


def _edge_dst_port_name(edge) -> str:
    for attr in ("dst_port_name", "dst_label", "dst_name"):
        if hasattr(edge, attr):
            value = str(getattr(edge, attr) or "").strip().lower()
            if value:
                return value
    return ""


def _refresh_connected_task_nodes(node_item, *, requester: str = "") -> int:
    if str(requester or "").strip().lower() == "task":
        return 0
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None:
        return 0
    try:
        from nodes.task import spec as task_spec
    except Exception:
        return 0
    runner = getattr(task_spec, "run_task_step_from_item", None)
    if not callable(runner):
        return 0
    task_kinds = set(getattr(task_spec, "TASK_NODE_KINDS", {"task", "agent_task", "task_node", "workflow_task"}))
    data_port = str(getattr(task_spec, "DATA_NEXUS_INPUT_PORT", "data_nexus") or "data_nexus").strip().lower()
    try:
        edges = list(getattr(scene, "_edges", []) or [])
    except Exception:
        edges = []
    refreshed = 0
    seen: set[int] = set()
    for edge in edges:
        if getattr(edge, "src", None) is not node_item:
            continue
        dst = getattr(edge, "dst", None)
        if dst is None or id(dst) in seen:
            continue
        if _kind_of_item(dst) not in task_kinds:
            continue
        dst_port = _edge_dst_port_name(edge)
        if dst_port and dst_port != data_port:
            continue
        try:
            runner(dst)
            refreshed += 1
            seen.add(id(dst))
        except Exception:
            continue
        widget = getattr(dst, "_task_widget", None)
        if widget is not None and hasattr(widget, "_refresh_from_state"):
            try:
                widget._refresh_from_state()
            except Exception:
                pass
    return refreshed


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
        "answered": 0,
    }
    answered_question_ids: set[str] = set()
    deleted_question_ids: set[str] = set()
    new_point_ids: set[str] = set()

    for action in actions:
        op = _normalized_action_op(action)

        if op == "sync_task_questions":
            task_id = _sanitize_folder_name(_action_text(action, "task_id", "task", "owner"), fallback="")
            if not task_id:
                continue
            keep_ids = {
                _sanitize_folder_name(value, fallback="")
                for value in _action_list(action, "keep_ids", "question_ids", "ids")
                if str(value or "").strip()
            }
            keep_ids = {value for value in keep_ids if value}
            folders = {
                _sanitize_point_folder(value).lower()
                for value in _action_list(action, "folders", "folder")
                if str(value or "").strip()
            } or {"prep_questions", "refinement_questions"}
            prefix = f"{task_id}_"
            remaining: list[dict[str, Any]] = []
            stale_ids: set[str] = set()
            for entry in nodes:
                if not isinstance(entry, dict):
                    continue
                point_id = str(entry.get("id", "") or "").strip()
                folder = _entry_folder(entry).lower()
                is_owned_question = (
                    point_id.startswith(prefix)
                    and point_id not in keep_ids
                    and folder in folders
                    and _is_question_point(entry)
                )
                if not is_owned_question:
                    remaining.append(entry)
                    continue
                _unlink_vault_note_for_entry(node_item, entry)
                stale_ids.add(point_id)
            if stale_ids:
                nodes = remaining
                edges = [
                    edge
                    for edge in edges
                    if edge.get("source") not in stale_ids and edge.get("target") not in stale_ids
                ]
                deleted_question_ids.update(stale_ids)
                counts["deleted"] += len(stale_ids)
                changed = True
            continue

        if op == "answer_question":
            question_ref = _action_text(
                action,
                "question",
                "question_id",
                "question_label",
                "prep_question",
                "target",
                "to",
            )
            explicit_answer_text = _action_text(action, "answer", "response", "note", "description", "comment", "text", "content")
            answer_point_ref = _action_text(
                action,
                "answer_point_id",
                "existing_answer_point_id",
                "answer_point",
                "answer_ref",
                "existing_point_id",
                "source_point_id",
            )
            if not question_ref:
                continue
            question = _find_question_point(nodes, question_ref)
            if question is None:
                continue
            question_id = str(question.get("id", "") or "").strip()
            if not question_id:
                continue

            entry = None
            if answer_point_ref:
                entry = _find_point(nodes, answer_point_ref) or _find_point_for_query(nodes, answer_point_ref)
                if entry is not None and _is_question_point(entry):
                    entry = None
            answer_text = explicit_answer_text
            if not answer_text and entry is not None:
                answer_text = (
                    str(entry.get("note", "") or "").strip()
                    or str(entry.get("summary", "") or "").strip()
                    or str(entry.get("title", "") or entry.get("label", "") or "").strip()
                )
            if entry is None and not answer_text:
                continue

            explicit_answer_label = _action_text(action, "answer_label", "label", "title", "name")
            answer_label = (
                explicit_answer_label
                or str((entry or {}).get("title", "") or (entry or {}).get("label", "") or "").strip()
                or _answer_label_for_question(question)
            )
            question_label = str(question.get("title", "") or question.get("label", "") or "").strip().lower()
            if question_label and answer_label.strip().lower() == question_label:
                answer_label = _answer_label_for_question(question)
            answer_id = _action_text(action, "answer_id", "id", "point_id")
            answer_type = _action_text(action, "type", "point_type", "kind", "answer_type")
            if not answer_type:
                answer_type = str((entry or {}).get("type", "") or "").strip() or _expected_answer_point_type(question) or "prep_answer"
            summary = _action_text(action, "summary")
            folder_text = _action_text(action, "folder", "vault_folder", "group")
            folder = _sanitize_point_folder(folder_text) if folder_text else _entry_folder(entry or {}) or "answers"

            entry = entry or (_find_point(nodes, answer_id) if answer_id else None)
            if entry is not None and _is_question_point(entry):
                entry = None
            entry = entry or _find_answer_for_question(nodes, edges, question_id)
            candidate = _find_point(nodes, answer_label) if explicit_answer_label else None
            if candidate is not None and not _is_question_point(candidate):
                entry = entry or candidate
            was_new = False
            if entry is None:
                entry = _new_point(
                    answer_label,
                    nodes,
                    note=answer_text,
                    point_id=answer_id or f"{question_id}_answer",
                    point_type=answer_type,
                    summary=summary,
                    folder=folder,
                )
                nodes.append(entry)
                entry_id = str(entry.get("id", "") or "").strip()
                if entry_id:
                    new_point_ids.add(entry_id)
                was_new = True
                counts["added"] += 1
                changed = True

            if answer_label and (was_new or explicit_answer_label) and str(entry.get("label", "") or "") != answer_label:
                entry["label"] = answer_label[:120]
                entry["title"] = answer_label[:120]
                entry["file"] = _join_point_file(_entry_folder(entry), answer_label)
                if not was_new:
                    counts["updated"] += 1
                changed = True
            normalized_type = _normalize_point_type(answer_type, fallback="prep_answer")
            if (was_new or _action_text(action, "type", "point_type", "kind", "answer_type")) and str(entry.get("type", "") or "") != normalized_type:
                entry["type"] = normalized_type
                if not was_new:
                    counts["updated"] += 1
                changed = True
            if folder_text and folder and _entry_folder(entry) != folder:
                _set_point_folder(entry, folder)
                if not was_new:
                    counts["updated"] += 1
                changed = True
            clean_summary = _clean_inline_text(summary, limit=500) if summary else ""
            if clean_summary and str(entry.get("summary", "") or "") != clean_summary:
                entry["summary"] = clean_summary
                if not was_new:
                    counts["updated"] += 1
                changed = True

            old_note = str(entry.get("note", "") or "")
            mode = _action_text(action, "note_mode", "mode").lower()
            should_write_note = bool(explicit_answer_text or was_new or not old_note.strip())
            new_note = _append_note(old_note, answer_text) if mode in {"append", "add", "additive"} else answer_text[:5000]
            if should_write_note and new_note != old_note:
                entry["note"] = new_note
                if not str(entry.get("summary", "") or "").strip() or _is_auto_summary(entry.get("summary", "")):
                    entry["summary"] = _derive_summary(
                        new_note,
                        str(entry.get("title", "") or entry.get("label", "") or ""),
                    )
                if not was_new:
                    counts["updated"] += 1
                changed = True

            answer_point_id = str(entry.get("id", "") or "").strip()
            if answer_point_id and _ensure_edge(edges, answer_point_id, question_id, DATA_NEXUS_ANSWER_EDGE_LABEL):
                counts["linked"] += 1
                changed = True
            if question_id:
                answered_question_ids.add(question_id)
            counts["answered"] += 1
            continue

        if op in {"upsert_point", "append_note"}:
            label = _action_text(action, "label", "name", "title")
            point_id = _action_text(action, "id", "point_id")
            ref = point_id or label
            if not ref:
                continue
            point_type = _action_text(action, "type", "point_type", "kind")
            summary = _action_text(action, "summary")
            note = _action_text(action, "note", "description", "comment", "text", "memory", "content")
            folder_text = _action_text(action, "folder", "vault_folder", "group")
            folder = _sanitize_point_folder(folder_text)
            has_source_refs = any(key in action for key in ("source_point_ids", "matched_point_ids", "source_ids"))
            source_point_ids = _coerce_string_list(
                action.get("source_point_ids")
                if "source_point_ids" in action
                else action.get("matched_point_ids")
                if "matched_point_ids" in action
                else action.get("source_ids")
            )
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
                    folder=folder,
                )
                nodes.append(entry)
                entry_id = str(entry.get("id", "") or "").strip()
                if entry_id:
                    new_point_ids.add(entry_id)
                was_new = True
                counts["added"] += 1
                changed = True
            if folder_text and _entry_folder(entry) != folder:
                _set_point_folder(entry, folder)
                if not was_new:
                    counts["updated"] += 1
                changed = True
            if label and str(entry.get("label", "") or "") != label:
                entry["label"] = label[:120]
                entry["title"] = label[:120]
                entry["file"] = _join_point_file(_entry_folder(entry), label)
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
            if has_source_refs:
                existing_source_ids = _question_source_point_ids(entry)
                if existing_source_ids != source_point_ids:
                    if source_point_ids:
                        entry["source_point_ids"] = source_point_ids
                    else:
                        entry.pop("source_point_ids", None)
                    if not was_new:
                        counts["updated"] += 1
                    changed = True

                entry_id = str(entry.get("id", "") or "").strip()
                if entry_id:
                    explicit_link_label = _action_text(action, "source_edge_label", "reference_label")
                    link_label = explicit_link_label or (
                        _question_reference_edge_label(entry) if _is_question_point(entry) else "uses_source"
                    )
                    clean_link_label = str(link_label or "").strip().lower()
                    if _action_bool(action, "replace_source_edges", False):
                        before_count = len(edges)

                        def should_remove_source_edge(edge: dict[str, Any]) -> bool:
                            if str(edge.get("source", "") or "") != entry_id:
                                return False
                            if explicit_link_label:
                                return _edge_label(edge) == clean_link_label
                            if _is_question_point(entry):
                                return _is_question_reference_edge(edge)
                            return bool(clean_link_label and _edge_label(edge) == clean_link_label)

                        edges = [edge for edge in edges if not should_remove_source_edge(edge)]
                        removed_count = before_count - len(edges)
                        if removed_count:
                            counts["unlinked"] += removed_count
                            changed = True
                    if source_point_ids and _action_bool(action, "create_source_edges", False):
                        for source_ref in source_point_ids:
                            source_entry = _find_point(nodes, source_ref) or _find_point_for_query(nodes, source_ref)
                            source_id = str((source_entry or {}).get("id", "") or "").strip()
                            if not source_id or source_id == entry_id:
                                continue
                            if _ensure_edge(edges, entry_id, source_id, link_label):
                                counts["linked"] += 1
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
            before_point_ids = {str(entry.get("id", "") or "").strip() for entry in nodes if isinstance(entry, dict)}
            source = _resolve_or_create_point(nodes, source_ref, create_missing=create_missing)
            target = _resolve_or_create_point(nodes, target_ref, create_missing=create_missing)
            created_link_point_ids = {
                str((entry or {}).get("id", "") or "").strip()
                for entry in (source, target)
                if str((entry or {}).get("id", "") or "").strip()
                and str((entry or {}).get("id", "") or "").strip() not in before_point_ids
            }
            if created_link_point_ids:
                new_point_ids.update(created_link_point_ids)
                counts["added"] += len(created_link_point_ids)
                changed = True
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
            "folders": _graph_folder_values(graph),
        }
    )
    active_question_id = _param_value_from_model(
        getattr(node_item, "model", None),
        DATA_NEXUS_ACTIVE_QUESTION_PARAM,
        "",
    ).strip()
    if active_question_id and active_question_id in (answered_question_ids | deleted_question_ids):
        _set_active_question_on_item(
            node_item,
            _next_open_question_id(
                list(next_graph.get("nodes", []) or []),
                list(next_graph.get("edges", []) or []),
                exclude=answered_question_ids,
            ),
        )
    status_parts = [f"{value} {name}" for name, value in counts.items() if value]
    status = f"Updated by {requester}: " + ", ".join(status_parts) + "."
    widget = getattr(node_item, "_data_nexus_widget", None)
    if widget is not None and hasattr(widget, "_apply_external_graph"):
        try:
            widget._apply_external_graph(
                next_graph,
                status,
                highlight_point_ids=sorted(new_point_ids) if new_point_ids else None,
            )
            refreshed = _refresh_connected_task_nodes(node_item, requester=requester)
            if refreshed:
                status = f"{status} Refreshed {refreshed} connected task(s)."
                try:
                    widget._set_status(status)
                except Exception:
                    pass
            return True, status
        except Exception:
            pass
    ok = _sync_model_from_graph(node_item, next_graph, write_sidecar=True)
    _emit_node_params_changed(node_item)
    refreshed = _refresh_connected_task_nodes(node_item, requester=requester)
    if refreshed:
        status = f"{status} Refreshed {refreshed} connected task(s)."
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
    active_question_id = _param_value_from_model(model, DATA_NEXUS_ACTIVE_QUESTION_PARAM, "")
    if active_question_id:
        graph = dict(graph)
        graph["active_question_id"] = active_question_id
    return _summary_for_graph(graph, vault_path=vault_path)


def data_nexus_point_bundle_from_item(node_item) -> dict[str, Any]:
    graph = _graph_with_point_files(_load_graph_for_node(node_item))
    model = getattr(node_item, "model", None)
    vault_path = _param_value_from_model(model, DATA_NEXUS_VAULT_PARAM, "")
    active_question_id = _param_value_from_model(model, DATA_NEXUS_ACTIVE_QUESTION_PARAM, "")
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
        point_id = str(entry.get("id", "") or "").strip()
        is_question = _is_question_point(entry)
        answer_entry = _find_answer_for_question(nodes, edges, point_id) if is_question and point_id else None
        points.append(
            {
                "id": point_id,
                "type": _normalize_point_type(entry.get("type", "concept")),
                "title": title,
                "summary": str(entry.get("summary", "") or "").strip() or _derive_summary(content, title),
                "content": content,
                "folder": _entry_folder(entry),
                "file": _point_file_name(entry),
                "source_point_ids": _question_source_point_ids(entry),
                "is_question": is_question,
                "question_text": _question_text_from_entry(entry) if is_question else "",
                "expected_answer_point_type": _expected_answer_point_type(entry) if is_question else "",
                "answer_status": "answered" if answer_entry is not None else "open" if is_question else "",
                "is_active_question": bool(active_question_id and point_id == active_question_id),
                "links": links_by_point.get(point_id, []),
            }
        )
    return {
        "version": 2,
        "vault": vault_path,
        "active_question_id": active_question_id,
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
        self._show_needs_stronger_answer_edges = False
        self._show_refines_answer_edges = False
        self._link_pull_enabled = False
        self._link_max_stretch = DATA_NEXUS_LINK_STRETCH_DEFAULT
        self._review_highlight_point_ids: set[str] = set()
        self._drag_offset = QtCore.QPointF(0.0, 0.0)
        self._pan_start = QtCore.QPointF(0.0, 0.0)
        self._pan_start_center = QtCore.QPointF(0.5, 0.5)
        self._zoom = 1.0
        self._view_center = QtCore.QPointF(0.5, 0.5)
        self.setMinimumSize(320, 190)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def set_review_highlight_points(
        self,
        point_ids: list[str] | tuple[str, ...] | set[str],
    ) -> None:
        clean_ids = {
            str(point_id or "").strip()
            for point_id in (point_ids or [])
            if str(point_id or "").strip()
        }
        graph_ids = {str(entry.get("id", "") or "").strip() for entry in self._graph.get("nodes", []) or []}
        clean_ids = {point_id for point_id in clean_ids if point_id in graph_ids}
        self._review_highlight_point_ids = clean_ids
        self.update()

    def _active_review_highlight_ids(self) -> set[str]:
        if not self._review_highlight_point_ids:
            return set()
        return set(self._review_highlight_point_ids)

    def set_link_target_mode(self, active: bool) -> None:
        self._link_target_mode = bool(active)
        try:
            cursor_group = getattr(QtCore.Qt, "CursorShape", QtCore.Qt)
            cursor_name = "CrossCursor" if self._link_target_mode else "ArrowCursor"
            cursor_shape = getattr(cursor_group, cursor_name)
            self.setCursor(cursor_shape)
        except Exception:
            pass

    def set_question_edge_visibility(self, *, needs_stronger: bool, refines: bool) -> None:
        next_needs = bool(needs_stronger)
        next_refines = bool(refines)
        if (
            self._show_needs_stronger_answer_edges == next_needs
            and self._show_refines_answer_edges == next_refines
        ):
            return
        self._show_needs_stronger_answer_edges = next_needs
        self._show_refines_answer_edges = next_refines
        self.update()

    def set_link_pull_settings(self, *, enabled: bool, max_stretch: float) -> None:
        next_enabled = bool(enabled)
        next_stretch = _coerce_link_max_stretch(max_stretch)
        if self._link_pull_enabled == next_enabled and abs(self._link_max_stretch - next_stretch) < 0.001:
            return
        self._link_pull_enabled = next_enabled
        self._link_max_stretch = next_stretch

    def _should_draw_edge(self, edge: dict[str, Any]) -> bool:
        label = _edge_label(edge)
        if label == "needs_stronger_answer":
            return self._show_needs_stronger_answer_edges
        if label == "refines_answer":
            return self._show_refines_answer_edges
        return True

    @staticmethod
    def _point_edge_color(entry: dict[str, Any], answer_ids: set[str] | None = None) -> QtGui.QColor:
        if _is_question_point(entry):
            return QtGui.QColor(_question_display_color(entry))
        if _is_archived_slide_point(entry):
            return QtGui.QColor("#166534")
        if _is_slide_point(entry):
            return QtGui.QColor("#39ff14")
        if _is_answer_point(entry, answer_ids=answer_ids):
            return QtGui.QColor("#0f766e")
        return QtGui.QColor("#14b8a6")

    def _edge_width(self, edge: dict[str, Any], *, glow: bool = False) -> float:
        label = _edge_label(edge)
        width = self._content_pen_width(1.8)
        if label == "needs_stronger_answer":
            width = self._content_pen_width(1.05)
        elif label == "refines_answer":
            width = self._content_pen_width(1.05)
        elif label == DATA_NEXUS_ANSWER_EDGE_LABEL:
            width = self._content_pen_width(2.0)
        if glow:
            width = max(width + self._content_pen_width(3.2), width * 3.2)
        return width

    def _edge_alpha(self, edge: dict[str, Any], *, glow: bool = False) -> int:
        if glow:
            return 46 if _is_question_reference_edge(edge) else 62
        if _is_question_reference_edge(edge):
            return 132
        if _edge_label(edge) == DATA_NEXUS_ANSWER_EDGE_LABEL:
            return 230
        return 225

    def _edge_brush(
        self,
        src: dict[str, Any],
        dst: dict[str, Any],
        start: QtCore.QPointF,
        end: QtCore.QPointF,
        *,
        alpha: int,
        answer_ids: set[str] | None = None,
    ) -> QtGui.QBrush:
        src_color = self._point_edge_color(src, answer_ids=answer_ids)
        dst_color = self._point_edge_color(dst, answer_ids=answer_ids)
        src_color.setAlpha(alpha)
        dst_color.setAlpha(alpha)
        src_kind = "question" if _is_question_point(src) else "slide" if (_is_slide_point(src) or _is_archived_slide_point(src)) else "answer" if _is_answer_point(src, answer_ids=answer_ids) else "point"
        dst_kind = "question" if _is_question_point(dst) else "slide" if (_is_slide_point(dst) or _is_archived_slide_point(dst)) else "answer" if _is_answer_point(dst, answer_ids=answer_ids) else "point"
        if src_kind == dst_kind:
            return QtGui.QBrush(src_color)
        gradient = QtGui.QLinearGradient(start, end)
        gradient.setColorAt(0.0, src_color)
        gradient.setColorAt(1.0, dst_color)
        return QtGui.QBrush(gradient)

    def _edge_pen(
        self,
        edge: dict[str, Any],
        src: dict[str, Any],
        dst: dict[str, Any],
        start: QtCore.QPointF,
        end: QtCore.QPointF,
        *,
        glow: bool = False,
        answer_ids: set[str] | None = None,
    ) -> QtGui.QPen:
        width = self._edge_width(edge, glow=glow)
        alpha = self._edge_alpha(edge, glow=glow)
        brush = self._edge_brush(src, dst, start, end, alpha=alpha, answer_ids=answer_ids)
        try:
            pen = QtGui.QPen(brush, width)
        except Exception:
            color = self._point_edge_color(src, answer_ids=answer_ids)
            color.setAlpha(alpha)
            pen = QtGui.QPen(color, width)
        try:
            pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        except Exception:
            try:
                pen.setCapStyle(QtCore.Qt.RoundCap)
            except Exception:
                pass
        return pen

    def _metadata_question_edges(self, nodes: dict[str, dict[str, Any]]):
        for entry in nodes.values():
            if not isinstance(entry, dict) or not _is_question_point(entry):
                continue
            question_id = str(entry.get("id", "") or "").strip()
            if not question_id:
                continue
            label = _question_reference_edge_label(entry)
            for source_id in _question_source_point_ids(entry):
                source_id = str(source_id or "").strip()
                if not source_id or source_id == question_id or source_id not in nodes:
                    continue
                yield {"source": question_id, "target": source_id, "label": label}

    def question_reference_edge_counts(self) -> dict[str, int]:
        nodes = {str(entry.get("id", "") or ""): entry for entry in self._graph.get("nodes", []) or []}
        counts = {"needs_stronger_answer": 0, "refines_answer": 0}
        seen: set[tuple[str, str, str]] = set()
        edges = list(self._graph.get("edges", []) or [])
        edges.extend(self._metadata_question_edges(nodes))
        for edge in edges:
            label = _edge_label(edge)
            if label not in counts:
                continue
            source = str(edge.get("source", "") or "")
            target = str(edge.get("target", "") or "")
            if not source or not target or source not in nodes or target not in nodes:
                continue
            key = (source, target, label)
            if key in seen:
                continue
            seen.add(key)
            counts[label] += 1
        return counts

    def _draw_edge_line(
        self,
        painter: QtGui.QPainter,
        nodes: dict[str, dict[str, Any]],
        edge: dict[str, Any],
        answer_ids: set[str] | None = None,
    ) -> bool:
        if not self._should_draw_edge(edge):
            return False
        src = nodes.get(edge.get("source"))
        dst = nodes.get(edge.get("target"))
        if not src or not dst:
            return False
        a = self._to_screen(src.get("x", 0.5), src.get("y", 0.5))
        b = self._to_screen(dst.get("x", 0.5), dst.get("y", 0.5))
        painter.setPen(self._edge_pen(edge, src, dst, a, b, glow=True, answer_ids=answer_ids))
        painter.drawLine(a, b)
        painter.setPen(self._edge_pen(edge, src, dst, a, b, answer_ids=answer_ids))
        painter.drawLine(a, b)
        return True

    def set_graph(self, graph: dict[str, Any]) -> None:
        self._graph = _coerce_graph(graph)
        ids = {str(entry.get("id", "") or "").strip() for entry in self._graph.get("nodes", [])}
        if self._review_highlight_point_ids:
            self._review_highlight_point_ids = {point_id for point_id in self._review_highlight_point_ids if point_id in ids}
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
        ids = {str(entry.get("id", "") or "").strip() for entry in self._graph.get("nodes", [])}
        if self._review_highlight_point_ids:
            self._review_highlight_point_ids = {point_id for point_id in self._review_highlight_point_ids if point_id in ids}
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
        next_zoom = _coerce_zoom(zoom)
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
        for entry in self._graph.get("nodes", []) or []:
            center = self._to_screen(entry.get("x", 0.5), entry.get("y", 0.5))
            dx = center.x() - point.x()
            dy = center.y() - point.y()
            dist = math.sqrt(dx * dx + dy * dy)
            hit_radius = max(
                9.0,
                self._point_radius(
                    selected=False,
                    is_question=_is_question_point(entry),
                    is_slide=_is_slide_point(entry) or _is_archived_slide_point(entry),
                )
                + 7.0,
            )
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
        if not self._link_pull_enabled:
            return
        nodes = [entry for entry in self._graph.get("nodes", []) or [] if isinstance(entry, dict)]
        edges = [edge for edge in self._graph.get("edges", []) or [] if isinstance(edge, dict)]
        if len(nodes) < 2 or not edges:
            return
        by_id = {str(entry.get("id", "") or ""): entry for entry in nodes}
        anchor = str(anchor_id or "")
        max_dist = _coerce_link_max_stretch(self._link_max_stretch)
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

                if dist <= max_dist:
                    continue

                correction = (dist - max_dist) * stiffness
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
                    next_ax = self._clamp_world_coord(ax + nx * correction * move_a)
                    next_ay = self._clamp_world_coord(ay + ny * correction * move_a)
                    if abs(next_ax - ax) > 0.0001 or abs(next_ay - ay) > 0.0001:
                        a["x"] = next_ax
                        a["y"] = next_ay
                        changed = True
                if move_b:
                    next_bx = self._clamp_world_coord(bx - nx * correction * move_b)
                    next_by = self._clamp_world_coord(by - ny * correction * move_b)
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

    def _point_radius(self, *, selected: bool, is_question: bool = False, is_slide: bool = False) -> float:
        if is_question:
            base = 7.2 if selected else 5.6
        elif is_slide:
            base = 15.8 if selected else 13.8
        else:
            base = 12.4 if selected else 10.8
        return max(1.6, min(32.0, base * max(DATA_NEXUS_MIN_ZOOM, min(DATA_NEXUS_MAX_ZOOM, float(self._zoom or 1.0)))))

    def _point_glow_radius(
        self,
        radius: float,
        *,
        selected: bool,
        hovered: bool,
        is_question: bool = False,
        is_slide: bool = False,
    ) -> float:
        if is_question:
            multiplier = 2.35 if selected or hovered else 1.9
        elif is_slide:
            multiplier = 3.0 if selected or hovered else 2.55
        else:
            multiplier = 2.65 if selected or hovered else 2.18
        return max(radius + 3.0, radius * multiplier)

    def _draw_review_highlight_ring(
        self,
        painter: QtGui.QPainter,
        center: QtCore.QPointF,
        radius: float,
        *,
        selected: bool = False,
        hovered: bool = False,
    ) -> None:
        ring_radius = radius + self._content_pen_width(6.0)
        glow_radius = ring_radius + self._content_pen_width(9.0)
        glow = QtGui.QRadialGradient(center, glow_radius)
        clear = QtGui.QColor("#ffffff")
        clear.setAlpha(0)
        soft = QtGui.QColor("#ffffff")
        soft.setAlpha(116 if selected or hovered else 86)
        bright = QtGui.QColor("#ffffff")
        bright.setAlpha(170 if selected or hovered else 132)
        inner_stop = max(0.0, min(0.95, (ring_radius - self._content_pen_width(3.0)) / max(1.0, glow_radius)))
        ring_stop = max(inner_stop, min(0.98, ring_radius / max(1.0, glow_radius)))
        glow.setColorAt(0.0, clear)
        glow.setColorAt(inner_stop, clear)
        glow.setColorAt(ring_stop, soft)
        glow.setColorAt(1.0, clear)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(glow))
        painter.drawEllipse(center, glow_radius, glow_radius)

        painter.setBrush(QtCore.Qt.NoBrush)
        pen = QtGui.QPen(bright, self._content_pen_width(2.2))
        pen.setCosmetic(False)
        try:
            pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        except Exception:
            try:
                pen.setCapStyle(QtCore.Qt.RoundCap)
            except Exception:
                pass
        painter.setPen(pen)
        painter.drawEllipse(center, ring_radius, ring_radius)

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

        def grid_pen(*, axis: bool, major: bool) -> QtGui.QPen:
            if axis:
                color = QtGui.QColor("#2a3444")
                color.setAlpha(118)
                width = 1.05
            elif major:
                color = QtGui.QColor("#202b39")
                color.setAlpha(92)
                width = 0.72
            else:
                color = QtGui.QColor("#182231")
                color.setAlpha(66)
                width = 0.48
            return QtGui.QPen(color, width)

        for idx in range(max(0, min(count_x, 500))):
            value = start_x + (idx * step)
            major = abs(value - round(value)) < 0.001
            axis = abs(value) < 0.001
            painter.setPen(grid_pen(axis=axis, major=major))
            painter.drawLine(self._to_screen(value, top), self._to_screen(value, bottom))

        for idx in range(max(0, min(count_y, 500))):
            value = start_y + (idx * step)
            major = abs(value - round(value)) < 0.001
            axis = abs(value) < 0.001
            painter.setPen(grid_pen(axis=axis, major=major))
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
        graph_edges = list(self._graph.get("edges", []) or [])
        answer_ids = _answer_point_ids_from_edges(nodes, graph_edges)
        drawn_question_refs: set[tuple[str, str, str]] = set()
        for edge in graph_edges:
            if self._draw_edge_line(painter, nodes, edge, answer_ids=answer_ids) and _is_question_reference_edge(edge):
                drawn_question_refs.add(
                    (
                        str(edge.get("source", "") or ""),
                        str(edge.get("target", "") or ""),
                        _edge_label(edge),
                    )
                )
        for edge in self._metadata_question_edges(nodes):
            key = (
                str(edge.get("source", "") or ""),
                str(edge.get("target", "") or ""),
                _edge_label(edge),
            )
            if key in drawn_question_refs:
                continue
            self._draw_edge_line(painter, nodes, edge, answer_ids=answer_ids)

        draw_labels = self._should_draw_point_labels()
        if draw_labels:
            font = painter.font()
            try:
                font.setPointSizeF(self._label_font_size(max(7.0, float(font.pointSizeF() or font.pointSize()))))
            except Exception:
                font.setPointSize(max(6, int(round(self._label_font_size(max(7, font.pointSize()))))))
            painter.setFont(font)
        review_highlight_ids = self._active_review_highlight_ids()
        for entry in self._graph.get("nodes", []) or []:
            point_id = str(entry.get("id", "") or "")
            center = self._to_screen(entry.get("x", 0.5), entry.get("y", 0.5))
            selected = point_id == self._selected_id
            hovered = point_id == self._hover_id
            is_question = _is_question_point(entry)
            is_slide = _is_slide_point(entry)
            is_archived_slide = _is_archived_slide_point(entry)
            is_answer = _is_answer_point(entry, answer_ids=answer_ids)
            is_slide_like = is_slide or is_archived_slide
            radius = self._point_radius(selected=selected, is_question=is_question, is_slide=is_slide_like)
            glow_radius = self._point_glow_radius(
                radius,
                selected=selected,
                hovered=hovered,
                is_question=is_question,
                is_slide=is_slide_like,
            )
            glow = QtGui.QRadialGradient(center, glow_radius)
            if is_question:
                is_refine_question = _question_reference_edge_label(entry) == "refines_answer"
                if is_refine_question:
                    outer = QtGui.QColor("#fde047" if selected or hovered else "#facc15")
                    mid = QtGui.QColor("#fef08a" if selected or hovered else "#eab308")
                    inner = QtGui.QColor("#fef9c3" if selected or hovered else "#fde68a")
                    label_color = QtGui.QColor("#fef3c7" if selected else "#facc15")
                else:
                    outer = QtGui.QColor("#fb923c" if selected or hovered else "#f97316")
                    mid = QtGui.QColor("#fdba74" if selected or hovered else "#ea580c")
                    inner = QtGui.QColor("#ffedd5" if selected or hovered else "#fed7aa")
                    label_color = QtGui.QColor("#ffedd5" if selected else "#fb923c")
                core = QtGui.QColor("#fff7ed")
            elif is_slide:
                outer = QtGui.QColor("#7cff00" if selected or hovered else "#39ff14")
                mid = QtGui.QColor("#a3ff12" if selected or hovered else "#65ff32")
                inner = QtGui.QColor("#dcff8f" if selected or hovered else "#baff6b")
                core = QtGui.QColor("#f7ffe8")
                label_color = QtGui.QColor("#dcff8f" if selected else "#39ff14")
            elif is_archived_slide:
                outer = QtGui.QColor("#15803d" if selected or hovered else "#14532d")
                mid = QtGui.QColor("#16a34a" if selected or hovered else "#166534")
                inner = QtGui.QColor("#86efac" if selected or hovered else "#4ade80")
                core = QtGui.QColor("#dcfce7")
                label_color = QtGui.QColor("#86efac" if selected else "#22c55e")
            elif is_answer:
                outer = QtGui.QColor("#0f766e" if selected or hovered else "#115e59")
                mid = QtGui.QColor("#0d9488" if selected or hovered else "#0f766e")
                inner = QtGui.QColor("#5eead4" if selected or hovered else "#2dd4bf")
                core = QtGui.QColor("#ccfbf1" if selected or hovered else "#99f6e4")
                label_color = QtGui.QColor("#5eead4" if selected else "#2dd4bf")
            else:
                outer = QtGui.QColor("#2dd4bf" if selected or hovered else "#0891b2")
                mid = QtGui.QColor("#5eead4" if selected or hovered else "#14b8a6")
                inner = QtGui.QColor("#ccfbf1" if selected or hovered else "#a7f3d0")
                core = QtGui.QColor("#ecfeff" if selected or hovered else "#d1fae5")
                label_color = QtGui.QColor("#ccfbf1" if selected else "#99f6e4")
            outer.setAlpha(0)
            mid.setAlpha(112 if selected or hovered else 76)
            glow.setColorAt(0.0, QtGui.QColor("#ffffff"))
            glow.setColorAt(0.2, inner)
            glow.setColorAt(0.58, mid)
            glow.setColorAt(1.0, outer)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QBrush(glow))
            painter.drawEllipse(center, glow_radius, glow_radius)

            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(core)
            core_radius = max(1.2, radius * 0.34)
            painter.drawEllipse(center, core_radius, core_radius)
            if point_id in review_highlight_ids:
                self._draw_review_highlight_ring(
                    painter,
                    center,
                    radius,
                    selected=selected,
                    hovered=hovered,
                )
            if not draw_labels and not hovered:
                continue
            label = str(entry.get("label", "") or point_id).strip()
            label_rect = QtCore.QRectF(
                center.x() + (radius + 4.0),
                center.y() - max(11.0, radius * 0.85),
                max(120.0, 160.0 * max(0.75, min(1.8, self._zoom))),
                max(18.0, 22.0 * max(0.75, min(1.6, self._zoom))),
            )
            painter.setPen(QtGui.QPen(label_color, 1.0))
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
                self._apply_link_elasticity(self._selected_id, passes=4, strength=0.35)
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
                self._apply_link_elasticity(self._selected_id, passes=4, strength=0.25)
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


class DataNexusPointTree(QtWidgets.QTreeWidget):
    folderDropRequested = QtCore.Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_point_ids: list[str] = []
        self._pressed_point_ids: list[str] = []
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setIndentation(14)
        self.setUniformRowHeights(True)
        try:
            selection_mode = getattr(QtWidgets.QAbstractItemView, "ExtendedSelection", None)
            if selection_mode is None:
                selection_mode = QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
            self.setSelectionMode(selection_mode)
        except Exception:
            self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        try:
            mode = getattr(QtWidgets.QAbstractItemView, "DragDrop", None)
            if mode is None:
                mode = QtWidgets.QAbstractItemView.DragDropMode.DragDrop
            self.setDragDropMode(mode)
        except Exception:
            pass
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        try:
            self.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
        except Exception:
            self.setTextElideMode(QtCore.Qt.ElideRight)

    @staticmethod
    def _role(offset: int = 0):
        try:
            base = QtCore.Qt.ItemDataRole.UserRole
        except Exception:
            try:
                base = QtCore.Qt.UserRole
            except Exception:
                base = 256
        try:
            return base + offset
        except Exception:
            try:
                return int(base) + offset
            except Exception:
                return int(getattr(base, "value", 256)) + offset

    @staticmethod
    def _item_flag(name: str):
        try:
            return getattr(QtCore.Qt.ItemFlag, name)
        except Exception:
            try:
                return getattr(QtCore.Qt, name)
            except Exception:
                return None

    @staticmethod
    def _is_left_button(event) -> bool:
        try:
            button = event.button()
        except Exception:
            return False
        try:
            left_button = QtCore.Qt.MouseButton.LeftButton
        except Exception:
            try:
                left_button = QtCore.Qt.LeftButton
            except Exception:
                return False
        return button == left_button

    @staticmethod
    def _event_pos(event):
        try:
            return event.position().toPoint()
        except Exception:
            try:
                return event.pos()
            except Exception:
                return QtCore.QPoint()

    def _selected_point_ids(self) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        try:
            items = list(self.selectedItems())
        except Exception:
            items = []
        for item in items:
            if item is None or str(item.data(0, self._role(2)) or "") != "point":
                continue
            point_id = str(item.data(0, self._role(0)) or "").strip()
            if point_id and point_id not in seen:
                seen.add(point_id)
                ids.append(point_id)
        if ids:
            return ids
        item = self.currentItem()
        if item is not None and str(item.data(0, self._role(2)) or "") == "point":
            point_id = str(item.data(0, self._role(0)) or "").strip()
            if point_id:
                ids.append(point_id)
        return ids

    def mousePressEvent(self, event):
        self._pressed_point_ids = []
        if self._is_left_button(event):
            item = self.itemAt(self._event_pos(event))
            if item is not None and str(item.data(0, self._role(2)) or "") == "point":
                clicked_id = str(item.data(0, self._role(0)) or "").strip()
                selected_ids = self._selected_point_ids()
                if clicked_id and clicked_id in selected_ids and len(selected_ids) > 1:
                    self._pressed_point_ids = selected_ids
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._pressed_point_ids = []

    def startDrag(self, supported_actions):
        self._drag_point_ids = list(self._pressed_point_ids or self._selected_point_ids())
        if not self._drag_point_ids:
            return
        super().startDrag(supported_actions)
        self._drag_point_ids = []
        self._pressed_point_ids = []

    def dropEvent(self, event):
        point_ids = list(self._drag_point_ids)
        if not point_ids:
            super().dropEvent(event)
            return
        try:
            pos = event.position().toPoint()
        except Exception:
            pos = event.pos()
        target = self.itemAt(pos)
        folder = ""
        if target is not None:
            kind = str(target.data(0, self._role(2)) or "")
            if kind == "folder":
                folder = str(target.data(0, self._role(1)) or "")
            elif kind == "point":
                folder = str(target.data(0, self._role(1)) or "")
        self.folderDropRequested.emit(point_ids, folder)
        self._drag_point_ids = []
        event.acceptProposedAction()


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
            "QTreeWidget{background:#0b1018;color:#e5e7eb;border:1px solid #334155;border-radius:4px;outline:0;}"
            "QTreeWidget::item{padding:1px 5px;border-bottom:1px solid #111827;}"
            "QTreeWidget::item:selected{background:#164e63;color:#f8fafc;}"
            "QTreeWidget::item:hover{background:#1f2937;}"
            "QToolButton,QPushButton{background:#1f2937;color:#e5e7eb;border:1px solid #334155;border-radius:4px;padding:4px 8px;}"
            "QToolButton:hover,QPushButton:hover{background:#273548;}"
            "QCheckBox{color:#cbd5e1;spacing:4px;}"
            "QCheckBox:disabled{color:#64748b;}"
            "QLabel{color:#cbd5e1;}"
            "QSplitter::handle{background:#1f2937;border:1px solid #334155;border-radius:2px;}"
            "QSplitter::handle:horizontal{width:7px;margin:0 1px;}"
            "QSplitter::handle:hover{background:#475569;}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(5)
        self._add_btn = self._make_tool_button("Add point", "SP_FileDialogNewFolder")
        self._add_folder_btn = self._make_tool_button("Create folder", "SP_DirIcon")
        self._link_btn = self._make_tool_button("Link selected point", "SP_ArrowRight")
        self._delete_btn = self._make_tool_button("Delete selected point (asks first)", "SP_TrashIcon")
        self._layout_btn = self._make_tool_button("Arrange linked points", "SP_BrowserReload")
        self._cluster_btn = self._make_tool_button("Cluster questions, slides, previous slides, and regular point types apart", "SP_FileDialogDetailedView")
        self._zoom_out_btn = self._make_text_tool_button("-", "Zoom out")
        self._zoom_reset_btn = self._make_text_tool_button("1:1", "Reset zoom")
        self._zoom_in_btn = self._make_text_tool_button("+", "Zoom in")
        self._save_btn = self._make_tool_button("Save nexus sidecar", "SP_DialogSaveButton")
        self._open_vault_btn = self._make_tool_button("Open/Switch Data Nexus vault", "SP_DialogOpenButton")
        self._merge_vault_btn = self._make_tool_button("Merge another Data Nexus vault into this one", "SP_FileDialogNewFolder")
        self._reload_btn = self._make_tool_button("Reload graph from vault files", "SP_DialogResetButton")
        self._open_btn = self._make_tool_button("Open Data Nexus folder", "SP_DirOpenIcon")
        self._show_needs_edges = QtWidgets.QCheckBox("Needs?")
        self._show_needs_edges.setToolTip("Show source-point lines for questions that need stronger answers")
        self._show_refines_edges = QtWidgets.QCheckBox("Refine?")
        self._show_refines_edges.setToolTip("Show source-point lines for refinement questions")
        self._link_pull_toggle = QtWidgets.QCheckBox("Pull")
        self._link_pull_toggle.setToolTip("Pull connected points only after a link stretches past the max length")
        try:
            slider_orientation = QtCore.Qt.Orientation.Horizontal
        except Exception:
            slider_orientation = QtCore.Qt.Horizontal
        self._link_stretch_slider = QtWidgets.QSlider(slider_orientation)
        self._link_stretch_slider.setRange(
            int(round(DATA_NEXUS_LINK_STRETCH_MIN * 100.0)),
            int(round(DATA_NEXUS_LINK_STRETCH_MAX * 100.0)),
        )
        self._link_stretch_slider.setSingleStep(5)
        self._link_stretch_slider.setPageStep(25)
        self._link_stretch_slider.setFixedWidth(96)
        self._link_stretch_slider.setToolTip("Max link stretch before pull starts")
        self._link_stretch_value = QtWidgets.QLabel("")
        self._link_stretch_value.setFixedWidth(34)
        self._link_stretch_value.setToolTip("Max link stretch before pull starts")
        try:
            self._link_stretch_value.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        except Exception:
            self._link_stretch_value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        for btn in (
            self._add_btn,
            self._add_folder_btn,
            self._link_btn,
            self._delete_btn,
            self._layout_btn,
            self._cluster_btn,
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
        toolbar.addWidget(self._show_needs_edges, 0)
        toolbar.addWidget(self._show_refines_edges, 0)
        toolbar.addWidget(self._link_pull_toggle, 0)
        toolbar.addWidget(self._link_stretch_slider, 0)
        toolbar.addWidget(self._link_stretch_value, 0)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        model = getattr(node_item, "model", None)
        try:
            splitter_orientation = QtCore.Qt.Orientation.Horizontal
        except Exception:
            splitter_orientation = QtCore.Qt.Horizontal
        self._body_splitter = QtWidgets.QSplitter(splitter_orientation)
        self._body_splitter.setHandleWidth(7)
        self._body_splitter.setChildrenCollapsible(False)

        point_panel = QtWidgets.QWidget()
        point_panel.setMinimumWidth(150)
        point_panel.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
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
        self._point_list = DataNexusPointTree()
        point_panel_layout.addLayout(point_header)
        point_panel_layout.addWidget(self._point_filter)
        point_panel_layout.addWidget(self._point_list, 1)
        self._body_splitter.addWidget(point_panel)

        self._canvas = DataNexusCanvas()
        self._canvas.setMinimumWidth(260)
        self._body_splitter.addWidget(self._canvas)

        inspector = QtWidgets.QWidget()
        inspector.setMinimumWidth(180)
        inspector.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
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
        self._body_splitter.addWidget(inspector)
        self._body_splitter.setStretchFactor(0, 0)
        self._body_splitter.setStretchFactor(1, 1)
        self._body_splitter.setStretchFactor(2, 0)
        saved_splitter_sizes = _splitter_sizes_from_model(model)
        self._body_splitter.setSizes(saved_splitter_sizes or [210, 410, 240])
        layout.addWidget(self._body_splitter, 1)

        self._canvas.set_graph(_load_graph_for_node(node_item))
        self._show_needs_edges.setChecked(
            _param_bool_from_model(model, DATA_NEXUS_SHOW_NEEDS_STRONGER_EDGES_PARAM, False)
        )
        self._show_refines_edges.setChecked(
            _param_bool_from_model(model, DATA_NEXUS_SHOW_REFINES_EDGES_PARAM, False)
        )
        link_pull_enabled = _param_bool_from_model(model, DATA_NEXUS_LINK_PULL_ENABLED_PARAM, False)
        link_max_stretch = _param_link_max_stretch_from_model(model)
        self._link_pull_toggle.setChecked(link_pull_enabled)
        self._link_stretch_slider.setValue(int(round(link_max_stretch * 100.0)))
        self._set_link_stretch_label(link_max_stretch)
        self._canvas.set_question_edge_visibility(
            needs_stronger=self._show_needs_edges.isChecked(),
            refines=self._show_refines_edges.isChecked(),
        )
        self._refresh_question_edge_controls()
        self._canvas.set_link_pull_settings(
            enabled=self._link_pull_toggle.isChecked(),
            max_stretch=link_max_stretch,
        )
        saved_zoom = _coerce_zoom(
            _param_value_from_model(model, DATA_NEXUS_VIEW_ZOOM_PARAM, "1.0")
        )
        self._canvas.set_zoom(saved_zoom)
        self._canvas.graphChanged.connect(self._on_graph_changed)
        self._canvas.selectionChanged.connect(self._on_selection_changed)
        self._canvas.linkTargetChosen.connect(self._on_link_target_chosen)
        self._canvas.zoomChanged.connect(self._on_zoom_changed)
        self._body_splitter.splitterMoved.connect(self._on_body_splitter_moved)
        self._point_filter.textChanged.connect(self._on_point_filter_changed)
        self._point_list.currentItemChanged.connect(self._on_point_list_current_item_changed)
        self._point_list.folderDropRequested.connect(self._move_point_to_folder)
        self._label_edit.textEdited.connect(self._on_label_edited)
        self._note_edit.textChanged.connect(self._on_note_edited)
        self._add_btn.clicked.connect(self._add_point)
        self._add_folder_btn.clicked.connect(self._add_folder)
        self._link_btn.clicked.connect(self._begin_link)
        self._delete_btn.clicked.connect(self._delete_selected)
        self._layout_btn.clicked.connect(self._arrange_points)
        self._cluster_btn.clicked.connect(self._cluster_points)
        self._zoom_out_btn.clicked.connect(self._zoom_out)
        self._zoom_reset_btn.clicked.connect(self._zoom_reset)
        self._zoom_in_btn.clicked.connect(self._zoom_in)
        self._save_btn.clicked.connect(self._save_now)
        self._open_vault_btn.clicked.connect(self._open_vault_folder)
        self._merge_vault_btn.clicked.connect(self._merge_vault_folder)
        self._reload_btn.clicked.connect(self._reload_from_vault)
        self._open_btn.clicked.connect(self._open_folder)
        self._show_needs_edges.toggled.connect(self._on_question_edge_visibility_changed)
        self._show_refines_edges.toggled.connect(self._on_question_edge_visibility_changed)
        self._link_pull_toggle.toggled.connect(self._on_link_pull_settings_changed)
        self._link_stretch_slider.valueChanged.connect(self._on_link_pull_settings_changed)
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
        return DataNexusPointTree._role(0)

    @staticmethod
    def _item_folder_role():
        return DataNexusPointTree._role(1)

    @staticmethod
    def _item_kind_role():
        return DataNexusPointTree._role(2)

    def _iter_point_tree_items(self):
        def walk(item):
            yield item
            for child_index in range(item.childCount()):
                yield from walk(item.child(child_index))

        for index in range(self._point_list.topLevelItemCount()):
            yield from walk(self._point_list.topLevelItem(index))

    def _expanded_folder_paths(self) -> set[str]:
        folder_role = self._item_folder_role()
        kind_role = self._item_kind_role()
        expanded: set[str] = set()
        for item in self._iter_point_tree_items():
            if str(item.data(0, kind_role) or "") == "folder" and item.isExpanded():
                folder = str(item.data(0, folder_role) or "")
                if folder:
                    expanded.add(folder)
        return expanded

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
                _entry_folder(entry),
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
        folder = _entry_folder(entry)
        point_type = str(entry.get("type", "") or "").strip()
        summary = str(entry.get("summary", "") or "").strip()
        note = re.sub(r"\s+", " ", str(entry.get("note", "") or "")).strip()
        parts = [label, file_name]
        if folder:
            parts.append(f"folder: {folder}")
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
        node_by_id = {str(entry.get("id", "") or ""): entry for entry in nodes if str(entry.get("id", "") or "")}
        answer_ids = _answer_point_ids_from_edges(node_by_id, list(graph.get("edges", []) or []))
        needle = self._point_filter_text()
        visible = [entry for entry in nodes if self._point_matches_filter(entry, needle)]
        selected = self._canvas.selected_id()
        role = self._item_user_role()
        folder_role = self._item_folder_role()
        kind_role = self._item_kind_role()
        expanded = self._expanded_folder_paths()
        had_tree = self._point_list.topLevelItemCount() > 0

        def folder_with_ancestors(folder: str) -> list[str]:
            parts = [part for part in _sanitize_point_folder(folder).split("/") if part]
            out = []
            for index in range(1, len(parts) + 1):
                out.append("/".join(parts[:index]))
            return out

        visible_folders: set[str] = set()
        for folder in _graph_folder_values(graph):
            if not needle or needle in folder.lower():
                visible_folders.update(folder_with_ancestors(folder))
        for entry in visible:
            visible_folders.update(folder_with_ancestors(_entry_folder(entry)))

        folder_items: dict[str, QtWidgets.QTreeWidgetItem] = {}

        def ensure_folder_item(folder: str) -> QtWidgets.QTreeWidgetItem | None:
            clean_folder = _sanitize_point_folder(folder)
            if not clean_folder:
                return None
            parent = None
            parts = clean_folder.split("/")
            for index in range(1, len(parts) + 1):
                folder_path = "/".join(parts[:index])
                existing = folder_items.get(folder_path)
                if existing is not None:
                    parent = existing
                    continue
                item = QtWidgets.QTreeWidgetItem([parts[index - 1]])
                item.setData(0, role, "")
                item.setData(0, folder_role, folder_path)
                item.setData(0, kind_role, "folder")
                item.setToolTip(0, folder_path)
                item.setSizeHint(0, QtCore.QSize(160, 24))
                try:
                    item.setIcon(0, self.style().standardIcon(QtWidgets.QStyle.SP_DirIcon))
                except Exception:
                    pass
                try:
                    drop_flag = DataNexusPointTree._item_flag("ItemIsDropEnabled")
                    drag_flag = DataNexusPointTree._item_flag("ItemIsDragEnabled")
                    if drop_flag is not None and drag_flag is not None:
                        item.setFlags((item.flags() | drop_flag) & ~drag_flag)
                except Exception:
                    pass
                should_expand = (folder_path in expanded) if had_tree else True
                if parent is None:
                    self._point_list.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                item.setExpanded(should_expand)
                folder_items[folder_path] = item
                parent = item
            return parent

        self._syncing_point_list = True
        try:
            self._point_list.clear()
            for folder in sorted(visible_folders, key=lambda item: item.lower()):
                ensure_folder_item(folder)
            for entry in sorted(visible, key=lambda item: str(item.get("label", "") or item.get("id", "")).lower()):
                point_id = str(entry.get("id", "") or "")
                folder = _entry_folder(entry)
                item = QtWidgets.QTreeWidgetItem([self._point_row_text(entry)])
                item.setData(0, role, point_id)
                item.setData(0, folder_role, folder)
                item.setData(0, kind_role, "point")
                item.setToolTip(0, self._point_row_tooltip(entry))
                item.setSizeHint(0, QtCore.QSize(160, 24))
                try:
                    item.setIcon(0, self.style().standardIcon(QtWidgets.QStyle.SP_FileIcon))
                except Exception:
                    pass
                if _is_question_point(entry):
                    try:
                        item.setForeground(0, QtGui.QBrush(QtGui.QColor(_question_display_color(entry))))
                    except Exception:
                        pass
                elif _is_archived_slide_point(entry):
                    try:
                        item.setForeground(0, QtGui.QBrush(QtGui.QColor("#22c55e")))
                    except Exception:
                        pass
                elif _is_slide_point(entry):
                    try:
                        item.setForeground(0, QtGui.QBrush(QtGui.QColor("#39ff14")))
                    except Exception:
                        pass
                elif _is_answer_point(entry, answer_ids=answer_ids):
                    try:
                        item.setForeground(0, QtGui.QBrush(QtGui.QColor("#2dd4bf")))
                    except Exception:
                        pass
                try:
                    drag_flag = DataNexusPointTree._item_flag("ItemIsDragEnabled")
                    if drag_flag is not None:
                        item.setFlags(item.flags() | drag_flag)
                except Exception:
                    pass
                parent = ensure_folder_item(folder)
                if parent is None:
                    self._point_list.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                if point_id == selected:
                    self._point_list.setCurrentItem(item)
                    self._point_list.scrollToItem(item, QtWidgets.QAbstractItemView.PositionAtCenter)
            count_text = f"{len(visible)}/{len(nodes)}" if needle else str(len(nodes))
            self._point_count.setText(count_text)
        finally:
            self._syncing_point_list = False
        self._refresh_question_edge_controls()

    def _sync_point_list_selection(self, point_id: str) -> None:
        role = self._item_user_role()
        self._syncing_point_list = True
        try:
            if not point_id:
                try:
                    self._point_list.setCurrentItem(None)
                except Exception:
                    self._point_list.clearSelection()
                return
            for item in self._iter_point_tree_items():
                if str(item.data(0, role) or "") == point_id:
                    self._point_list.setCurrentItem(item)
                    self._point_list.scrollToItem(item, QtWidgets.QAbstractItemView.PositionAtCenter)
                    return
            try:
                self._point_list.setCurrentItem(None)
            except Exception:
                self._point_list.clearSelection()
        finally:
            self._syncing_point_list = False

    def _on_point_filter_changed(self, _text: str) -> None:
        self._refresh_point_list()

    def _on_point_list_current_item_changed(self, current, _previous) -> None:
        if self._syncing_point_list or current is None:
            return
        if str(current.data(0, self._item_kind_role()) or "") != "point":
            return
        point_id = str(current.data(0, self._item_user_role()) or "")
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

    def _refresh_question_edge_controls(self) -> None:
        counts = self._canvas.question_reference_edge_counts()
        needs_count = int(counts.get("needs_stronger_answer", 0) or 0)
        refines_count = int(counts.get("refines_answer", 0) or 0)
        model = getattr(self._node_item, "model", None)
        wants_needs = _param_bool_from_model(model, DATA_NEXUS_SHOW_NEEDS_STRONGER_EDGES_PARAM, False)
        wants_refines = _param_bool_from_model(model, DATA_NEXUS_SHOW_REFINES_EDGES_PARAM, False)
        show_needs = wants_needs and needs_count > 0
        show_refines = wants_refines and refines_count > 0

        self._show_needs_edges.blockSignals(True)
        self._show_refines_edges.blockSignals(True)
        try:
            self._show_needs_edges.setEnabled(needs_count > 0)
            self._show_refines_edges.setEnabled(refines_count > 0)
            self._show_needs_edges.setChecked(show_needs)
            self._show_refines_edges.setChecked(show_refines)
        finally:
            self._show_needs_edges.blockSignals(False)
            self._show_refines_edges.blockSignals(False)

        if needs_count > 0:
            self._show_needs_edges.setToolTip(
                f"Show/hide {needs_count} source-point line(s) for questions that need stronger answers"
            )
        else:
            self._show_needs_edges.setToolTip("No source-point lines for needs-answer questions exist in this graph")
        if refines_count > 0:
            self._show_refines_edges.setToolTip(
                f"Show/hide {refines_count} source-point line(s) for refinement questions"
            )
        else:
            self._show_refines_edges.setToolTip("No source-point lines for refinement questions exist in this graph")

        self._canvas.set_question_edge_visibility(needs_stronger=show_needs, refines=show_refines)

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

    def _apply_external_graph(
        self,
        graph: dict[str, Any],
        status: str = "",
        *,
        highlight_point_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    ) -> None:
        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        self._canvas.set_graph(graph)
        if highlight_point_ids is not None:
            self._canvas.set_review_highlight_points(highlight_point_ids)
        self._refresh_point_list()
        self._refresh_question_edge_controls()
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
        zoom_value = _coerce_zoom(zoom)
        model = getattr(self._node_item, "model", None)
        _set_param_value_on_model(model, DATA_NEXUS_VIEW_ZOOM_PARAM, f"{zoom_value:.4f}")
        _ensure_hidden_params(self._node_item)
        self._zoom_out_btn.setEnabled(zoom_value > DATA_NEXUS_MIN_ZOOM + 0.001)
        self._zoom_in_btn.setEnabled(zoom_value < DATA_NEXUS_MAX_ZOOM - 0.001)
        self._set_status(f"Zoom {int(round(zoom_value * 100.0))}%")

    def _on_body_splitter_moved(self, _pos: int, _index: int) -> None:
        try:
            sizes = [int(size) for size in self._body_splitter.sizes()]
        except Exception:
            sizes = []
        if len(sizes) != 3 or sum(sizes) <= 0:
            return
        model = getattr(self._node_item, "model", None)
        _set_param_value_on_model(model, DATA_NEXUS_SPLITTER_SIZES_PARAM, ",".join(str(max(1, size)) for size in sizes))
        _ensure_hidden_params(self._node_item)
        _emit_node_params_changed(self._node_item)

    def _on_question_edge_visibility_changed(self, _checked: bool = False) -> None:
        show_needs = bool(self._show_needs_edges.isChecked())
        show_refines = bool(self._show_refines_edges.isChecked())
        model = getattr(self._node_item, "model", None)
        _set_param_value_on_model(model, DATA_NEXUS_SHOW_NEEDS_STRONGER_EDGES_PARAM, "1" if show_needs else "0")
        _set_param_value_on_model(model, DATA_NEXUS_SHOW_REFINES_EDGES_PARAM, "1" if show_refines else "0")
        _ensure_hidden_params(self._node_item)
        self._canvas.set_question_edge_visibility(needs_stronger=show_needs, refines=show_refines)
        _emit_node_params_changed(self._node_item)
        states = []
        states.append("needs on" if show_needs else "needs off")
        states.append("refine on" if show_refines else "refine off")
        self._set_status(", ".join(states) + ".")

    def _link_stretch_from_slider(self) -> float:
        try:
            raw = float(self._link_stretch_slider.value()) / 100.0
        except Exception:
            raw = DATA_NEXUS_LINK_STRETCH_DEFAULT
        return _coerce_link_max_stretch(raw)

    def _set_link_stretch_label(self, value: float) -> None:
        self._link_stretch_value.setText(f"{_coerce_link_max_stretch(value):.2f}")

    def _on_link_pull_settings_changed(self, _value: Any = None) -> None:
        enabled = bool(self._link_pull_toggle.isChecked())
        max_stretch = self._link_stretch_from_slider()
        self._set_link_stretch_label(max_stretch)
        model = getattr(self._node_item, "model", None)
        _set_param_value_on_model(model, DATA_NEXUS_LINK_PULL_ENABLED_PARAM, "1" if enabled else "0")
        _set_param_value_on_model(model, DATA_NEXUS_LINK_MAX_STRETCH_PARAM, f"{max_stretch:.2f}")
        _ensure_hidden_params(self._node_item)
        self._canvas.set_link_pull_settings(enabled=enabled, max_stretch=max_stretch)
        _emit_node_params_changed(self._node_item)
        state = "on" if enabled else "off"
        self._set_status(f"Link pull {state}, max stretch {max_stretch:.2f}.")

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
                entry["file"] = _join_point_file(_entry_folder(entry), label)
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

    def _selected_tree_folder(self) -> str:
        item = self._point_list.currentItem()
        if item is None:
            return ""
        kind = str(item.data(0, self._item_kind_role()) or "")
        if kind in {"folder", "point"}:
            return _sanitize_point_folder(item.data(0, self._item_folder_role()))
        return ""

    def _add_folder(self) -> None:
        parent = _dialog_parent_for_node(self._node_item) or self.window() or self
        text, ok = QtWidgets.QInputDialog.getText(parent, "Create Data Nexus Folder", "Folder name:")
        if not ok:
            return
        folder = _sanitize_point_folder(text)
        if not folder:
            self._set_status("Enter a valid folder name.")
            return
        graph = self._canvas.graph()
        folders = set(_graph_folder_values(graph))
        if folder in folders:
            self._set_status(f"Folder already exists: {folder}")
            return
        folders.add(folder)
        graph["folders"] = sorted(folders, key=lambda item: item.lower())
        self._canvas.replace_graph(graph)
        self._refresh_point_list()
        self._on_graph_changed()
        self._set_status(f"Created folder: {folder}")

    def _move_point_to_folder(self, point_ids, folder: str) -> None:
        if isinstance(point_ids, str):
            raw_ids = [point_ids]
        else:
            try:
                raw_ids = list(point_ids or [])
            except Exception:
                raw_ids = []
        target_ids: list[str] = []
        seen: set[str] = set()
        for raw_id in raw_ids:
            point_id = str(raw_id or "").strip()
            if point_id and point_id not in seen:
                seen.add(point_id)
                target_ids.append(point_id)
        if not target_ids:
            return
        clean_folder = _sanitize_point_folder(folder)
        graph = self._canvas.graph()
        changed = False
        moved_ids: list[str] = []
        target_id_set = set(target_ids)
        for entry in graph.get("nodes", []) or []:
            point_id = str(entry.get("id", "") or "")
            if point_id not in target_id_set:
                continue
            if _entry_folder(entry) == clean_folder:
                continue
            _set_point_folder(entry, clean_folder)
            moved_ids.append(point_id)
            changed = True
        if not changed:
            return
        folders = set(_graph_folder_values(graph))
        if clean_folder:
            folders.add(clean_folder)
        graph["folders"] = sorted(folders, key=lambda item: item.lower())
        self._canvas.replace_graph(graph)
        self._canvas.select_id(moved_ids[0])
        self._refresh_point_list()
        self._on_graph_changed()
        destination = clean_folder or "root"
        if len(moved_ids) == 1:
            self._set_status(f"Moved point to {destination}.")
        else:
            self._set_status(f"Moved {len(moved_ids)} points to {destination}.")

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
        folder = self._selected_tree_folder()
        nodes.append(_new_point(f"Point {idx}", nodes, point_id=point_id, point_type="concept", folder=folder))
        graph["nodes"] = nodes
        if folder:
            folders = set(_graph_folder_values(graph))
            folders.add(folder)
            graph["folders"] = sorted(folders, key=lambda item: item.lower())
        self._canvas.set_graph(graph)
        self._canvas.set_review_highlight_points([point_id])
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
        label = _clean_inline_text(
            (deleted_node or {}).get("label") or (deleted_node or {}).get("title") or selected,
            limit=120,
        )
        graph = self._canvas.graph()
        linked_count = sum(
            1
            for edge in graph.get("edges", []) or []
            if edge.get("source") == selected or edge.get("target") == selected
        )
        detail = f"Point: {label}\nID: {selected}"
        if linked_count:
            detail += f"\n\nThis will also remove {linked_count} connected link(s)."
        parent = _dialog_parent_for_node(self._node_item) or self.window() or self
        confirm = QtWidgets.QMessageBox.question(
            parent,
            "Delete Data Nexus Point",
            f"Delete this Data Nexus point?\n\n{detail}",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if confirm != QtWidgets.QMessageBox.Yes:
            self._set_status("Delete canceled.")
            return
        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        _unlink_vault_note_for_entry(self._node_item, deleted_node)
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
        self._set_status("Arranged linked points.")

    def _cluster_points(self) -> None:
        self._link_source_id = ""
        self._canvas.set_link_target_mode(False)
        graph = _arrange_graph_question_clusters(self._canvas.graph())
        self._canvas.set_graph(graph)
        self._on_graph_changed()
        nodes = graph.get("nodes", []) or []
        question_count = sum(1 for entry in nodes if isinstance(entry, dict) and _is_question_point(entry))
        slide_count = sum(
            1
            for entry in nodes
            if isinstance(entry, dict) and not _is_question_point(entry) and _is_slide_point(entry)
        )
        archived_slide_count = sum(
            1
            for entry in nodes
            if isinstance(entry, dict) and not _is_question_point(entry) and _is_archived_slide_point(entry)
        )
        other_count = sum(
            1
            for entry in nodes
            if isinstance(entry, dict)
            and not _is_question_point(entry)
            and not _is_slide_point(entry)
            and not _is_archived_slide_point(entry)
        )
        regular_cluster_keys: set[str] = set()
        for entry in nodes:
            if not isinstance(entry, dict):
                continue
            if _is_question_point(entry) or _is_slide_point(entry) or _is_archived_slide_point(entry):
                continue
            point_type = _normalize_point_type(entry.get("type", ""), fallback="")
            folder = _entry_folder(entry).strip().lower()
            if point_type and point_type not in {"concept", "point", "answer", "prep_answer"}:
                regular_cluster_keys.add(f"type:{point_type}")
            elif folder:
                regular_cluster_keys.add(f"folder:{folder}")
            else:
                regular_cluster_keys.add(f"type:{point_type or 'concept'}")
        if question_count or slide_count or archived_slide_count:
            self._set_status(
                f"Clustered {question_count} question point(s), {slide_count} current slide point(s), {archived_slide_count} previous slide point(s), and {other_count} regular point(s) across {len(regular_cluster_keys)} regular cluster(s)."
            )
        else:
            self._set_status("Clustered points by type.")

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
    _ensure_param(node_item, DATA_NEXUS_VIEW_ZOOM_PARAM, "1.0000")
    _ensure_param(node_item, DATA_NEXUS_SPLITTER_SIZES_PARAM, "")
    _ensure_param(node_item, DATA_NEXUS_SHOW_NEEDS_STRONGER_EDGES_PARAM, "0")
    _ensure_param(node_item, DATA_NEXUS_SHOW_REFINES_EDGES_PARAM, "0")
    _ensure_param(node_item, DATA_NEXUS_ACTIVE_QUESTION_PARAM, "")
    _ensure_param(node_item, DATA_NEXUS_LINK_PULL_ENABLED_PARAM, "0")
    _ensure_param(node_item, DATA_NEXUS_LINK_MAX_STRETCH_PARAM, f"{DATA_NEXUS_LINK_STRETCH_DEFAULT:.2f}")
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
