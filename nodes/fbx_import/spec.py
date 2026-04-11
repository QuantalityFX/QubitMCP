from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

try:
    from PySide6 import QtWidgets, QtGui
except Exception:
    try:
        from PySide2 import QtWidgets, QtGui  # type: ignore
    except Exception:
        QtWidgets = None  # type: ignore[assignment]
        QtGui = None  # type: ignore[assignment]

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

ROLE_PORTS: Tuple[str, str, str] = ("rest_geometry", "capture_pose", "animated_pose")
_INTERNAL_PARAM_NAMES = (
    "resolved_rest_geometry",
    "resolved_capture_pose",
    "resolved_animated_pose",
    "source_validation_state",
    "source_validation_messages",
    "source_validation_warnings",
    "source_validation_errors",
)


@dataclass
class SourceResolutionResult:
    status: str
    requested_sources: Dict[str, str] = field(default_factory=dict)
    effective_sources: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def message_lines(self) -> List[str]:
        lines = [f"Status: {self.status.upper()}"]
        for role in ROLE_PORTS:
            raw = (self.requested_sources.get(role) or "").strip()
            resolved = (self.effective_sources.get(role) or "").strip()
            if resolved:
                if raw and raw != resolved:
                    lines.append(f"{role}: {resolved} (requested: {raw})")
                else:
                    lines.append(f"{role}: {resolved}")
            elif raw:
                lines.append(f"{role}: {raw} (unresolved)")
            else:
                lines.append(f"{role}: <none>")
        if self.errors:
            lines.append("Errors:")
            lines.extend(f"- {msg}" for msg in self.errors)
        if self.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {msg}" for msg in self.warnings)
        return lines


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return (entry.get("value") or "").strip()
    return ""


def _set_param_value(model, name: str, value: str) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            entry["value"] = value
            model.params = params
            return
    params.append({"name": name, "value": value})
    model.params = params


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            model.params = params
            return
    params.append({"name": name, "value": default})
    model.params = params


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    hidden_entry = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == store_key:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": store_key, "value": ""}
        params.append(hidden_entry)

    raw = hidden_entry.get("value", "")
    hidden = {tok.strip().lower() for tok in str(raw).split(",") if tok.strip()}
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _ensure_input(node_item, name: str) -> None:
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input(name)
    elif hasattr(node_item, "add_input_port"):
        node_item.add_input_port(name)
    elif hasattr(node_item, "add_input"):
        node_item.add_input(name)


def build_ports(node_item) -> None:
    for role in ROLE_PORTS:
        _ensure_param(node_item, role, "")
        _ensure_input(node_item, role)
    for internal_name in _INTERNAL_PARAM_NAMES:
        _ensure_param(node_item, internal_name, "")
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [*ROLE_PORTS, *_INTERNAL_PARAM_NAMES],
    )


def _ordered_in_edges(scene, item):
    try:
        return list(scene._ordered_in_edges(item))
    except Exception:
        try:
            return list(scene._in_edges(item))
        except Exception:
            return []


def _edge_dst_name(edge) -> str:
    return (
        (getattr(edge, "dst_port_name", None) or "")
        or (getattr(edge, "dst_label", None) or "")
        or (getattr(edge, "dst_name", None) or "")
    ).strip()


def _workflow_dir_for_node(node_item) -> Path | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None

    scene_path = getattr(scene, "_filename", None) if scene is not None else None
    workflow_path = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                workflow_path = getattr(win, "_current_path", None)
        except Exception:
            pass

    workflow_path = workflow_path or scene_path
    if not workflow_path:
        return None
    try:
        return Path(workflow_path).parent
    except Exception:
        return None


def _resolve_existing_path(raw_path: str, base_dir: Path | None) -> Path | None:
    if not raw_path:
        return None
    try:
        path = Path(raw_path).expanduser()
    except Exception:
        return None

    try:
        if path.exists():
            return path.resolve()
    except Exception:
        pass

    if base_dir is None or path.is_absolute():
        return None
    try:
        alt = (base_dir / path).resolve()
        if alt.exists():
            return alt
    except Exception:
        return None
    return None


