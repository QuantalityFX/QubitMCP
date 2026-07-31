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

from echograph.rigging.fbx_stage3_ingest import (
    FBXBindIngestError,
    ingest_fbx_bind_data,
)
from echograph.rigging.fbx_stage4_animation import (
    FBXAnimationIngestError,
    ingest_fbx_animation_data,
)
from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

FBX_ANIMATION_KIND_ALIASES: Tuple[str, ...] = (
    "fbx_animation",
    "fbx animation",
    "fbxanimation",
    "fbx_animation_import",
    "fbx animation import",
    "fbxanimationimport",
)


@dataclass
class FBXAnimationSourceResolutionResult:
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


def _remove_named_output(node_item, name: str) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    key = str(name or "").strip().lower()
    try:
        outputs = list(getattr(model, "_named_outputs", None) or [])
    except Exception:
        outputs = []
    filtered = [str(value) for value in outputs if str(value).strip().lower() != key]
    try:
        setattr(model, "_named_outputs", filtered)
    except Exception:
        pass


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


def _append_result_warnings(warnings: List[str], source_result, prefix: str = "") -> None:
    for msg in list(getattr(source_result, "warnings", []) or []):
        text = str(msg or "").strip()
        if prefix == "skeleton: " and (
            text.startswith("No mesh bones were found; using node hierarchy")
            or text.startswith("Source inverse bind matrices were missing; rebuilt")
            or text.startswith("Source inverse bind matrices were identity-only; rebuilt")
        ):
            continue
        if text:
            warnings.append(f"{prefix}{text}" if prefix else text)


def _summary_from_results(bind_result, animation_result) -> str:
    skeleton = getattr(bind_result, "skeleton", None) if bind_result is not None else None
    joint_count = len(getattr(skeleton, "joints", []) or []) if skeleton is not None else 0
    mesh_count = len(getattr(bind_result, "meshes", []) or []) if bind_result is not None else 0
    try:
        clips = list(getattr(animation_result, "clips", []) or [])
    except Exception:
        clips = []
    if not clips:
        return f"fbx animation: joints={joint_count} meshes_ignored={mesh_count} clips=0"
    clip = clips[0]
    return (
        f"fbx animation: joints={joint_count} meshes_ignored={mesh_count}"
        f" clips={len(clips)} first={getattr(clip, 'name', 'clip')}"
    )


def _clip_summary_text(bind_result, animation_result) -> str:
    skeleton = getattr(bind_result, "skeleton", None) if bind_result is not None else None
    joint_count = len(getattr(skeleton, "joints", []) or []) if skeleton is not None else 0
    mesh_count = len(getattr(bind_result, "meshes", []) or []) if bind_result is not None else 0
    try:
        clips = list(getattr(animation_result, "clips", []) or [])
    except Exception:
        clips = []
    if not clips:
        return f"joints: {joint_count} | meshes ignored: {mesh_count} | clips: 0"
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
        f" | meshes ignored: {mesh_count}"
        f" | clips: {len(clips)}"
        f" | first: {first_name}"
        f" [{start:.3f}s-{end:.3f}s]"
        f" tracks={track_count}"
    )


def _compact_path_line(path_value: str) -> str:
    raw = (path_value or "").strip()
    if not raw:
        return "path: <none>"
    try:
        return f"path: {Path(raw).name}"
    except Exception:
        return f"path: {raw}"


def _cached_results_match_path(model, path_obj: Path) -> bool:
    animation_result = getattr(model, "_fbx_animation_result", None)
    bind_result = getattr(model, "_fbx_animation_bind_result", None)
    for result in (animation_result, bind_result):
        if result is None:
            continue
        try:
            existing_source = str(getattr(result, "source_path", "") or "").strip()
            if existing_source:
                if Path(existing_source).resolve() != path_obj.resolve():
                    return False
        except Exception:
            return False
    return True


