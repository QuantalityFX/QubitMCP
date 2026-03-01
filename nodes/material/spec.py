from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except Exception:
    from PySide2 import QtWidgets, QtCore, QtGui  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant
from echograph.material_debug import material_debug_log as _material_debug_log


SUPPORTED_MESH_EXTS = {".obj", ".fbx", ".gltf", ".glb", ".ply", ".stl", ".off", ".om"}
_DEFAULT_TRANSPARENCY = 80
_DEFAULT_IOR = 1.50
_DEFAULT_REFRACTION = 24
_DEFAULT_TINT_COLOR = "#dfe7ff"
_DEFAULT_FRESNEL_AMOUNT = 0
_DEFAULT_FRESNEL_COLOR = "#ffffff"


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return entry.get("value", "") or ""
    return ""


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        setattr(model, "params", params)
    if not isinstance(params, list):
        params = list(params)
        setattr(model, "params", params)
    key = name.strip().lower()
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
    entry = None
    for param in params:
        if (param.get("name") or "").strip().lower() == "__ui_hidden_params":
            entry = param
            break
    if entry is None:
        entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(entry)
    hidden = {tok.strip().lower() for tok in str(entry.get("value") or "").split(",") if tok.strip()}
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _debug_log_enabled(model) -> bool:
    return (str(_param_value(model, "debug_log") or "").strip().lower() in {"1", "true", "yes", "on"})


def _resolve_input_item(node_item):
    scene = node_item.scene()
    model = getattr(node_item, "model", None)

    def _trace(item, depth=0, visited=None):
        if item is None or depth > 8:
            return None, "", ""
        if visited is None:
            visited = set()
        if item in visited:
            return None, "", ""
        visited.add(item)
        item_model = getattr(item, "model", None)
        if item_model is None:
            return None, "", ""
        kind = (getattr(item_model, "kind", "") or "").strip().lower()
        if kind == "switch" and scene is not None:
            try:
                edges = list(scene._ordered_in_edges(item))
            except Exception:
                try:
                    edges = list(scene._in_edges(item))
                except Exception:
                    edges = []
            if edges:
                return _trace(getattr(edges[0], "src", None), depth + 1, visited)
        path = _param_value(item_model, "path") or _param_value(item_model, "mesh") or _param_value(item_model, "source")
        return item, kind, path

    if scene is not None:
        try:
            in_edges = list(scene._ordered_in_edges(node_item))
        except Exception:
            try:
                in_edges = list(scene._in_edges(node_item))
            except Exception:
                in_edges = []
        chosen = None
        for edge in in_edges:
            name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
            if (name or "").strip().lower() in {"mesh", "path"}:
                chosen = edge
                break
        if chosen is None and in_edges:
            chosen = in_edges[0]
        if chosen is not None:
            return _trace(getattr(chosen, "src", None), 0, set())

    if model is not None:
        return None, "", _param_value(model, "source") or _param_value(model, "path")
    return None, "", ""


def _resolve_window(node_item):
    scene = node_item.scene()
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
    return QtWidgets.QApplication.activeWindow()


def _clamp_percent(value: str, default: int) -> int:
    try:
        num = int(round(float(str(value or "").strip())))
    except Exception:
        num = int(default)
    return max(0, min(100, num))


def _legacy_refraction_to_ior(value: str, default: float = _DEFAULT_IOR) -> float:
    text = str(value or "").strip()
    if not text:
        return float(default)
    try:
        num = float(text)
    except Exception:
        return float(default)
    if num <= 0.0:
        return 1.0
    if num <= 1.0:
        return max(1.0, min(2.5, 1.0 + num))
    return max(1.0, min(2.5, 1.0 + (num * 0.01)))


def _clamp_ior(value: str, default: float = _DEFAULT_IOR) -> float:
    text = str(value or "").strip()
    if not text:
        return max(1.0, min(2.5, float(default)))
    try:
        num = float(text)
    except Exception:
        return max(1.0, min(2.5, float(default)))
    return max(1.0, min(2.5, num))


def _ior_from_model(model) -> float:
    raw_ior = _param_value(model, "ior")
    if raw_ior.strip():
        return _clamp_ior(raw_ior, _DEFAULT_IOR)
    return _legacy_refraction_to_ior(_param_value(model, "refraction"), _DEFAULT_IOR)


