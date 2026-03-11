from __future__ import annotations

import os
import re
import time
from pathlib import Path

try:
    from PySide6 import QtWidgets, QtCore
except Exception:
    try:
        from PySide2 import QtWidgets, QtCore  # type: ignore
    except Exception:
        QtWidgets = None  # type: ignore
        QtCore = None  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

_PASS_THROUGH_KINDS = {
    "ply_sequence",
    "switch",
    "uv_unwrap",
    "texture",
    "texture_pro",
    "texture_layer",
    "mnaterial",
    "material",
    "transforms",
    "fx",
    "fx_trail",
}

_PLY_SEQ_LOG_ENABLED = False
_PLY_SEQ_LOG_FLAGS = {}
_PLY_SEQ_LOG_TIMES = {}


def _log(msg: str) -> None:
    if not _PLY_SEQ_LOG_ENABLED:
        return
    try:
        root = Path(__file__).resolve().parents[2]
    except Exception:
        root = Path.cwd()
    try:
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with (log_dir / "ply_sequence_debug.log").open("a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass


def _log_throttled(key: str, msg: str, interval: float = 0.3) -> None:
    try:
        now = float(time.time())
        last = float(_PLY_SEQ_LOG_TIMES.get(str(key), 0.0) or 0.0)
        if (now - last) < float(interval):
            return
        _PLY_SEQ_LOG_TIMES[str(key)] = now
    except Exception:
        pass
    _log(msg)


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for p in (getattr(model, "params", None) or []):
        if (p.get("name") or "").strip().lower() == key:
            return p.get("value", "") or ""
    return ""


def _bool_param(raw: str, default: bool = False) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _set_node_log_enabled(node_item, enabled: bool) -> None:
    global _PLY_SEQ_LOG_ENABLED
    model = getattr(node_item, "model", None)
    node_name = str(getattr(model, "name", "") or "").strip()
    if not node_name:
        node_name = f"node_{id(node_item)}"
    _PLY_SEQ_LOG_FLAGS[node_name] = bool(enabled)
    _PLY_SEQ_LOG_ENABLED = any(bool(v) for v in _PLY_SEQ_LOG_FLAGS.values())


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


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    existing = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == store_key:
            existing = entry
            break
    if existing is None:
        existing = {"name": store_key, "value": ""}
        params.append(existing)

    raw = existing.get("value", "")
    hidden = {part.strip().lower() for part in str(raw).split(",") if part.strip()}
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    existing["value"] = ",".join(sorted(hidden))
    model.params = params


def _ordered_in_edges(scene, item) -> list:
    try:
        return list(scene._ordered_in_edges(item))
    except Exception:
        try:
            return list(scene._in_edges(item))
        except Exception:
            return []


def _pick_input_edge(scene, item, wanted_ports=None):
    edges = _ordered_in_edges(scene, item)
    if not edges:
        return None
    if wanted_ports:
        wanted = {str(p).strip().lower() for p in wanted_ports if str(p).strip()}
        for edge in edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in wanted:
                return edge
    return edges[0]


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


def _resolve_source_path(node_item, raw_path: str) -> str:
    text = str(raw_path or "").strip()
    if not text:
        return ""
    try:
        p = Path(text).expanduser()
    except Exception:
        return text
    if p.is_absolute():
        return str(p)
    base = _workflow_dir_for_node(node_item)
    if base is None:
        return str(p)
    try:
        return str((base / p).resolve())
    except Exception:
        return str(base / p)


def _resolve_sequence_source_from_input(node_item) -> str:
    scene = node_item.scene()
    if scene is None:
        return ""

    edge = _pick_input_edge(scene, node_item, wanted_ports=("mesh", "path", "source"))
    if edge is None:
        return ""

    item = getattr(edge, "src", None)
    visited = set()
    depth = 0
    while item is not None and id(item) not in visited and depth < 16:
        visited.add(id(item))
        depth += 1
        up_model = getattr(item, "model", None)
        if up_model is None:
            break
        kind = (getattr(up_model, "kind", "") or "").strip().lower()

        if kind == "transforms":
            src = _param_value(up_model, "source")
            if src:
                return _resolve_source_path(node_item, src)

        path = _param_value(up_model, "path") or _param_value(up_model, "mesh") or _param_value(up_model, "source")
        if kind not in _PASS_THROUGH_KINDS:
            return _resolve_source_path(node_item, path)

        up_edge = _pick_input_edge(scene, item, wanted_ports=("mesh", "path", "source"))
        if up_edge is None:
            up_edge = _pick_input_edge(scene, item, wanted_ports=None)
        if up_edge is None:
            return _resolve_source_path(node_item, path)
        item = getattr(up_edge, "src", None)

    return ""


def _resolve_sequence_source(node_item) -> str:
    scene = node_item.scene()
    model = getattr(node_item, "model", None)
    if scene is None:
        raw = _param_value(model, "mesh") or _param_value(model, "source") or _param_value(model, "path")
        return _resolve_source_path(node_item, raw)

    src = _resolve_sequence_source_from_input(node_item)
    if src:
        return src

    raw = _param_value(model, "mesh") or _param_value(model, "source") or _param_value(model, "path")
    return _resolve_source_path(node_item, raw)


def _natural_sort_key(path: Path):
    parts = re.split(r"(\d+)", path.name.lower())
    out = []
    for token in parts:
        if token.isdigit():
            out.append((1, int(token)))
        else:
            out.append((0, token))
    return out


def _sequence_from_first_frame(path: Path) -> list[Path]:
    try:
        p = Path(path)
    except Exception:
        return []
    if not p.exists() or not p.is_file():
        return []
    if p.suffix.lower() != ".ply":
        return []

    stem = str(p.stem)
    spans = [(m.start(), m.end()) for m in re.finditer(r"\d+", stem)]
    if not spans:
        return [p]

    children = []
    try:
        for child in p.parent.iterdir():
            if not child.is_file():
                continue
            if child.suffix.lower() != ".ply":
                continue
            children.append(child)
    except Exception:
        return [p]
    if not children:
        return [p]

    best = [p]
    best_count = 1
    ext = p.suffix

    def _score_span(span):
        start, end = span
        width = max(0, int(end) - int(start))
        # Prefer wider numeric groups (e.g., 000001 over the "3" in _3dgs).
        return (-width, int(start))

    for start, end in sorted(spans, key=_score_span):
        prefix = stem[:start]
        suffix = stem[end:]
        rx = re.compile(rf"^{re.escape(prefix)}(\d+){re.escape(suffix)}{re.escape(ext)}$", flags=re.IGNORECASE)
        cur = []
        for child in children:
            if rx.match(child.name):
                cur.append(child)
        if len(cur) > best_count:
            best = list(cur)
            best_count = len(cur)

    if best_count <= 1:
        out = [p]
    else:
        out = sorted(best, key=_natural_sort_key)
    try:
        _log(
            "seq_scan seed="
            + str(p)
            + " frames="
            + str(len(out))
            + " first="
            + (out[0].name if out else "")
            + " last="
            + (out[-1].name if out else "")
        )
    except Exception:
        pass
    return out


def _open_preview_path(node_item, path: str, *, preserve_camera: bool = False) -> bool:
    text = str(path or "").strip()
    if not text:
        return False
    win = _resolve_window(node_item)
    if win is None:
        return False
    glv = getattr(win, "gl_view", None)
    cam_state = None
    if bool(preserve_camera) and glv is not None:
        get_state = getattr(glv, "_mgl_get_camera_state", None)
        if callable(get_state):
            try:
                cam_state = dict(get_state() or {})
            except Exception:
                cam_state = None
            if isinstance(cam_state, dict):
                try:
                    cam_state.pop("scene_xforms", None)
                except Exception:
                    pass
    handler = getattr(win, "open_3d_model", None)
    if not callable(handler):
        _log("preview_open skip: open_3d_model unavailable path=" + text)
        return False

    def _restore_camera() -> None:
        if not bool(preserve_camera):
            return
        if not isinstance(cam_state, dict):
            return
        if glv is None:
            return
        queue_state = getattr(glv, "_mgl_queue_camera_state", None)
        if not callable(queue_state):
            return
        try:
            queue_state(cam_state)
            _log_throttled(
                "preview_cam_preserve",
                "preview_cam_preserve path=" + text,
                interval=0.35,
            )
        except Exception:
            pass

    try:
        handler(text, None, False)
        _restore_camera()
        _log("preview_open ok path=" + text + " sig=(path,None,False)")
        return True
    except TypeError:
        pass
    except Exception:
        _log("preview_open fail path=" + text + " sig=(path,None,False)")
    try:
        handler(text, None)
        _restore_camera()
        _log("preview_open ok path=" + text + " sig=(path,None)")
        return True
    except TypeError:
        pass
    except Exception:
        _log("preview_open fail path=" + text + " sig=(path,None)")
    try:
        handler(text)
        _restore_camera()
        _log("preview_open ok path=" + text + " sig=(path)")
        return True
    except Exception:
        _log("preview_open fail path=" + text + " sig=(path)")
        return False


def _timeline_frame_for_node(node_item) -> int:
    win = _resolve_window(node_item)
    if win is None:
        return 0
    glv = getattr(win, "gl_view", None)
    if glv is None:
        return 0
    fn = getattr(glv, "_timeline_current_frame", None)
    if not callable(fn):
        return 0
    try:
        return max(0, int(fn()))
    except Exception:
        return 0


def _pick_frame_path(seq_paths: list[Path], frame: int, loop: bool) -> Path | None:
    if not seq_paths:
        return None
    idx = int(frame)
    if bool(loop):
        idx = idx % len(seq_paths)
    else:
        idx = max(0, min(idx, len(seq_paths) - 1))
    try:
        return seq_paths[idx]
    except Exception:
        return seq_paths[0]


def _resolve_window(node_item):
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
        win = node_item.window()
        if win is not None:
            return win
    except Exception:
        pass
    if QtWidgets is not None:
        try:
            aw = QtWidgets.QApplication.activeWindow()
            if aw is not None and aw.isWindow():
                return aw
        except Exception:
            pass
    return None


def _set_node_param_value(node_item, name: str, value: str, *, notify_scene: bool = False) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    key = (name or "").strip().lower()
    try:
        cur = _param_value(model, key)
    except Exception:
        cur = ""
    if str(cur or "") == str(value or ""):
        return
    if hasattr(node_item, "_set_param_value"):
        try:
            node_item._set_param_value(name, str(value or ""), rebuild=False, notify_scene=notify_scene)
            return
        except Exception:
            pass
    params = list(getattr(model, "params", None) or [])
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value or "")
            break
    else:
        params.append({"name": name, "value": str(value or "")})
    try:
        model.params = params
    except Exception:
        pass