def _build_preview_asset(model, result: FBXAnimationSourceResolutionResult) -> Dict[str, Any] | None:
    path_text = str(getattr(result, "resolved_path", "") or "").strip()
    if not path_text:
        return None
    try:
        path_obj = Path(path_text)
    except Exception:
        return None
    if path_obj.suffix.lower() != ".fbx":
        return None

    bind_result = getattr(model, "_fbx_animation_bind_result", None)
    animation_result = getattr(model, "_fbx_animation_result", None)
    if not _cached_results_match_path(model, path_obj):
        bind_result = None
        animation_result = None

    if bind_result is None:
        try:
            bind_result = ingest_fbx_bind_data(path_obj)
        except Exception:
            return None
        try:
            setattr(model, "_fbx_animation_bind_result", bind_result)
            setattr(model, "_fbx_animation_skeleton", getattr(bind_result, "skeleton", None))
        except Exception:
            pass

    skeleton = getattr(bind_result, "skeleton", None)
    if skeleton is None:
        return None

    if animation_result is None:
        try:
            animation_result = ingest_fbx_animation_data(path_obj, skeleton=skeleton)
        except Exception:
            animation_result = None
        try:
            setattr(model, "_fbx_animation_result", animation_result)
            clips_for_attr = list(getattr(animation_result, "clips", []) or []) if animation_result is not None else []
            setattr(model, "_fbx_animation_clip", clips_for_attr[0] if clips_for_attr else None)
        except Exception:
            pass

    try:
        clips = list(getattr(animation_result, "clips", []) or [])
    except Exception:
        clips = []
    clip = clips[0] if clips else None
    owner = str(getattr(model, "name", "") or "").strip()
    if not owner:
        owner = path_obj.stem or "fbx_animation"
    debug_log = _param_bool(model, "debug_log", default=False)
    context = {
        "skeleton": skeleton,
        "clip": clip,
        "meshes": [],
        "loop": bool(clip is not None),
        "mesh_skinning_enabled": False,
        "preview_bind_geometry_only": bool(clip is None),
        "skin_weight_debug": False,
        "show_capture_joints": False,
        "show_animated_joints": True,
        "fbx_debug_log": bool(debug_log),
        "source_format": "fbx_animation",
    }
    return {
        "path": str(path_obj),
        "texture": "",
        "node": owner,
        "kind": "fbx_animation",
        "ext": ".fbx",
        "visible": True,
        "wire_only": True,
        "has_skeleton": True,
        "fbx_rig_context": context,
        "debug_log": bool(debug_log),
        "fbx_debug_log": bool(debug_log),
    }


def build_fbx_animation_scene_asset(node_item) -> Dict[str, Any] | None:
    result = resolve_fbx_animation_source(
        node_item,
        persist=True,
        validate_animation=True,
    )
    if result.status == "error":
        return None
    return _build_preview_asset(getattr(node_item, "model", None), result)


def build_ports(node_item) -> None:
    _ensure_param(node_item, "path", "")
    _ensure_param(node_item, "resolved_path", "")
    _ensure_param(node_item, "debug_log", "0")
    _remove_named_output(node_item, "path")
    _ensure_hidden_params(getattr(node_item, "model", None), ["resolved_path", "debug_log"])