def _normalize_color_hex(value: str, default: str = _DEFAULT_TINT_COLOR) -> str:
    color = QtGui.QColor(str(value or "").strip() or default)
    if not color.isValid():
        color = QtGui.QColor(default)
    return color.name().lower()


def _material_payload(model) -> dict:
    tint_color = _normalize_color_hex(
        _param_value(model, "tint_color") or _param_value(model, "base_color"),
        _DEFAULT_TINT_COLOR,
    )
    fresnel_color = _normalize_color_hex(
        _param_value(model, "fresnel_color"),
        _DEFAULT_FRESNEL_COLOR,
    )
    return {
        "transparency": float(_clamp_percent(_param_value(model, "transparency"), _DEFAULT_TRANSPARENCY)) / 100.0,
        "ior": _ior_from_model(model),
        "tint_color": tint_color,
        "fresnel_amount": float(_clamp_percent(_param_value(model, "fresnel_amount"), _DEFAULT_FRESNEL_AMOUNT)) / 100.0,
        "fresnel_color": fresnel_color,
    }


def build_ports(node_item) -> None:
    _ensure_param(node_item, "mesh", "")
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    _ensure_param(node_item, "debug_log", "0")
    _ensure_param(node_item, "transparency", str(_DEFAULT_TRANSPARENCY))
    _ensure_param(node_item, "ior", f"{_ior_from_model(getattr(node_item, 'model', None)):.2f}")
    _ensure_param(node_item, "refraction", str(_DEFAULT_REFRACTION))
    _ensure_param(node_item, "tint_color", _DEFAULT_TINT_COLOR)
    _ensure_param(node_item, "fresnel_amount", str(_DEFAULT_FRESNEL_AMOUNT))
    _ensure_param(node_item, "fresnel_color", _DEFAULT_FRESNEL_COLOR)
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [
            "mesh",
            "source",
            "path",
            "debug_log",
            "transparency",
            "ior",
            "refraction",
            "tint_color",
            "fresnel_amount",
            "fresnel_color",
            "base_color",
            "roughness",
            "specular_color",
        ],
    )
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")


class MaterialWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._pending = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        row0 = QtWidgets.QHBoxLayout()
        row0.setContentsMargins(0, 0, 0, 0)
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        row0.addWidget(self._status, 1)
        layout.addLayout(row0, 0)

        tint_row = QtWidgets.QHBoxLayout()
        tint_row.setContentsMargins(0, 0, 0, 0)
        tint_row.addWidget(QtWidgets.QLabel("Tint"), 0)
        self._tint_btn = QtWidgets.QPushButton("")
        self._tint_btn.setFixedHeight(22)
        self._tint_btn.clicked.connect(self._on_tint_clicked)
        tint_row.addWidget(self._tint_btn, 1)
        layout.addLayout(tint_row, 0)

        fresnel_row = QtWidgets.QHBoxLayout()
        fresnel_row.setContentsMargins(0, 0, 0, 0)
        fresnel_row.addWidget(QtWidgets.QLabel("Fresnel"), 0)
        self._fresnel_btn = QtWidgets.QPushButton("")
        self._fresnel_btn.setFixedHeight(22)
        self._fresnel_btn.clicked.connect(self._on_fresnel_clicked)
        fresnel_row.addWidget(self._fresnel_btn, 1)
        layout.addLayout(fresnel_row, 0)

        self._trans_slider, self._trans_value = self._slider_row(layout, "Transparency", self._on_transparency_changed)
        self._ior_spin = self._ior_row(layout)
        self._fresnel_slider, self._fresnel_value = self._slider_row(layout, "Fresnel Amt", self._on_fresnel_changed)

        row3 = QtWidgets.QHBoxLayout()
        row3.setContentsMargins(0, 0, 0, 0)
        row3.addStretch(1)
        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(64)
        self._view_btn.clicked.connect(self._on_view_clicked)
        row3.addWidget(self._view_btn, 0)
        layout.addLayout(row3, 0)

        self._ensure_scene()
        self._sync_from_params()
        QtCore.QTimer.singleShot(0, self._update_inputs)

    def sizeHint(self):
        return QtCore.QSize(224, 156)

    def _slider_row(self, parent_layout, label: str, slot):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QtWidgets.QLabel(label), 0)
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(0, 100)
        slider.valueChanged.connect(slot)
        row.addWidget(slider, 1)
        value_label = QtWidgets.QLabel("0%")
        value_label.setFixedWidth(38)
        value_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        row.addWidget(value_label, 0)
        parent_layout.addLayout(row, 0)
        return slider, value_label

    def _ior_row(self, parent_layout):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QtWidgets.QLabel("IOR"), 0)
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(1.00, 2.50)
        spin.setDecimals(2)
        spin.setSingleStep(0.01)
        spin.setKeyboardTracking(False)
        spin.setAlignment(QtCore.Qt.AlignRight)
        spin.valueChanged.connect(self._on_ior_changed)
        row.addWidget(spin, 1)
        parent_layout.addLayout(row, 0)
        return spin

    def _ensure_scene(self):
        if self._scene is None:
            self._scene = self._node_item.scene()
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._schedule_update)
            except Exception:
                pass
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(self._on_scene_param_changed)
            except Exception:
                pass
        self._scene_connected = True

    def _schedule_update(self, *_args):
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(60, self._update_inputs)

    def _on_scene_param_changed(self, name=None, _params=None):
        if _param_change_relevant(self._node_item, name):
            self._schedule_update()

    def _set_param(self, name: str, value: str, notify_scene: bool = True):
        key = (name or "").strip().lower()
        try:
            current = ""
            for entry in (getattr(self._node_item.model, "params", None) or []):
                if (entry.get("name") or "").strip().lower() == key:
                    current = entry.get("value", "") or ""
                    break
            if current == value:
                return False
        except Exception:
            pass
        try:
            self._node_item._set_param_value(name, value, rebuild=False, notify_scene=notify_scene)
            return True
        except Exception:
            return False

    def _sync_from_params(self):
        model = getattr(self._node_item, "model", None)
        if model is None:
            return
        tint_color = _normalize_color_hex(
            _param_value(model, "tint_color") or _param_value(model, "base_color"),
            _DEFAULT_TINT_COLOR,
        )
        fresnel_color = _normalize_color_hex(
            _param_value(model, "fresnel_color"),
            _DEFAULT_FRESNEL_COLOR,
        )
        trans = _clamp_percent(_param_value(model, "transparency"), _DEFAULT_TRANSPARENCY)
        ior = _ior_from_model(model)
        fresnel_amount = _clamp_percent(_param_value(model, "fresnel_amount"), _DEFAULT_FRESNEL_AMOUNT)
        self._set_color_button(self._tint_btn, tint_color, _DEFAULT_TINT_COLOR)
        self._set_color_button(self._fresnel_btn, fresnel_color, _DEFAULT_FRESNEL_COLOR)
        try:
            self._trans_slider.blockSignals(True)
            self._trans_slider.setValue(int(trans))
        finally:
            self._trans_slider.blockSignals(False)
        self._trans_value.setText(f"{int(trans)}%")
        try:
            self._ior_spin.blockSignals(True)
            self._ior_spin.setValue(float(ior))
        finally:
            self._ior_spin.blockSignals(False)
        try:
            self._fresnel_slider.blockSignals(True)
            self._fresnel_slider.setValue(int(fresnel_amount))
        finally:
            self._fresnel_slider.blockSignals(False)
        self._fresnel_value.setText(f"{int(fresnel_amount)}%")

    def _set_color_button(self, button, color_hex: str, default: str):
        color = QtGui.QColor(_normalize_color_hex(color_hex, default))
        lum = (0.2126 * color.red()) + (0.7152 * color.green()) + (0.0722 * color.blue())
        text_color = "#0f172a" if lum > 140.0 else "#f8fafc"
        button.setText(color.name().upper())
        button.setStyleSheet(
            "QPushButton{background:" + color.name() + ";color:" + text_color + ";"
            "border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
            "QPushButton:hover{border:1px solid #64748b;}"
        )

    def _update_inputs(self):
        self._pending = False
        self._sync_from_params()
        src_item, src_kind, src_path = _resolve_input_item(self._node_item)
        src_path = (src_path or "").strip()
        if src_path:
            self._set_param("source", src_path, notify_scene=False)
            self._set_param("path", src_path, notify_scene=False)
        else:
            self._set_param("source", "", notify_scene=False)
            self._set_param("path", "", notify_scene=False)
        has_link = bool(src_item is not None)
        valid_mesh = bool(src_path) and os.path.exists(src_path) and Path(src_path).suffix.lower() in SUPPORTED_MESH_EXTS
        self._view_btn.setEnabled(bool(valid_mesh or has_link))
        if valid_mesh:
            self._status.setText(Path(src_path).name)
        elif has_link:
            self._status.setText("Waiting for mesh")
        else:
            self._status.setText("No mesh input")

    def _on_transparency_changed(self, value: int):
        self._trans_value.setText(f"{int(value)}%")
        self._set_param("transparency", str(int(value)), notify_scene=True)

    def _on_ior_changed(self, value: float):
        self._set_param("ior", f"{float(value):.2f}", notify_scene=True)

    def _on_fresnel_changed(self, value: int):
        self._fresnel_value.setText(f"{int(value)}%")
        self._set_param("fresnel_amount", str(int(value)), notify_scene=True)

    def _pick_color(self, title: str, current: QtGui.QColor):
        parent = _resolve_window(self._node_item) or self
        dialog = QtWidgets.QColorDialog(parent)
        dialog.setWindowTitle(title)
        dialog.setCurrentColor(current)
        try:
            dialog.setOption(QtWidgets.QColorDialog.DontUseNativeDialog, True)
        except Exception:
            pass
        try:
            dialog.setOption(QtWidgets.QColorDialog.ShowAlphaChannel, False)
        except Exception:
            pass
        try:
            dialog.setModal(True)
        except Exception:
            pass
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return None
        picked = dialog.currentColor()
        if not picked.isValid():
            return None
        return picked.name().lower()

    def _on_tint_clicked(self):
        current = QtGui.QColor(
            _normalize_color_hex(
                self._node_item._param_value("tint_color") or self._node_item._param_value("base_color"),
                _DEFAULT_TINT_COLOR,
            )
        )
        color_hex = self._pick_color("Tint Color", current)
        if not color_hex:
            return
        self._set_color_button(self._tint_btn, color_hex, _DEFAULT_TINT_COLOR)
        self._set_param("tint_color", color_hex, notify_scene=True)

    def _on_fresnel_clicked(self):
        current = QtGui.QColor(
            _normalize_color_hex(
                self._node_item._param_value("fresnel_color"),
                _DEFAULT_FRESNEL_COLOR,
            )
        )
        color_hex = self._pick_color("Fresnel Color", current)
        if not color_hex:
            return
        self._set_color_button(self._fresnel_btn, color_hex, _DEFAULT_FRESNEL_COLOR)
        self._set_param("fresnel_color", color_hex, notify_scene=True)

    def _build_preview_asset(self) -> Optional[dict]:
        return build_material_asset(self._node_item)

    def _on_view_clicked(self):
        asset = self._build_preview_asset()
        if not asset:
            QtWidgets.QMessageBox.warning(
                _resolve_window(self._node_item) or self,
                "Material",
                "Connect a valid mesh-producing node first.",
            )
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if not callable(handler):
            QtWidgets.QMessageBox.warning(
                _resolve_window(self._node_item) or self,
                "Material",
                "3D view is not available.",
            )
            return
        try:
            handler([asset])
        except Exception:
            pass