def _refresh_connected_scenes(node_item, changed_name: str, *, follow_camera: bool = False) -> int:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None:
        return 0

    win = _resolve_window(node_item)
    handler = getattr(win, "open_scene_assets", None) if win is not None else None
    if not callable(handler):
        return 0

    try:
        from nodes.scene import spec as scene_spec  # type: ignore
    except Exception:
        return 0

    collector = getattr(scene_spec, "_collect_assets", None)
    if not callable(collector):
        return 0

    node_items = getattr(scene, "_node_items", None)
    if isinstance(node_items, dict):
        candidates = list(node_items.values())
    else:
        candidates = []

    # Follow Cam is preview-only; scene refresh should keep viewport stable.
    _ = bool(follow_camera)

    def _reapply_asset_xforms(asset_rows) -> int:
        glv = getattr(win, "gl_view", None) if win is not None else None
        renderer = getattr(glv, "_mgl_renderer", None) or glv
        if renderer is None:
            return 0
        setf = getattr(renderer, "_mgl_set_scene_asset_xform", None)
        if not callable(setf):
            return 0
        rebuild_splats = getattr(renderer, "_mgl_rebuild_scene_splats", None)
        applied = 0
        touched_splats = False
        for entry in asset_rows or []:
            if not isinstance(entry, dict):
                continue
            owner = str(entry.get("node") or "").strip()
            if not owner:
                path_hint = str(entry.get("path") or "").strip()
                if path_hint:
                    try:
                        owner = Path(path_hint).name
                    except Exception:
                        owner = ""
            if not owner:
                continue
            xf = entry.get("xform")
            if not isinstance(xf, dict):
                continue
            ext = str(entry.get("ext") or "").strip().lower()
            if not ext:
                try:
                    ext = Path(str(entry.get("path") or "").strip()).suffix.lower()
                except Exception:
                    ext = ""
            is_splat = ext == ".ply"
            try:
                setf(
                    owner,
                    pos=xf.get("pos"),
                    rot=xf.get("rot"),
                    scl=xf.get("scl"),
                    apply_to_scene_models=not bool(is_splat),
                    use_splat_xform=bool(is_splat),
                )
                applied += 1
                if is_splat:
                    touched_splats = True
            except Exception:
                continue
        if touched_splats and callable(rebuild_splats):
            try:
                rebuild_splats(preserve_camera=True)
            except Exception:
                pass
        return int(applied)

    refreshed = 0
    for item in candidates:
        model = getattr(item, "model", None)
        if model is None:
            continue
        kind = (getattr(model, "kind", "") or "").strip().lower()
        if kind != "scene":
            continue
        if not _param_change_relevant(item, changed_name):
            continue
        try:
            assets = list(collector(item) or [])
        except Exception:
            assets = []
        if not assets:
            continue
        dispatched = False
        try:
            handler(assets, frame=False)
            dispatched = True
        except TypeError:
            try:
                handler(assets)
                dispatched = True
            except Exception:
                pass
        except Exception:
            pass
        if not dispatched:
            continue
        refreshed += 1
        try:
            applied = _reapply_asset_xforms(assets)
            _log_throttled(
                "scene_refresh_apply_" + str(changed_name),
                "scene_refresh_xforms changed_name="
                + str(changed_name)
                + " applied="
                + str(int(applied)),
                interval=0.2,
            )
        except Exception:
            pass
    _log_throttled(
        "scene_refresh_" + str(changed_name),
        "scene_refresh changed_name="
        + str(changed_name)
        + " refreshed="
        + str(refreshed)
        + " follow="
        + ("1" if bool(follow_camera) else "0"),
        interval=0.2,
    )
    return int(refreshed)


