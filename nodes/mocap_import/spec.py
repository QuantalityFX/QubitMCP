from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from PySide6 import QtWidgets, QtGui
except Exception:
    try:
        from PySide2 import QtWidgets, QtGui  # type: ignore
    except Exception:
        QtWidgets = None  # type: ignore[assignment]
        QtGui = None  # type: ignore[assignment]

from echograph.rigging.bvh_ingest import BVHIngestError, ingest_bvh_animation_data
from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

MOCAP_KIND_ALIASES: Tuple[str, ...] = (
    "mocap_import",
    "mocap import",
    "mocapimport",
    "bvh_import",
    "bvh import",
    "bvhimport",
)


@dataclass
class MocapSourceResolutionResult:
    status: str
    requested_path: str = ""
    resolved_path: str = ""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    summary: str = ""

    def message_lines(self) -> List[str]:
        lines = [f"Status: {self.status.upper()}"]
        if self.resolved_path:
            if self.requested_path and self.requested_path != self.resolved_path:
                lines.append(f"path: {self.resolved_path} (requested: {self.requested_path})")
            else:
                lines.append(f"path: {self.resolved_path}")
        elif self.requested_path:
            lines.append(f"path: {self.requested_path} (unresolved)")
        else:
            lines.append("path: <none>")
        if self.summary:
            lines.append(self.summary)
        if self.errors:
            lines.append("Errors:")
            lines.extend(f"- {msg}" for msg in self.errors)
        if self.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {msg}" for msg in self.warnings)
        return lines


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
        p = Path(workflow_path)
        return p.parent if p.suffix else p
    except Exception:
        return None


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return (entry.get("value") or "").strip()
    return ""


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        try:
            setattr(model, "params", params)
        except Exception:
            return
    if not isinstance(params, list):
        try:
            params = list(params)
        except Exception:
            params = []
        setattr(model, "params", params)

    key = (name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _set_param_value(model, name: str, value: str) -> None:
    if model is None:
        return
    key = (name or "").strip().lower()
    params = list(getattr(model, "params", None) or [])
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value or "")
            model.params = params
            return
    params.append({"name": name, "value": str(value or "")})
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


def _summary_from_result(animation_result) -> str:
    if animation_result is None:
        return "mocap: <unvalidated>"
    skeleton = getattr(animation_result, "skeleton", None)
    clips = list(getattr(animation_result, "clips", []) or [])
    joint_count = len(getattr(skeleton, "joints", []) or []) if skeleton is not None else 0
    if not clips:
        return f"mocap: joints={joint_count} clips=0"
    clip = clips[0]
    frame_count = int((getattr(clip, "metadata", {}) or {}).get("frame_count", 0) or 0)
    frame_time = float((getattr(clip, "metadata", {}) or {}).get("frame_time", 0.0) or 0.0)
    return (
        f"mocap: joints={joint_count} clips={len(clips)}"
        f" first={getattr(clip, 'name', 'clip')}"
        f" frames={frame_count} frame_time={frame_time:.6f}"
    )