def _trace_source_item(scene, src_item):
    visited = set()
    current = src_item
    depth = 0
    while current is not None and depth < 8:
        cur_id = id(current)
        if cur_id in visited:
            return None, "source trace cycle detected."
        visited.add(cur_id)
        depth += 1

        model = getattr(current, "model", None)
        kind = (getattr(model, "kind", "") or "").strip().lower() if model is not None else ""
        if kind != "switch":
            return current, ""

        upstream = _ordered_in_edges(scene, current)
        if not upstream:
            return None, "switch input is not connected."
        current = getattr(upstream[0], "src", None)
    if depth >= 8:
        return None, "source trace exceeded maximum depth."
    return current, ""


def _source_path_from_item(src_item, role: str) -> Tuple[str, str]:
    model = getattr(src_item, "model", None)
    if model is None:
        return "", "connected source has no model."

    kind = (getattr(model, "kind", "") or "").strip().lower()
    name = (getattr(model, "name", "") or "").strip() or "<unnamed>"

    if kind in ("import", "html_preview"):
        path = _param_value(model, "path")
        if not path:
            return "", f"{role}: source node '{name}' has empty path."
        return path, ""

    if kind in ("fbx_import", "fbx import", "fbximport"):
        path = _param_value(model, f"resolved_{role}") or _param_value(model, role)
        if not path:
            return "", f"{role}: upstream FBXImport node '{name}' has no resolved source."
        return path, ""

    return "", f"{role}: source node '{name}' has unsupported kind '{kind}'."


def _validate_fbx_path(role: str, raw_path: str, base_dir: Path | None) -> Tuple[str, str]:
    if not raw_path:
        return "", f"{role}: source is empty."
    path = _resolve_existing_path(raw_path, base_dir)
    if path is None:
        return "", f"{role}: file does not exist: {raw_path}"
    if path.suffix.lower() != ".fbx":
        return "", f"{role}: file is not .fbx: {path}"
    return str(path), ""


def resolve_fbx_import_sources(
    node_item,
    *,
    base_dir: Path | None = None,
    persist: bool = False,
) -> SourceResolutionResult:
    model = getattr(node_item, "model", None)
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None

    if base_dir is None:
        base_dir = _workflow_dir_for_node(node_item)

    requested = {role: "" for role in ROLE_PORTS}
    effective = {role: "" for role in ROLE_PORTS}
    errors: List[str] = []
    warnings: List[str] = []

    for role in ROLE_PORTS:
        role_edges = []
        if scene is not None:
            for edge in _ordered_in_edges(scene, node_item):
                if _edge_dst_name(edge).lower() == role:
                    role_edges.append(edge)

        wired_path = ""
        if role_edges:
            if len(role_edges) > 1:
                warnings.append(
                    f"{role}: multiple inputs connected; using first edge deterministically."
                )
            src = getattr(role_edges[0], "src", None)
            src_item, trace_issue = _trace_source_item(scene, src)
            if trace_issue:
                warnings.append(f"{role}: {trace_issue}")
            if src_item is not None:
                wired_path, source_issue = _source_path_from_item(src_item, role)
                if source_issue:
                    warnings.append(source_issue)

        param_path = _param_value(model, role)
        if wired_path:
            requested[role] = wired_path
        else:
            requested[role] = param_path
            if role_edges and param_path:
                warnings.append(
                    f"{role}: wired source unresolved; using parameter fallback."
                )

    rest_resolved, rest_issue = _validate_fbx_path(
        "rest_geometry", requested["rest_geometry"], base_dir
    )
    if rest_issue:
        errors.append(rest_issue)
    else:
        effective["rest_geometry"] = rest_resolved

    for role in ("capture_pose", "animated_pose"):
        raw = requested[role]
        if not raw:
            continue
        resolved, issue = _validate_fbx_path(role, raw, base_dir)
        if issue:
            warnings.append(f"{issue}; override ignored.")
        else:
            effective[role] = resolved

    if not effective["rest_geometry"]:
        if effective["capture_pose"] or effective["animated_pose"]:
            warnings.append(
                "capture_pose/animated_pose overrides were ignored because rest_geometry is invalid."
            )
        effective["capture_pose"] = ""
        effective["animated_pose"] = ""
    else:
        for role in ("capture_pose", "animated_pose"):
            if not effective[role]:
                effective[role] = effective["rest_geometry"]

    status = "error" if errors else ("warning" if warnings else "ok")
    result = SourceResolutionResult(
        status=status,
        requested_sources=requested,
        effective_sources=effective,
        errors=errors,
        warnings=warnings,
    )

    if persist and model is not None:
        _set_param_value(model, "resolved_rest_geometry", effective["rest_geometry"])
        _set_param_value(model, "resolved_capture_pose", effective["capture_pose"])
        _set_param_value(model, "resolved_animated_pose", effective["animated_pose"])
        _set_param_value(model, "source_validation_state", status)
        _set_param_value(model, "source_validation_errors", "\n".join(errors))
        _set_param_value(model, "source_validation_warnings", "\n".join(warnings))
        _set_param_value(model, "source_validation_messages", "\n".join(result.message_lines()))

    return result