if QtWidgets is not None and QtCore is not None:
    class PLYSequenceWidget(QtWidgets.QWidget):
        def __init__(self, node_item, parent=None):
            super().__init__(parent)
            self._node_item = node_item
            self._scene = None
            self._scene_connected = False

            self._seq_seed = ""
            self._seq_paths: list[Path] = []
            self._last_frame = None
            self._last_path = ""
            self._refresh_pending = False
            self._preview_active = False
            self._node_name = str(getattr(getattr(self._node_item, "model", None), "name", "") or "ply_sequence")

            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(6, 4, 6, 4)
            layout.setSpacing(4)

            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)

            self._source_edit = QtWidgets.QLineEdit()
            self._source_edit.setPlaceholderText("First frame .ply")
            self._source_edit.editingFinished.connect(self._on_source_committed)
            row.addWidget(self._source_edit, 1)

            self._browse_btn = QtWidgets.QPushButton("Browse")
            self._browse_btn.setFixedWidth(70)
            self._browse_btn.clicked.connect(self._on_browse_clicked)
            row.addWidget(self._browse_btn, 0)

            self._view_btn = QtWidgets.QPushButton("View")
            self._view_btn.setFixedWidth(58)
            self._view_btn.clicked.connect(self._on_view_clicked)
            row.addWidget(self._view_btn, 0)

            layout.addLayout(row, 0)

            row2 = QtWidgets.QHBoxLayout()
            row2.setContentsMargins(0, 0, 0, 0)
            row2.setSpacing(8)

            self._loop_box = QtWidgets.QCheckBox("Loop")
            self._loop_box.toggled.connect(self._on_loop_toggled)
            row2.addWidget(self._loop_box, 0)
            self._follow_cam_box = QtWidgets.QCheckBox("Follow Cam")
            self._follow_cam_box.toggled.connect(self._on_follow_cam_toggled)
            row2.addWidget(self._follow_cam_box, 0)
            row2.addStretch(1)
            layout.addLayout(row2, 0)

            self._status = QtWidgets.QLabel("")
            self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
            layout.addWidget(self._status, 0)

            self._timer = QtCore.QTimer(self)
            self._timer.setInterval(40)
            self._timer.timeout.connect(self._on_timer_tick)
            self._timer.start()

            self._ensure_scene()
            QtCore.QTimer.singleShot(0, self._sync_state)
            _log("widget_init node=" + self._node_name)

        def sizeHint(self):
            return QtCore.QSize(260, 90)

        def _ensure_scene(self):
            if self._scene is None:
                self._scene = self._node_item.scene()
            if self._scene is None or self._scene_connected:
                return
            if hasattr(self._scene, "linksChanged"):
                try:
                    self._scene.linksChanged.connect(self._schedule_sync)
                except Exception:
                    pass
            if hasattr(self._scene, "paramChanged"):
                try:
                    self._scene.paramChanged.connect(self._on_scene_param_changed)
                except Exception:
                    pass
            self._scene_connected = True

        def _on_scene_param_changed(self, name=None, _params=None):
            if _param_change_relevant(self._node_item, name):
                self._schedule_sync()

        def _schedule_sync(self):
            if self._refresh_pending:
                return
            self._refresh_pending = True
            QtCore.QTimer.singleShot(0, self._sync_state)

        def _set_param(self, name: str, value: str, notify_scene: bool = True):
            _set_node_param_value(self._node_item, name, value, notify_scene=notify_scene)

        def _source_is_input_driven(self) -> bool:
            try:
                src = (_resolve_sequence_source_from_input(self._node_item) or "").strip()
            except Exception:
                src = ""
            return bool(src)

        def _on_source_committed(self):
            if self._source_is_input_driven():
                _log("source_commit ignored node=" + self._node_name + " reason=input_driven")
                self._schedule_sync()
                return
            text = str(self._source_edit.text() or "").strip()
            self._set_param("source", text, notify_scene=True)
            _log("source_commit node=" + self._node_name + " source=" + text)
            self._schedule_sync()

        def _on_browse_clicked(self):
            if self._source_is_input_driven():
                _log("source_browse ignored node=" + self._node_name + " reason=input_driven")
                self._schedule_sync()
                return
            start = str(self._source_edit.text() or "").strip()
            if not start:
                start = os.path.expanduser("~")
            parent = _resolve_window(self._node_item) or self
            chosen, _flt = QtWidgets.QFileDialog.getOpenFileName(
                parent,
                "Select First PLY Frame",
                start,
                "PLY Files (*.ply);;All Files (*.*)",
            )
            if not chosen:
                return
            self._source_edit.setText(chosen)
            self._set_param("source", chosen, notify_scene=True)
            _log("source_browse node=" + self._node_name + " source=" + str(chosen))
            self._schedule_sync()

        def _on_loop_toggled(self, checked: bool):
            self._set_param("loop", "1" if bool(checked) else "0", notify_scene=True)
            _log("loop_toggle node=" + self._node_name + " loop=" + ("1" if bool(checked) else "0"))
            self._schedule_sync()

        def _on_follow_cam_toggled(self, checked: bool):
            self._set_param("follow_camera", "1" if bool(checked) else "0", notify_scene=True)
            _log("follow_cam_toggle node=" + self._node_name + " follow=" + ("1" if bool(checked) else "0"))
            self._schedule_sync()

        def _on_view_clicked(self):
            path = str(_param_value(getattr(self._node_item, "model", None), "path") or "").strip()
            if self._preview_active:
                self._preview_active = False
                self._view_btn.setText("View")
                _log("preview_stop node=" + self._node_name)
                # Preview had priority; force a scene refresh on current path now.
                self._last_path = ""
                self._schedule_sync()
                return
            self._preview_active = True
            self._view_btn.setText("Stop")
            _log("preview_start node=" + self._node_name)
            if path:
                follow_camera = _bool_param(
                    _param_value(getattr(self._node_item, "model", None), "follow_camera"),
                    default=False,
                )
                _open_preview_path(self._node_item, path, preserve_camera=not bool(follow_camera))

        def _on_timer_tick(self):
            self._sync_state()

        def _apply_path(self, path: str, reason: str, frame: int) -> None:
            new_path = str(path or "").strip()
            if new_path == self._last_path:
                self._last_frame = frame
                return
            self._set_param("path", new_path, notify_scene=False)
            node_name = str(getattr(getattr(self._node_item, "model", None), "name", "") or "").strip()
            follow_camera = _bool_param(
                _param_value(getattr(self._node_item, "model", None), "follow_camera"),
                default=False,
            )
            if self._preview_active and new_path:
                _open_preview_path(self._node_item, new_path, preserve_camera=not bool(follow_camera))
            elif node_name:
                _refresh_connected_scenes(
                    self._node_item,
                    node_name,
                    follow_camera=bool(follow_camera),
                )
            self._last_path = new_path
            self._last_frame = frame
            _log(
                "path_update node="
                + self._node_name
                + " frame="
                + str(frame)
                + " reason="
                + str(reason)
                + " path="
                + (new_path or "<empty>")
            )

        def _sync_state(self):
            self._refresh_pending = False
            model = getattr(self._node_item, "model", None)
            if model is None:
                return

            enabled = _bool_param(_param_value(model, "enabled"), default=True)
            loop = _bool_param(_param_value(model, "loop"), default=True)
            follow_camera = _bool_param(_param_value(model, "follow_camera"), default=False)
            debug_log = _bool_param(_param_value(model, "debug_log"), default=False)
            _set_node_log_enabled(self._node_item, debug_log)
            try:
                self._loop_box.blockSignals(True)
                self._loop_box.setChecked(bool(loop))
            except Exception:
                pass
            finally:
                try:
                    self._loop_box.blockSignals(False)
                except Exception:
                    pass
            try:
                self._follow_cam_box.blockSignals(True)
                self._follow_cam_box.setChecked(bool(follow_camera))
            except Exception:
                pass
            finally:
                try:
                    self._follow_cam_box.blockSignals(False)
                except Exception:
                    pass

            src_input = (_resolve_sequence_source_from_input(self._node_item) or "").strip()
            src_path = src_input
            if not src_path:
                src_path = _resolve_source_path(self._node_item, _param_value(model, "source"))
            self._set_param("source", src_path, notify_scene=False)

            try:
                if self._source_edit.text() != src_path:
                    self._source_edit.blockSignals(True)
                    self._source_edit.setText(src_path)
                    self._source_edit.blockSignals(False)
            except Exception:
                pass

            input_driven = bool(src_input)
            try:
                self._source_edit.setReadOnly(input_driven)
            except Exception:
                pass
            try:
                if input_driven:
                    self._source_edit.setToolTip("Driven by connected input. Disconnect input to set a manual source.")
                else:
                    self._source_edit.setToolTip("Manual first frame source (.ply).")
            except Exception:
                pass
            try:
                if hasattr(self, "_browse_btn") and self._browse_btn is not None:
                    self._browse_btn.setEnabled(not input_driven)
                    self._browse_btn.setToolTip(
                        "" if not input_driven else "Disabled while source is driven by input."
                    )
            except Exception:
                pass

            if not enabled:
                self._status.setText("Disabled.")
                self._view_btn.setEnabled(False)
                self._view_btn.setText("View")
                self._preview_active = False
                self._apply_path("", "disabled", int(_timeline_frame_for_node(self._node_item)))
                return

            if not src_path:
                self._status.setText("No source .ply.")
                self._view_btn.setEnabled(False)
                self._view_btn.setText("View")
                self._preview_active = False
                self._apply_path("", "no_source", int(_timeline_frame_for_node(self._node_item)))
                self._seq_paths = []
                self._seq_seed = ""
                return

            if not os.path.exists(src_path):
                self._status.setText("Source path not found.")
                self._view_btn.setEnabled(False)
                self._view_btn.setText("View")
                self._preview_active = False
                self._apply_path("", "source_missing", int(_timeline_frame_for_node(self._node_item)))
                self._seq_paths = []
                self._seq_seed = ""
                return

            if Path(src_path).suffix.lower() != ".ply":
                self._status.setText("Sequence expects .ply source.")
                self._view_btn.setEnabled(False)
                self._view_btn.setText("View")
                self._preview_active = False
                self._apply_path("", "wrong_ext", int(_timeline_frame_for_node(self._node_item)))
                self._seq_paths = []
                self._seq_seed = ""
                return

            if self._seq_seed != src_path:
                self._seq_paths = _sequence_from_first_frame(Path(src_path))
                self._seq_seed = src_path
                self._last_frame = None
                _log("seq_seed node=" + self._node_name + " source=" + src_path + " count=" + str(len(self._seq_paths)))

            if not self._seq_paths:
                self._status.setText("No sequence frames found.")
                self._view_btn.setEnabled(False)
                self._view_btn.setText("View")
                self._preview_active = False
                self._apply_path("", "no_frames", int(_timeline_frame_for_node(self._node_item)))
                return

            frame = _timeline_frame_for_node(self._node_item)
            target = _pick_frame_path(self._seq_paths, frame, loop=loop)
            if target is None:
                self._status.setText("No frame for current time.")
                self._view_btn.setEnabled(False)
                self._view_btn.setText("View")
                self._preview_active = False
                self._apply_path("", "no_target", int(frame))
                return

            path = str(target)
            self._view_btn.setEnabled(True)
            self._view_btn.setText("Stop" if self._preview_active else "View")

            idx = 0
            try:
                idx = self._seq_paths.index(target)
            except Exception:
                idx = 0
            self._status.setText(f"Frame {idx + 1}/{len(self._seq_paths)} (timeline {frame})")

            self._apply_path(path, "frame_resolve", int(frame))
            _log_throttled(
                "frame_tick_" + self._node_name,
                "frame_tick node="
                + self._node_name
                + " frame="
                + str(frame)
                + " idx="
                + str(idx)
                + " total="
                + str(len(self._seq_paths))
                + " preview="
                + ("1" if self._preview_active else "0"),
                interval=0.5,
            )


def build_ports(node_item) -> None:
    _ensure_param(node_item, "mesh", "")
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    _ensure_param(node_item, "debug_log", "0")
    _ensure_param(node_item, "enabled", "1")
    _ensure_param(node_item, "loop", "1")
    _ensure_param(node_item, "follow_camera", "0")
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        ("mesh", "path", "debug_log", "follow_camera"),
    )
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")


def _ensure_body_space(node_item, body_h: int) -> None:
    try:
        extra = max(0.0, float(body_h) + 4.0)
        old_h = float(getattr(node_item, "height", 0.0) or 0.0)
        new_h = old_h + extra
        if new_h > old_h + 0.5:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = new_h
    except Exception:
        pass


def render_node_body(node_item, y_cursor: int) -> int:
    if QtWidgets is None or QtCore is None:
        return y_cursor

    body = PLYSequenceWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    h = max(80, int(body.sizeHint().height()))
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass

    _ensure_body_space(node_item, h)
    return y_cursor + h


PLY_SEQUENCE_SPEC = Spec(
    stripe_color="#0ea5e9",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