def resolve_fbx_animation_source(
    node_item,
    *,
    base_dir: Path | None = None,
    persist: bool = False,
    validate_animation: bool = False,
) -> FBXAnimationSourceResolutionResult:
    model = getattr(node_item, "model", None)
    if base_dir is None:
        base_dir = _workflow_dir_for_node(node_item)

    requested_path = _param_value(model, "path")
    errors: List[str] = []
    warnings: List[str] = []
    resolved_path = ""
    bind_result = None
    animation_result = None

    if not requested_path:
        errors.append("path: source is empty.")
    else:
        path_obj = _resolve_existing_path(requested_path, base_dir)
        if path_obj is None:
            errors.append(f"path: file does not exist: {requested_path}")
        elif path_obj.suffix.lower() != ".fbx":
            errors.append(f"path: file is not .fbx: {path_obj}")
        else:
            resolved_path = str(path_obj)

    if validate_animation and resolved_path:
        try:
            bind_result = ingest_fbx_bind_data(resolved_path)
        except FBXBindIngestError as exc:
            warnings.append(f"skeleton preview ingest failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"skeleton preview ingest failed: {exc}")
        else:
            _append_result_warnings(warnings, bind_result, "skeleton: ")

        skeleton = getattr(bind_result, "skeleton", None) if bind_result is not None else None
        try:
            animation_result = ingest_fbx_animation_data(resolved_path, skeleton=skeleton)
        except FBXAnimationIngestError as exc:
            errors.append(f"animation ingest failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"animation ingest failed: {exc}")
        else:
            _append_result_warnings(warnings, animation_result, "animation: ")
            try:
                clips = list(getattr(animation_result, "clips", []) or [])
            except Exception:
                clips = []
            if not clips:
                warnings.append("animation: no clips were found.")

    summary = _summary_from_results(bind_result, animation_result) if validate_animation else ""
    status = "error" if errors else ("warning" if warnings else "ok")
    result = FBXAnimationSourceResolutionResult(
        status=status,
        requested_path=requested_path,
        resolved_path=resolved_path,
        errors=errors,
        warnings=warnings,
        summary=summary,
    )

    if persist and model is not None:
        try:
            setattr(model, "_fbx_animation_resolved_path", resolved_path)
            setattr(model, "_fbx_animation_validation_state", status)
            setattr(model, "_fbx_animation_validation_errors", list(errors))
            setattr(model, "_fbx_animation_validation_warnings", list(warnings))
            setattr(model, "_fbx_animation_validation_messages", list(result.message_lines()))
            setattr(model, "_fbx_animation_bind_result", bind_result)
            setattr(model, "_fbx_animation_result", animation_result)
            if bind_result is not None:
                setattr(model, "_fbx_animation_skeleton", getattr(bind_result, "skeleton", None))
            if animation_result is not None:
                clips = list(getattr(animation_result, "clips", []) or [])
                setattr(model, "_fbx_animation_clip", clips[0] if clips else None)
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
    if kind not in FBX_ANIMATION_KIND_ALIASES:
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
        detail_box.setPlaceholderText("FBX animation validation details will appear here.")
    except Exception:
        pass

    browse_button = QtWidgets.QPushButton("Browse FBX")
    browse_button.setToolTip("Select an FBX file that contains skeleton animation.")
    validate_button = QtWidgets.QPushButton("Validate FBX Anim")
    validate_button.setToolTip("Parse the FBX skeleton hierarchy and animation clips.")
    view_button = QtWidgets.QPushButton("View")
    view_button.setToolTip("Open the animated skeleton wire preview in the 3D viewport.")
    copy_button = QtWidgets.QPushButton("Copy Report")
    copy_button.setToolTip("Copy the FBX animation validation report to clipboard.")

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
        result = resolve_fbx_animation_source(
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
        bind_result = getattr(model_obj, "_fbx_animation_bind_result", None)
        animation_result = getattr(model_obj, "_fbx_animation_result", None)
        if validate or bind_result is not None or animation_result is not None:
            detail_lines.append(_clip_summary_text(bind_result, animation_result))
        elif result.status != "error":
            detail_lines.append("fbx animation: <unvalidated>")
        if result.errors:
            detail_lines.append(f"errors: {len(result.errors)}")
        if result.warnings:
            detail_lines.append(f"warnings: {len(result.warnings)}")
        detail_box.setPlainText("\n".join(detail_lines))

        report = "\n".join(result.message_lines())
        report_holder["value"] = report
        tooltip_lines = []
        if not validate and not result.errors:
            tooltip_lines.append("Animation ingest not run yet. Click 'Validate FBX Anim'.")
        tooltip_lines.extend(result.message_lines())
        detail_box.setToolTip("\n".join(tooltip_lines))

        if toast:
            if result.status in ("error", "warning"):
                QtWidgets.QMessageBox.warning(card, "FBX Animation Validation", report)
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
            "Select FBX Animation File",
            start_dir,
            "FBX Animation (*.fbx);;FBX Files (*.fbx);;All Files (*.*)",
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
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "FBX animation report copied.", card)
        except Exception:
            pass

    def _on_view_clicked() -> None:
        item = _node_item()
        if item is None:
            QtWidgets.QMessageBox.warning(card, "FBX Animation View", "Node item is not available.")
            return
        result = _refresh(persist=True, validate=True, toast=False)
        if result is None:
            return
        if result.status == "error":
            QtWidgets.QMessageBox.warning(
                card,
                "FBX Animation View",
                "\n".join(result.message_lines()),
            )
            return

        model_obj = getattr(item, "model", None)
        asset = _build_preview_asset(model_obj, result)
        if not isinstance(asset, dict):
            QtWidgets.QMessageBox.warning(
                card,
                "FBX Animation View",
                "FBX skeleton preview is not available. Validate the source first.",
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
            QtWidgets.QMessageBox.warning(card, "FBX Animation View", "3D view is not available.")
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


FBX_ANIMATION_IMPORT_SPEC = Spec(
    stripe_color="#38bdf8",
    augment_infocard_footer=augment_infocard_footer,
    build_ports=build_ports,
)


__all__ = [
    "FBX_ANIMATION_KIND_ALIASES",
    "FBXAnimationSourceResolutionResult",
    "build_ports",
    "resolve_fbx_animation_source",
    "build_fbx_animation_scene_asset",
    "augment_infocard_footer",
    "FBX_ANIMATION_IMPORT_SPEC",
]
