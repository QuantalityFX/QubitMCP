from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List

from echograph.constants import script_dir


SCHEMA_VERSION = 1
SUPPORTED_TOOL_TYPES = {"python_script", "python_code", "workflow_shortcut", "graph_snippet"}
WORKFLOW_OPEN_MODES = {"new_instance", "current_window"}
SHELF_TOOLS_PATH = script_dir() / "shelf_tools.json"


def _slugify(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip().lower())
    text = text.strip("-")
    return text or "shelf-tool"


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)):
        return bool(int(value))
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "y"}:
            return True
        if text in {"0", "false", "no", "off", "n"}:
            return False
    return bool(default)


def _coerce_order(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(fallback)


def _repo_root() -> Path:
    return script_dir().resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def make_stored_path(path: str | Path) -> tuple[str, str]:
    """Return (stored_path, path_mode), preferring repo-relative paths."""
    raw = str(path or "").strip()
    if not raw:
        return "", "absolute"
    try:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (_repo_root() / p).resolve()
        else:
            p = p.resolve()
        root = _repo_root()
        if _is_relative_to(p, root):
            return p.relative_to(root).as_posix(), "repo_relative"
        return str(p), "absolute"
    except Exception:
        return raw, "absolute"


def resolve_stored_path(value: str, path_mode: str | None = None) -> Path:
    raw = str(value or "").strip()
    if not raw:
        return Path()
    mode = str(path_mode or "").strip().lower()
    p = Path(raw).expanduser()
    if mode == "repo_relative" or (not p.is_absolute() and not raw.startswith("~")):
        return (_repo_root() / p).resolve()
    try:
        return p.resolve()
    except Exception:
        return p


def _normalize_args(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, tuple):
        return [str(v) for v in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_params(value: Any) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not isinstance(value, list):
        return out
    for entry in value:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "") or "").strip()
        if not name:
            continue
        out.append({"name": name, "value": str(entry.get("value", "") or "")})
    return out


def _normalize_workflow_open_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in WORKFLOW_OPEN_MODES:
        return text
    return "new_instance"