def render_node_body(node_item, y_cursor: int) -> int:
    inset_x = 6
    inset_top = 2
    inset_bottom = 10
    body = MaterialWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(inset_x, y_cursor + inset_top)
    h = body.sizeHint().height()
    proxy.resize(max(40, int(node_item.width) - (inset_x * 2)), h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + inset_top + h + inset_bottom


MATERIAL_SPEC = Spec(
    stripe_color="#60a5fa",
    render_node_body=render_node_body,
    build_ports=build_ports,
)

# Backward-compatible alias for the original internal name.
MNATERIAL_SPEC = MATERIAL_SPEC


def build_material_asset(node_item) -> Optional[dict]:
    model = getattr(node_item, "model", None)
    material_node = (getattr(model, "name", "") or "").strip() or "material"
    debug_log = _debug_log_enabled(model)
    src_item, src_kind, src_path = _resolve_input_item(node_item)
    src_kind = (src_kind or "").strip().lower()
    src_model = getattr(src_item, "model", None) if src_item is not None else None
    src_node = (getattr(src_model, "name", "") or "").strip()
    if not src_path:
        if debug_log:
            _material_debug_log(
                "material.build.skip_no_input",
                material_node=material_node,
                source_node=src_node,
                source_kind=src_kind,
            )
        return None
    try:
        path = Path(src_path)
    except Exception:
        if debug_log:
            _material_debug_log(
                "material.build.skip_bad_path",
                material_node=material_node,
                source_node=src_node,
                source_kind=src_kind,
                source_path=src_path,
            )
        return None
    if not path.exists() or path.suffix.lower() not in SUPPORTED_MESH_EXTS:
        if debug_log:
            _material_debug_log(
                "material.build.skip_invalid_mesh",
                material_node=material_node,
                source_node=src_node,
                source_kind=src_kind,
                source_path=str(path),
                exists=bool(path.exists()),
                ext=path.suffix.lower(),
            )
        return None

    owner_item = src_item
    owner_model = getattr(src_item, "model", None) if src_item is not None else None
    owner_kind = src_kind
    texture_model = owner_model
    texture_kind = owner_kind

    def _resolve_upstream(item):
        if item is None:
            return None, "", ""
        return _resolve_input_item(item)

    if owner_kind in {"texture", "texture_pro", "texture_layer"}:
        upstream_item, upstream_kind, upstream_path = _resolve_upstream(owner_item)
        if upstream_item is not None and getattr(upstream_item, "model", None) is not None:
            if upstream_kind == "uv_unwrap":
                if upstream_path:
                    path = Path(upstream_path)
                upstream2_item, upstream2_kind, _ = _resolve_upstream(upstream_item)
                if upstream2_item is not None and getattr(upstream2_item, "model", None) is not None:
                    owner_item = upstream2_item
                    owner_model = getattr(upstream2_item, "model", owner_model)
                    owner_kind = upstream2_kind or owner_kind
            else:
                owner_item = upstream_item
                owner_model = getattr(upstream_item, "model", owner_model)
                owner_kind = upstream_kind or owner_kind
                if upstream_path:
                    path = Path(upstream_path)
    elif owner_kind == "uv_unwrap":
        upstream_item, upstream_kind, upstream_path = _resolve_upstream(owner_item)
        if upstream_item is not None and getattr(upstream_item, "model", None) is not None:
            owner_item = upstream_item
            owner_model = getattr(upstream_item, "model", owner_model)
            owner_kind = upstream_kind or owner_kind
            if upstream_path:
                path = Path(upstream_path)

    node_name = (getattr(owner_model, "name", "") or "").strip() or (getattr(getattr(src_item, "model", None), "name", "") or "").strip()
    if not node_name:
        node_name = path.stem or "material"

    texture = ""
    texture_provider = None
    if texture_kind == "texture_pro":
        texture_provider = getattr(texture_model, "_texture_pro_provider", None) if texture_model is not None else None
    elif texture_kind == "texture_layer":
        texture_provider = getattr(texture_model, "_texture_layer_provider", None) if texture_model is not None else None
    if texture_kind in {"texture", "texture_pro"}:
        texture = _param_value(texture_model, "texture") if texture_model is not None else ""
    elif texture_kind == "texture_layer":
        texture = _param_value(texture_model, "texture") if texture_model is not None and path.suffix.lower() == ".obj" else ""
    elif owner_model is not None and path.suffix.lower() == ".obj":
        texture = _param_value(owner_model, "texture")

    asset = {
        "path": str(path),
        "texture": texture,
        "node": node_name,
        "ext": path.suffix.lower(),
        "visible": True,
        "material": _material_payload(getattr(node_item, "model", None)),
        "debug_log": bool(debug_log),
    }
    if texture_provider is not None:
        asset["texture_provider"] = texture_provider
    if debug_log:
        _material_debug_log(
            "material.build.ready",
            material_node=material_node,
            source_node=src_node,
            source_kind=src_kind,
            owner_node=node_name,
            owner_kind=owner_kind,
            path=str(path),
            texture=bool(texture),
            transparency=float(asset["material"].get("transparency", 0.0) or 0.0),
            ior=float(asset["material"].get("ior", 1.0) or 1.0),
            tint_color=str(asset["material"].get("tint_color") or ""),
            fresnel_amount=float(asset["material"].get("fresnel_amount", 0.0) or 0.0),
            fresnel_color=str(asset["material"].get("fresnel_color") or ""),
        )
    return asset