def _param_bool(model, name: str, default: bool = False) -> bool:
    raw = _param_value(model, name)
    text = str(raw or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _compact_path_line(path_value: str) -> str:
    raw = (path_value or "").strip()
    if not raw:
        return "path: <none>"
    try:
        return f"path: {Path(raw).name}"
    except Exception:
        return f"path: {raw}"


def _clip_summary_text(animation_result) -> str:
    if animation_result is None:
        return "clips: <unavailable>"
    try:
        skeleton = getattr(animation_result, "skeleton", None)
        joint_count = len(getattr(skeleton, "joints", []) or []) if skeleton is not None else 0
    except Exception:
        joint_count = 0
    try:
        clips = list(getattr(animation_result, "clips", []) or [])
    except Exception:
        clips = []
    if not clips:
        return f"joints: {joint_count} | clips: 0"
    clip = clips[0]
    try:
        first_name = str(getattr(clip, "name", "") or "").strip() or "clip_0"
    except Exception:
        first_name = "clip_0"
    try:
        start = float(getattr(clip, "start_time", 0.0) or 0.0)
    except Exception:
        start = 0.0
    try:
        end = float(getattr(clip, "end_time", start) or start)
    except Exception:
        end = start
    try:
        track_count = int(len(getattr(clip, "tracks", []) or []))
    except Exception:
        track_count = 0
    return (
        f"joints: {joint_count}"
        f" | clips: {len(clips)}"
        f" | first: {first_name}"
        f" [{start:.3f}s-{end:.3f}s]"
        f" tracks={track_count}"
    )


def _build_preview_asset(model, result: MocapSourceResolutionResult) -> Dict[str, Any] | None:
    path_text = str(getattr(result, "resolved_path", "") or "").strip()
    if not path_text:
        return None
    try:
        path_obj = Path(path_text)
    except Exception:
        return None
    if path_obj.suffix.lower() != ".bvh":
        return None

    animation_result = getattr(model, "_mocap_animation_result", None)
    try:
        existing_source = str(getattr(animation_result, "source_path", "") or "").strip()
        if existing_source and path_obj.exists():
            try:
                if Path(existing_source).resolve() != path_obj.resolve():
                    animation_result = None
            except Exception:
                pass
    except Exception:
        animation_result = None

    if animation_result is None:
        try:
            animation_result = ingest_bvh_animation_data(path_obj)
        except Exception:
            return None
        try:
            setattr(model, "_mocap_animation_result", animation_result)
            setattr(model, "_mocap_skeleton", getattr(animation_result, "skeleton", None))
            clips = list(getattr(animation_result, "clips", []) or [])
            setattr(model, "_mocap_clip", clips[0] if clips else None)
        except Exception:
            pass

    skeleton = getattr(animation_result, "skeleton", None)
    if skeleton is None:
        return None
    try:
        clips = list(getattr(animation_result, "clips", []) or [])
    except Exception:
        clips = []
    clip = clips[0] if clips else None
    owner = str(getattr(model, "name", "") or "").strip()
    if not owner:
        owner = path_obj.stem or "mocap_import"
    debug_log = _param_bool(model, "debug_log", default=False)
    context = {
        "skeleton": skeleton,
        "clip": clip,
        "meshes": [],
        "loop": True,
        "mesh_skinning_enabled": False,
        "skin_weight_debug": False,
        "show_capture_joints": False,
        "show_animated_joints": True,
        "fbx_debug_log": bool(debug_log),
        "source_format": "bvh",
    }
    return {
        "path": str(path_obj),
        "texture": "",
        "node": owner,
        "kind": "mocap",
        "ext": ".bvh",
        "visible": True,
        "fbx_rig_context": context,
        "debug_log": bool(debug_log),
        "fbx_debug_log": bool(debug_log),
    }


def build_ports(node_item) -> None:
    _ensure_param(node_item, "path", "")
    _ensure_param(node_item, "resolved_path", "")
    _ensure_param(node_item, "debug_log", "0")
    _ensure_hidden_params(getattr(node_item, "model", None), ["resolved_path", "debug_log"])


def resolve_mocap_import_source(
    node_item,
    *,
    base_dir: Path | None = None,
    persist: bool = False,
    validate_animation: bool = False,
) -> MocapSourceResolutionResult:
    model = getattr(node_item, "model", None)
    if base_dir is None:
        base_dir = _workflow_dir_for_node(node_item)

    requested_path = _param_value(model, "path")
    errors: List[str] = []
    warnings: List[str] = []
    resolved_path = ""
    animation_result = None

    if not requested_path:
        errors.append("path: source is empty.")
    else:
        path_obj = _resolve_existing_path(requested_path, base_dir)
        if path_obj is None:
            errors.append(f"path: file does not exist: {requested_path}")
        elif path_obj.suffix.lower() != ".bvh":
            errors.append(f"path: file is not .bvh: {path_obj}")
        else:
            resolved_path = str(path_obj)

    if validate_animation and resolved_path:
        try:
            animation_result = ingest_bvh_animation_data(resolved_path)
        except BVHIngestError as exc:
            errors.append(f"path: BVH ingest failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"path: BVH ingest failed: {exc}")
        else:
            for msg in list(getattr(animation_result, "warnings", []) or []):
                text = str(msg or "").strip()
                if text:
                    warnings.append(text)

    summary = _summary_from_result(animation_result) if validate_animation else ""
    status = "error" if errors else ("warning" if warnings else "ok")
    result = MocapSourceResolutionResult(
        status=status,
        requested_path=requested_path,
        resolved_path=resolved_path,
        errors=errors,
        warnings=warnings,
        summary=summary,
    )

    if persist and model is not None:
        try:
            setattr(model, "_mocap_resolved_path", resolved_path)
            setattr(model, "_mocap_validation_state", status)
            setattr(model, "_mocap_validation_errors", list(errors))
            setattr(model, "_mocap_validation_warnings", list(warnings))
            setattr(model, "_mocap_validation_messages", list(result.message_lines()))
            setattr(model, "_mocap_animation_result", animation_result)
            if animation_result is not None:
                setattr(model, "_mocap_skeleton", getattr(animation_result, "skeleton", None))
                clips = list(getattr(animation_result, "clips", []) or [])
                setattr(model, "_mocap_clip", clips[0] if clips else None)
            _set_param_value(model, "resolved_path", resolved_path)
        except Exception:
            pass

    return result


def augment_infocard_footer(card, footer_layout) -> bool:
    if QtWidgets is None or QtGui is None:
        return False

    node = getattr(card, "_node_ref", None)
    scene = getattr(card, "_graph_scene", None)
    if node is None or scene is None:
        return False
    kind = (getattr(node, "kind", "") or "").strip().lower()
    if kind not in MOCAP_KIND_ALIASES:
        return False

    _ensure_hidden_params(node, ["resolved_path", "debug_log"])

    def _node_item():
        try:
            return scene._node_items.get(node.name)
        except Exception:
            return None

    status_label = QtWidgets.QLabel("Status: unresolved")
    status_label.setStyleSheet("color:#94a3b8;")
    detail_box = QtWidgets.QPlainTextEdit("")
    detail_box.setReadOnly(True)
    detail_box.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
    detail_box.setMinimumHeight(96)
    try:
        detail_box.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
    except Exception:
        pass
    detail_box.setStyleSheet(
        "QPlainTextEdit {"
        "color:#cbd5e1;"
        "background:#0f172a;"
        "border:1px solid #1e293b;"
        "border-radius:4px;"
        "padding:6px;"
        "}"
    )
    try:
        detail_box.setPlaceholderText("BVH validation details will appear here.")
    except Exception:
        pass

    browse_button = QtWidgets.QPushButton("Browse BVH")
    browse_button.setToolTip("Select a BVH mocap file.")
    validate_button = QtWidgets.QPushButton("Validate BVH")
    validate_button.setToolTip("Parse the BVH hierarchy and motion data.")
    view_button = QtWidgets.QPushButton("View")
    view_button.setToolTip("Open the BVH skeleton animation in the 3D viewport.")
    copy_button = QtWidgets.QPushButton("Copy Report")
    copy_button.setToolTip("Copy the BVH validation report to clipboard.")

    container = QtWidgets.QWidget(card)
    try:
        container.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Preferred,
        )
    except Exception:
        pass
    stack = QtWidgets.QVBoxLayout(container)
    stack.setContentsMargins(0, 0, 0, 0)
    stack.setSpacing(6)

    button_row = QtWidgets.QHBoxLayout()
    button_row.setContentsMargins(0, 0, 0, 0)
    button_row.setSpacing(8)
    button_row.addWidget(browse_button)
    button_row.addWidget(validate_button)
    button_row.addWidget(view_button)
    button_row.addWidget(copy_button)
    button_row.addStretch(1)

    stack.addLayout(button_row)
    stack.addWidget(status_label)
    stack.addWidget(detail_box, 1)
    insert_idx = footer_layout.count()
    footer_layout.addWidget(container, 100)
    try:
        footer_layout.setStretch(insert_idx, 100)
    except Exception:
        pass
    report_holder: Dict[str, str] = {"value": ""}

    def _refresh(*_args, persist: bool = False, validate: bool = False, toast: bool = False):
        item = _node_item()
        if item is None:
            status_label.setText("Status: no node item")
            status_label.setStyleSheet("color:#f59e0b;")
            detail_box.setPlainText("Connect this node to the graph canvas.")
            return None
        result = resolve_mocap_import_source(
            item,
            persist=bool(persist),
            validate_animation=bool(validate),
        )
        if result.status == "error":
            color = "#ef4444"
            label = "ERROR"
        elif result.status == "warning":
            color = "#f59e0b"
            label = "WARNING"
        else:
            color = "#22c55e" if validate else "#94a3b8"
            label = "OK" if validate else "PATH OK"
        status_label.setText(f"Status: {label}")
        status_label.setStyleSheet(f"color:{color};")

        model_obj = getattr(item, "model", None)
        detail_lines = [_compact_path_line(result.resolved_path or result.requested_path)]
        animation_result = getattr(model_obj, "_mocap_animation_result", None)
        if validate or animation_result is not None:
            detail_lines.append(_clip_summary_text(animation_result))
        elif result.status != "error":
            detail_lines.append("mocap: <unvalidated>")
        if result.errors:
            detail_lines.append(f"errors: {len(result.errors)}")
        if result.warnings:
            detail_lines.append(f"warnings: {len(result.warnings)}")
        detail_box.setPlainText("\n".join(detail_lines))

        report = "\n".join(result.message_lines())
        report_holder["value"] = report
        tooltip_lines = []
        if not validate and not result.errors:
            tooltip_lines.append("Animation ingest not run yet. Click 'Validate BVH'.")
        tooltip_lines.extend(result.message_lines())
        detail_box.setToolTip("\n".join(tooltip_lines))

        if toast:
            if result.status in ("error", "warning"):
                QtWidgets.QMessageBox.warning(card, "Mocap Import Validation", report)
            else:
                try:
                    QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), report, card)
                except Exception:
                    pass
        return result

    def _set_node_param(name: str, value: str) -> None:
        item = _node_item()
        if item is None:
            return
        model_obj = getattr(item, "model", None)
        setter = getattr(item, "_set_param_value", None)
        setter_ok = False
        if callable(setter):
            try:
                setter(name, value, rebuild=False, notify_scene=True)
                setter_ok = True
            except TypeError:
                try:
                    setter(name, value)
                    setter_ok = True
                except Exception:
                    pass
            except Exception:
                pass
        _set_param_value(model_obj, name, value)
        try:
            if model_obj is not None and hasattr(scene, "paramChanged") and not setter_ok:
                scene.paramChanged.emit(
                    getattr(model_obj, "name", "") or "",
                    list(getattr(model_obj, "params", None) or []),
                )
        except Exception:
            pass

    def _browse() -> None:
        start_dir = ""
        item = _node_item()
        model = getattr(item, "model", None) if item is not None else None
        raw = _param_value(model, "path")
        if raw:
            try:
                start_dir = str(Path(raw).expanduser().parent)
            except Exception:
                start_dir = ""
        path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            card,
            "Select BVH Mocap File",
            start_dir,
            "BVH Motion (*.bvh);;All Files (*.*)",
        )
        if not path or item is None:
            return
        _set_node_param("path", path)
        _refresh(persist=True, validate=True, toast=False)

    def _copy_report() -> None:
        text = str(report_holder.get("value") or "").strip()
        if not text:
            text = str(detail_box.toPlainText() or "").strip()
        if not text:
            return
        try:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
        except Exception:
            pass
        try:
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "BVH report copied.", card)
        except Exception:
            pass

    def _on_view_clicked() -> None:
        item = _node_item()
        if item is None:
            QtWidgets.QMessageBox.warning(card, "Mocap Import View", "Node item is not available.")
            return
        result = _refresh(persist=True, validate=True, toast=False)
        if result is None:
            return
        if result.status == "error":
            QtWidgets.QMessageBox.warning(
                card,
                "Mocap Import View",
                "\n".join(result.message_lines()),
            )
            return

        model_obj = getattr(item, "model", None)
        asset = _build_preview_asset(model_obj, result)
        if not isinstance(asset, dict):
            QtWidgets.QMessageBox.warning(
                card,
                "Mocap Import View",
                "BVH skeleton animation is not available. Validate the source first.",
            )
            return

        win = card.window()
        try:
            glv = getattr(win, "gl_view", None) if win is not None else None
            path_text = str(asset.get("path") or "").strip()
            context_obj = asset.get("fbx_rig_context")
            if glv is not None and path_text and isinstance(context_obj, dict):
                path_obj = Path(path_text)
                try:
                    cache_key = str(path_obj.resolve())
                except Exception:
                    cache_key = str(path_obj)
                try:
                    mtime = float(path_obj.stat().st_mtime)
                except Exception:
                    mtime = None
                cache = getattr(glv, "_mgl_fbx_rig_context_cache", None)
                if not isinstance(cache, dict):
                    cache = {}
                cache_entry = {"mtime": mtime, "context": dict(context_obj)}
                cache[cache_key] = cache_entry
                cache[str(path_obj)] = cache_entry
                setattr(glv, "_mgl_fbx_rig_context_cache", cache)
        except Exception:
            pass

        opened = False
        scene_handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if callable(scene_handler):
            try:
                scene_handler([asset], frame=True)
                opened = True
            except Exception:
                opened = False
        if not opened:
            model_handler = getattr(win, "open_3d_model", None) if win is not None else None
            if callable(model_handler):
                try:
                    model_handler(str(asset.get("path") or ""), None, frame=True)
                    opened = True
                except Exception:
                    opened = False
        if not opened:
            QtWidgets.QMessageBox.warning(card, "Mocap Import View", "3D view is not available.")
            return

        try:
            glv = getattr(win, "gl_view", None) if win is not None else None
            if glv is not None:
                toggle = getattr(glv, "_mgl_wireframe_toggle", None)
                if toggle is not None and not bool(toggle.isChecked()):
                    toggle.setChecked(True)
        except Exception:
            pass

    browse_button.clicked.connect(_browse)
    validate_button.clicked.connect(lambda: _refresh(persist=True, validate=True, toast=True))
    view_button.clicked.connect(_on_view_clicked)
    copy_button.clicked.connect(_copy_report)

    def _on_param_changed(changed_name, *_args):
        item = _node_item()
        if item is None:
            return
        if _param_change_relevant(item, changed_name):
            _refresh(persist=False, validate=False)

    if scene is not None:
        try:
            scene.paramChanged.connect(_on_param_changed)
        except Exception:
            pass

    _refresh(persist=False, validate=False, toast=False)
    return True


MOCAP_IMPORT_SPEC = Spec(
    stripe_color="#7c3aed",
    augment_infocard_footer=augment_infocard_footer,
    build_ports=build_ports,
)


__all__ = [
    "MOCAP_KIND_ALIASES",
    "MocapSourceResolutionResult",
    "build_ports",
    "resolve_mocap_import_source",
    "augment_infocard_footer",
    "MOCAP_IMPORT_SPEC",
]