def _compact_source_line(role: str, path_value: str) -> str:
    raw = (path_value or "").strip()
    if not raw:
        return f"{role}: <none>"
    try:
        name = Path(raw).name
    except Exception:
        name = raw
    return f"{role}: {name}"


def augment_infocard_footer(card, footer_layout) -> bool:
    if QtWidgets is None or QtGui is None:
        return False

    node = getattr(card, "_node_ref", None)
    scene = getattr(card, "_graph_scene", None)
    if node is None or scene is None:
        return False

    kind = (getattr(node, "kind", "") or "").strip().lower()
    if kind not in ("fbx_import", "fbx import", "fbximport"):
        return False

    def _node_item():
        try:
            return scene._node_items.get(node.name)
        except Exception:
            return None

    status_label = QtWidgets.QLabel("Status: unresolved")
    status_label.setStyleSheet("color:#94a3b8;")
    detail_label = QtWidgets.QLabel("")
    detail_label.setWordWrap(True)
    detail_label.setStyleSheet("color:#cbd5e1;")

    button = QtWidgets.QPushButton("Validate FBX Sources")
    button.setToolTip("Resolve rest/capture/animated source roles and validate compatibility.")

    footer_layout.addWidget(status_label)
    footer_layout.addWidget(detail_label)
    footer_layout.addWidget(button)

    def _refresh(*_args, persist: bool = False, toast: bool = False):
        item = _node_item()
        if item is None:
            status_label.setText("Status: no node item")
            status_label.setStyleSheet("color:#f59e0b;")
            detail_label.setText("Connect this node to the graph canvas.")
            return

        result = resolve_fbx_import_sources(item, persist=persist)
        status_text = result.status.upper()
        if result.status == "ok":
            status_label.setStyleSheet("color:#22c55e;")
        elif result.status == "warning":
            status_label.setStyleSheet("color:#f59e0b;")
        else:
            status_label.setStyleSheet("color:#ef4444;")
        status_label.setText(f"Status: {status_text}")

        detail_lines = [
            _compact_source_line("rest", result.effective_sources.get("rest_geometry", "")),
            _compact_source_line("capture", result.effective_sources.get("capture_pose", "")),
            _compact_source_line("animated", result.effective_sources.get("animated_pose", "")),
        ]
        detail_label.setText(" | ".join(detail_lines))
        detail_label.setToolTip("\n".join(result.message_lines()))

        if toast:
            report = "\n".join(result.message_lines())
            if result.status == "error":
                QtWidgets.QMessageBox.warning(card, "FBXImport Validation", report)
            else:
                QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), report, card)

    def _on_validate_clicked():
        _refresh(persist=True, toast=True)

    button.clicked.connect(_on_validate_clicked)

    def _on_links_changed(*_args):
        _refresh(persist=False, toast=False)

    def _on_param_changed(changed_name, *_args):
        item = _node_item()
        if item is None:
            return
        if _param_change_relevant(item, changed_name):
            _refresh(persist=False, toast=False)

    try:
        scene.linksChanged.connect(_on_links_changed)
    except Exception:
        pass
    try:
        scene.paramChanged.connect(_on_param_changed)
    except Exception:
        pass

    _refresh(persist=False, toast=False)
    return True


FBX_IMPORT_SPEC = Spec(
    stripe_color="#2563eb",
    augment_infocard_footer=augment_infocard_footer,
    build_ports=build_ports,
)


__all__ = [
    "ROLE_PORTS",
    "SourceResolutionResult",
    "build_ports",
    "resolve_fbx_import_sources",
    "augment_infocard_footer",
    "FBX_IMPORT_SPEC",
]
