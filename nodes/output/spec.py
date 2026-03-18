# nodes/output/spec.py
from __future__ import annotations

from datetime import datetime

from nodes.core import Spec
from echograph.qt_compat import QtWidgets, QtCore

OUTPUT_RUN_MODE_KEY = "__output_run_mode"
OUTPUT_MODE_AUTO = "auto"
OUTPUT_MODE_MANUAL = "manual"
OUTPUT_MODE_STARTUP = "run_on_startup"
OUTPUT_HIDDEN_KEY = "__ui_hidden_params"
VOICE_ACTOR_NODE_KINDS = {"voice_actor", "voice actor", "voiceactor"}

OUTPUT_BODY_W = 220
OUTPUT_BODY_H = 58


def _param_value(model, name: str, default: str = "") -> str:
    if model is None:
        return default
    key = (name or "").strip().lower()
    for p in (getattr(model, "params", None) or []):
        if (p.get("name") or "").strip().lower() == key:
            return str(p.get("value", "") or "")
    return default


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    hidden_entry = None
    for p in params:
        if (p.get("name") or "").strip().lower() == OUTPUT_HIDDEN_KEY:
            hidden_entry = p
            break
    if hidden_entry is None:
        hidden_entry = {"name": OUTPUT_HIDDEN_KEY, "value": ""}
        params.append(hidden_entry)
    hidden = {part.strip().lower() for part in str(hidden_entry.get("value", "")).split(",") if part.strip()}
    for name in names or []:
        key = str(name or "").strip().lower()
        if key:
            hidden.add(key)
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _set_node_param(node_item, name: str, value: str, *, emit_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    clean = str(value or "")
    found = False
    changed = False
    for p in params:
        if (p.get("name") or "").strip().lower() == key:
            found = True
            if str(p.get("value", "") or "") != clean:
                p["value"] = clean
                changed = True
            break
    if not found:
        params.append({"name": name, "value": clean})
        changed = True
    model.params = params
    _ensure_hidden_params(model, [name])
    if not changed or not emit_scene:
        return
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None or not hasattr(scene, "paramChanged"):
        return
    try:
        scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
    except Exception:
        pass


def _normalize_run_mode(value: str) -> str:
    text = str(value or "").strip().lower()
    if text == OUTPUT_MODE_MANUAL:
        return OUTPUT_MODE_MANUAL
    if text == OUTPUT_MODE_STARTUP:
        return OUTPUT_MODE_STARTUP
    return OUTPUT_MODE_AUTO


def _ensure_output_params(node_item) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    mode = _normalize_run_mode(_param_value(model, OUTPUT_RUN_MODE_KEY, OUTPUT_MODE_AUTO))
    _set_node_param(node_item, OUTPUT_RUN_MODE_KEY, mode, emit_scene=False)
    _ensure_hidden_params(model, [OUTPUT_RUN_MODE_KEY])


def build_ports(node_item) -> None:
    _ensure_output_params(node_item)


def _refresh_voice_actor_widgets(scene) -> None:
    if scene is None:
        return
    node_items = list(getattr(scene, "_node_items", {}).values() or [])
    for item in node_items:
        model = getattr(item, "model", None)
        kind = (getattr(model, "kind", "") or "").strip().lower()
        if kind not in VOICE_ACTOR_NODE_KINDS:
            continue
        handled = False
        for proxy in list(getattr(item, "_plugin_proxies", []) or []):
            if proxy is None or not hasattr(proxy, "widget"):
                continue
            widget = proxy.widget()
            if widget is None:
                continue
            ensure_scene = getattr(widget, "_ensure_scene_connections", None)
            if callable(ensure_scene):
                try:
                    ensure_scene()
                except Exception:
                    pass
            refresh = getattr(widget, "_schedule_param_refresh", None)
            if callable(refresh):
                try:
                    refresh()
                    handled = True
                except Exception:
                    pass
            else:
                refresh = getattr(widget, "_refresh_source_param_options", None)
                if callable(refresh):
                    try:
                        QtCore.QTimer.singleShot(0, refresh)
                        handled = True
                    except Exception:
                        pass
        if handled:
            continue
        if hasattr(scene, "refresh_node_widget"):
            try:
                scene.refresh_node_widget(getattr(model, "name", ""))
            except Exception:
                pass


def _run_output_update(scene, node_item) -> bool:
    if scene is None or node_item is None:
        return False
    model = getattr(node_item, "model", None)
    if model is None:
        return False
    out_name = str(getattr(model, "name", "") or "").strip()
    if not out_name:
        return False
    ordered_models = []
    if hasattr(scene, "recompute_active_path"):
        try:
            ordered_models = list(scene.recompute_active_path(out_name) or [])
        except Exception:
            ordered_models = []
    if not ordered_models:
        ordered_models = [model]
    on_info = getattr(scene, "on_info", None)
    if callable(on_info):
        try:
            on_info(model)
        except Exception:
            pass
    on_branch = getattr(scene, "on_branch", None)
    if callable(on_branch):
        try:
            on_branch(ordered_models)
        except Exception:
            pass
    _refresh_voice_actor_widgets(scene)
    if hasattr(scene, "paramChanged"):
        try:
            scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
        except Exception:
            pass
    return True


class OutputModeWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._syncing_mode = False
        self._startup_ran = False
        self._running = False
        _ensure_output_params(node_item)
        model = getattr(node_item, "model", None)
        self._mode = _normalize_run_mode(_param_value(model, OUTPUT_RUN_MODE_KEY, OUTPUT_MODE_AUTO))

        self._mode_label = QtWidgets.QLabel("Mode")
        self._mode_label.setStyleSheet("QLabel{color:#94a3b8;}")

        self._mode_combo = QtWidgets.QComboBox()
        self._mode_combo.setStyleSheet(
            "QComboBox{background:#11151c;color:#e6edf3;border:1px solid #334155;border-radius:4px;padding:3px 8px;}"
            "QComboBox QAbstractItemView{background:#0f1216;color:#e6edf3;selection-background-color:#7c3aed;}"
        )
        self._mode_combo.addItem("Auto", OUTPUT_MODE_AUTO)
        self._mode_combo.addItem("Manual", OUTPUT_MODE_MANUAL)
        self._mode_combo.addItem("Run On Startup", OUTPUT_MODE_STARTUP)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self._run_btn = QtWidgets.QPushButton("Run")
        self._run_btn.setStyleSheet(
            "QPushButton{background:#166534;color:#e5e7eb;border:1px solid #15803d;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#15803d;}"
            "QPushButton:pressed{background:#16a34a;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._run_btn.clicked.connect(self._on_run_clicked)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("QLabel{color:#94a3b8;font-size:10px;}")
        try:
            self._status.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        except Exception:
            pass

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self._mode_label, 0)
        row.addWidget(self._mode_combo, 1)
        row.addWidget(self._run_btn, 0)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addLayout(row, 0)
        layout.addWidget(self._status, 0)

        self._set_mode_ui()
        self._queue_scene_sync()

    def sizeHint(self):
        return QtCore.QSize(OUTPUT_BODY_W, OUTPUT_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(OUTPUT_BODY_W, OUTPUT_BODY_H)

    def _set_status(self, text: str) -> None:
        self._status.setText(str(text or ""))

    def _queue_scene_sync(self) -> None:
        QtCore.QTimer.singleShot(0, self._sync_scene_state)
        QtCore.QTimer.singleShot(120, self._sync_scene_state)

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None or self._scene_connected:
            return self._scene
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._on_scene_links_changed)
            except Exception:
                pass
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(self._on_scene_param_changed)
            except Exception:
                pass
        self._scene_connected = True
        return self._scene

    def _set_mode_ui(self) -> None:
        self._syncing_mode = True
        try:
            self._mode_combo.blockSignals(True)
            desired = OUTPUT_MODE_AUTO
            if self._mode == OUTPUT_MODE_MANUAL:
                desired = OUTPUT_MODE_MANUAL
            elif self._mode == OUTPUT_MODE_STARTUP:
                desired = OUTPUT_MODE_STARTUP
            for idx in range(self._mode_combo.count()):
                if str(self._mode_combo.itemData(idx) or "") == desired:
                    self._mode_combo.setCurrentIndex(idx)
                    break
        finally:
            self._mode_combo.blockSignals(False)
            self._syncing_mode = False
        manual = self._mode == OUTPUT_MODE_MANUAL
        self._run_btn.setVisible(manual)
        self._run_btn.setEnabled(manual)
        if self._mode == OUTPUT_MODE_STARTUP:
            if self._startup_ran:
                self._set_status("Startup run completed.")
            else:
                self._set_status("Will run once when workflow opens.")
        elif self._mode == OUTPUT_MODE_MANUAL:
            self._set_status("Manual mode: click Run.")
        else:
            self._set_status("Auto mode: updates with graph changes.")

    def _sync_scene_state(self) -> None:
        self._ensure_scene()
        if self._mode == OUTPUT_MODE_STARTUP and not self._startup_ran:
            self._run_now(startup=True)

    def _on_scene_links_changed(self, *_args) -> None:
        if self._mode == OUTPUT_MODE_STARTUP and not self._startup_ran:
            self._run_now(startup=True)

    def _on_scene_param_changed(self, name=None, _params=None) -> None:
        if self._mode != OUTPUT_MODE_STARTUP or self._startup_ran:
            return
        changed = str(name or "").strip()
        model_name = str(getattr(getattr(self._node_item, "model", None), "name", "") or "").strip()
        if changed and changed != model_name:
            return
        self._run_now(startup=True)

    def _on_mode_changed(self, _index: int) -> None:
        if self._syncing_mode:
            return
        selected = _normalize_run_mode(self._mode_combo.currentData())
        if selected == self._mode:
            return
        self._mode = selected
        _set_node_param(self._node_item, OUTPUT_RUN_MODE_KEY, self._mode)
        if self._mode != OUTPUT_MODE_STARTUP:
            self._startup_ran = False
        self._set_mode_ui()
        if self._mode == OUTPUT_MODE_STARTUP:
            self._queue_scene_sync()

    def _on_run_clicked(self) -> None:
        self._run_now(startup=False)

    def _run_now(self, *, startup: bool) -> None:
        if self._running:
            return
        scene = self._ensure_scene()
        if scene is None:
            self._set_status("Waiting for scene...")
            return
        self._running = True
        try:
            ok = _run_output_update(scene, self._node_item)
            if not ok:
                self._set_status("Run failed.")
                return
            when = datetime.now().strftime("%H:%M:%S")
            if startup:
                self._startup_ran = True
                self._set_status(f"Startup run completed at {when}.")
            else:
                self._set_status(f"Ran at {when}.")
        finally:
            self._running = False
            self._set_mode_ui()


def render_node_body(node_item, y_cursor: int) -> int:
    try:
        body = OutputModeWidget(node_item, None)
    except Exception as exc:
        print("[EchoGraph] Output UI init failed:", exc)
        body = QtWidgets.QFrame()
        body.setStyleSheet(
            "QFrame{background:#0f1216;color:#e5e7eb;border:1px solid #334;border-radius:6px;}"
        )
        lay = QtWidgets.QVBoxLayout(body)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        msg = QtWidgets.QLabel("Output controls failed to load.")
        msg.setWordWrap(True)
        msg.setStyleSheet("QLabel{color:#e5e7eb;}")
        lay.addWidget(msg, 1)

    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)

    h = body.sizeHint().height()
    try:
        pad = float(getattr(node_item, "_PADDING", 0))
        available = float(node_item.height) - float(y_cursor) - pad
        if available > h:
            h = int(available)
    except Exception:
        pass
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + h


OUTPUT_SPEC = Spec(
    stripe_color="#a855f7",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