def _normalize_graph_snippet_payload(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {"format": "EchoGraphClipboard", "version": 1, "nodes": [], "edges": [], "centroid": [0.0, 0.0]}
    payload = deepcopy(value)
    payload["format"] = "EchoGraphClipboard"
    try:
        payload["version"] = int(payload.get("version", 1) or 1)
    except Exception:
        payload["version"] = 1
    if not isinstance(payload.get("nodes"), list):
        payload["nodes"] = []
    if not isinstance(payload.get("edges"), list):
        payload["edges"] = []
    if not isinstance(payload.get("comments"), list):
        payload["comments"] = []
    centroid = payload.get("centroid")
    if not isinstance(centroid, list) or len(centroid) < 2:
        payload["centroid"] = [0.0, 0.0]
    return payload


def normalize_tool(raw: Dict[str, Any], index: int = 0, existing_ids: Iterable[str] = ()) -> Dict[str, Any]:
    tool = dict(raw or {})
    tool_type = str(tool.get("type", "") or "").strip().lower() or "python_script"
    label = str(tool.get("label", "") or "").strip() or tool_type.replace("_", " ").title()
    existing = {str(v) for v in existing_ids}
    tool_id = str(tool.get("id", "") or "").strip()
    if not tool_id or tool_id in existing:
        base = _slugify(label)
        tool_id = f"{base}-{uuid.uuid4().hex[:8]}"

    normalized: Dict[str, Any] = {
        "id": tool_id,
        "type": tool_type,
        "label": label,
        "tooltip": str(tool.get("tooltip", "") or "").strip(),
        "enabled": _coerce_bool(tool.get("enabled"), True),
        "order": _coerce_order(tool.get("order"), index),
        "icon": str(tool.get("icon", "") or "").strip(),
    }

    if tool_type == "python_script":
        script_path = str(tool.get("script_path", "") or "").strip()
        path_mode = str(tool.get("path_mode", "") or "").strip() or "absolute"
        working_dir = str(tool.get("working_dir", "") or "").strip()
        normalized.update(
            {
                "script_path": script_path,
                "path_mode": path_mode,
                "working_dir": working_dir,
                "args": _normalize_args(tool.get("args")),
                "execution": _normalize_execution(tool.get("execution")),
            }
        )
    elif tool_type == "python_code":
        normalized.update(
            {
                "code": str(tool.get("code", "") or ""),
                "params": _normalize_params(tool.get("params")),
                "execution": _normalize_execution(tool.get("execution")),
            }
        )
    elif tool_type == "workflow_shortcut":
        normalized.update(
            {
                "workflow_path": str(tool.get("workflow_path", "") or "").strip(),
                "path_mode": str(tool.get("path_mode", "") or "").strip() or "absolute",
                "open_mode": _normalize_workflow_open_mode(tool.get("open_mode")),
            }
        )
    elif tool_type == "graph_snippet":
        normalized.update(
            {
                "payload": _normalize_graph_snippet_payload(tool.get("payload")),
            }
        )
    else:
        # Preserve unsupported tool payloads so future versions do not lose data.
        normalized.update({k: deepcopy(v) for k, v in tool.items() if k not in normalized})

    for key, value in tool.items():
        if key not in normalized:
            normalized[key] = deepcopy(value)

    return normalized


def _normalize_execution(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    thread_mode = str(raw.get("thread_mode", "worker") or "worker").strip().lower()
    if thread_mode not in {"worker", "main_thread"}:
        thread_mode = "worker"
    return {
        "thread_mode": thread_mode,
        "show_output": _coerce_bool(raw.get("show_output"), True),
    }


class ShelfToolsStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else SHELF_TOOLS_PATH
        self.schema_version = SCHEMA_VERSION
        self.tools: List[Dict[str, Any]] = []
        self.load_error = ""
        self.unsupported_schema = False

    def load(self) -> List[Dict[str, Any]]:
        self.load_error = ""
        self.unsupported_schema = False
        if not self.path.exists():
            self.tools = []
            return self.tools
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.load_error = f"Failed to read shelf tools: {exc}"
            self.tools = []
            return self.tools
        if not isinstance(data, dict):
            self.load_error = "Shelf tools file must contain a JSON object."
            self.tools = []
            return self.tools
        try:
            version = int(data.get("schema_version", SCHEMA_VERSION))
        except Exception:
            version = SCHEMA_VERSION
        self.schema_version = version
        if version > SCHEMA_VERSION:
            self.unsupported_schema = True
            self.load_error = f"Shelf tools schema {version} is newer than supported schema {SCHEMA_VERSION}."
        raw_tools = data.get("tools", [])
        if not isinstance(raw_tools, list):
            raw_tools = []
        self.tools = []
        seen: set[str] = set()
        for idx, raw in enumerate(raw_tools):
            if not isinstance(raw, dict):
                continue
            tool = normalize_tool(raw, idx, seen)
            seen.add(tool["id"])
            self.tools.append(tool)
        self.tools.sort(key=lambda t: _coerce_order(t.get("order"), 0))
        self._renumber()
        return self.tools

    def save(self) -> None:
        self._renumber()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "tools": self.tools,
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def all_tools(self) -> List[Dict[str, Any]]:
        return [dict(tool) for tool in self.tools]

    def add_tool(self, tool: Dict[str, Any], *, save: bool = True) -> Dict[str, Any]:
        existing = {t.get("id", "") for t in self.tools}
        normalized = normalize_tool(tool, len(self.tools), existing)
        self.tools.append(normalized)
        self._renumber()
        if save:
            self.save()
        return dict(normalized)

    def update_tool(self, tool_id: str, patch: Dict[str, Any], *, save: bool = True) -> Dict[str, Any] | None:
        idx = self._index_of(tool_id)
        if idx < 0:
            return None
        current = dict(self.tools[idx])
        current.update(dict(patch or {}))
        current["id"] = self.tools[idx]["id"]
        current["order"] = self.tools[idx].get("order", idx)
        existing = {t.get("id", "") for i, t in enumerate(self.tools) if i != idx}
        self.tools[idx] = normalize_tool(current, idx, existing)
        self._renumber()
        if save:
            self.save()
        return dict(self.tools[idx])

    def remove_tool(self, tool_id: str, *, save: bool = True) -> bool:
        idx = self._index_of(tool_id)
        if idx < 0:
            return False
        del self.tools[idx]
        self._renumber()
        if save:
            self.save()
        return True

    def duplicate_tool(self, tool_id: str, *, save: bool = True) -> Dict[str, Any] | None:
        idx = self._index_of(tool_id)
        if idx < 0:
            return None
        duplicate = deepcopy(self.tools[idx])
        duplicate.pop("id", None)
        duplicate["label"] = f"{duplicate.get('label', 'Tool')} Copy"
        duplicate["order"] = idx + 1
        existing = {t.get("id", "") for t in self.tools}
        normalized = normalize_tool(duplicate, idx + 1, existing)
        self.tools.insert(idx + 1, normalized)
        self._renumber()
        if save:
            self.save()
        return dict(normalized)

    def move_tool(self, tool_id: str, delta: int, *, save: bool = True) -> bool:
        idx = self._index_of(tool_id)
        if idx < 0:
            return False
        target = max(0, min(len(self.tools) - 1, idx + int(delta)))
        if target == idx:
            return False
        tool = self.tools.pop(idx)
        self.tools.insert(target, tool)
        self._renumber()
        if save:
            self.save()
        return True

    def move_tool_to_index(self, tool_id: str, target_index: int, *, save: bool = True) -> bool:
        idx = self._index_of(tool_id)
        if idx < 0:
            return False
        try:
            requested = int(target_index)
        except Exception:
            return False
        tool = self.tools.pop(idx)
        target = max(0, min(len(self.tools), requested))
        if target == idx:
            self.tools.insert(idx, tool)
            return False
        self.tools.insert(target, tool)
        self._renumber()
        if save:
            self.save()
        return True

    def tool_by_id(self, tool_id: str) -> Dict[str, Any] | None:
        idx = self._index_of(tool_id)
        if idx < 0:
            return None
        return dict(self.tools[idx])

    def _index_of(self, tool_id: str) -> int:
        needle = str(tool_id or "")
        for idx, tool in enumerate(self.tools):
            if str(tool.get("id", "")) == needle:
                return idx
        return -1

    def _renumber(self) -> None:
        for idx, tool in enumerate(self.tools):
            tool["order"] = idx
