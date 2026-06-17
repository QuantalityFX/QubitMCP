from __future__ import annotations

import math
from typing import Any, Optional

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from nodes.core import Spec


CURVE_TYPES = [
    ("Line", "line"),
    ("Circle", "circle"),
    ("Spiral", "spiral"),
]
CURVE_BODY_H = 34
CURVE_NODE_W = 220


def _param_value(model, name: str, default: str = "") -> str:
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value", default) or default)
    return default


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if not isinstance(params, list):
        params = list(params or [])
        try:
            setattr(model, "params", params)
        except Exception:
            return
    key = (name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _set_param(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    setter = getattr(node_item, "_set_param_value", None)
    if callable(setter):
        try:
            setter(name, str(value), rebuild=False, notify_scene=bool(notify_scene))
            return
        except Exception:
            pass
    _ensure_param(node_item, name, str(value))
    model = getattr(node_item, "model", None)
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value)
            return


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    entry = None
    for param in params:
        if isinstance(param, dict) and (param.get("name") or "").strip().lower() == "__ui_hidden_params":
            entry = param
            break
    if entry is None:
        entry = {"name": "__ui_hidden_params", "value": ""}
        params.append(entry)
    hidden = {part.strip().lower() for part in str(entry.get("value") or "").split(",") if part.strip()}
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    try:
        setattr(model, "params", params)
    except Exception:
        pass


def _resolve_window(node_item):
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
        active = QtWidgets.QApplication.activeWindow()
        if active is not None and active.isWindow():
            return active
    except Exception:
        pass
    return None


def _curve_points(curve_type: str) -> list[list[float]]:
    key = str(curve_type or "").strip().lower()
    if key == "circle":
        points = []
        segments = 96
        radius = 0.75
        for idx in range(segments + 1):
            angle = (2.0 * math.pi * float(idx)) / float(segments)
            points.append([radius * math.cos(angle), 0.0, radius * math.sin(angle)])
        return points
    if key == "spiral":
        points = []
        segments = 144
        turns = 3.0
        for idx in range(segments + 1):
            t = float(idx) / float(segments)
            angle = 2.0 * math.pi * turns * t
            radius = 0.15 + (0.65 * t)
            y = -0.65 + (1.3 * t)
            points.append([radius * math.cos(angle), y, radius * math.sin(angle)])
        return points
    return [[-0.8, 0.0, 0.0], [0.8, 0.0, 0.0]]


def _points_to_line_points(points: list[list[float]]) -> list[list[float]]:
    rows: list[list[float]] = []
    for idx in range(max(0, len(points) - 1)):
        rows.append(points[idx])
        rows.append(points[idx + 1])
    return rows


def build_curve_scene_asset(node_item) -> dict[str, Any]:
    model = getattr(node_item, "model", None)
    curve_type = _param_value(model, "curve_type", "line").strip().lower() or "line"
    valid = {key for _label, key in CURVE_TYPES}
    if curve_type not in valid:
        curve_type = "line"
    points = _curve_points(curve_type)
    line_points = _points_to_line_points(points)
    node_name = str(getattr(model, "name", "") or "curve").strip() or "curve"
    return {
        "kind": "curve",
        "node": node_name,
        "visible": True,
        "curve_type": curve_type,
        "points": points,
        "line_points": line_points,
        "point_count": int(len(points)),
        "line_segment_count": int(len(line_points) // 2),
        "line_width": 3.0,
        "color": (1.0, 1.0, 1.0, 1.0),
    }


class CurveWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._combo = QtWidgets.QComboBox()
        self._combo.setMinimumWidth(0)
        self._combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        try:
            view = QtWidgets.QListView()
            view.setMouseTracking(True)
            view.setUniformItemSizes(True)
            self._combo.setView(view)
        except Exception:
            pass
        self._combo.setStyleSheet(
            "QComboBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:6px;padding:2px 6px;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{"
            "  background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "  outline:0px;}"
            "QComboBox QAbstractItemView::item{padding:6px 10px;}"
            "QComboBox QAbstractItemView::item:hover{background:#1f2937;}"
            "QComboBox QAbstractItemView::item:selected{background:#22d3ee;color:#0f1216;}"
        )
        for label, key in CURVE_TYPES:
            self._combo.addItem(label, key)
        layout.addWidget(self._combo, 1)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setFixedWidth(64)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        layout.addWidget(self._view_btn, 0)

        model = getattr(self._node_item, "model", None)
        _ensure_param(self._node_item, "curve_type", "line")
        _ensure_hidden_params(model, ["curve_type"])

        current = _param_value(model, "curve_type", "line").strip().lower() or "line"
        idx = self._combo.findData(current)
        self._combo.setCurrentIndex(max(0, idx))
        self._combo.currentIndexChanged.connect(self._on_curve_changed)

    def sizeHint(self):
        return QtCore.QSize(CURVE_NODE_W, CURVE_BODY_H)

    def _on_curve_changed(self):
        curve_type = str(self._combo.currentData() or self._combo.currentText() or "line").strip().lower()
        _set_param(self._node_item, "curve_type", curve_type, notify_scene=True)

    def _on_view_clicked(self):
        asset = build_curve_scene_asset(self._node_item)
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if callable(handler):
            try:
                handler([asset], frame=True)
            except TypeError:
                handler([asset])


def render_node_body(node_item, y_cursor: int) -> int:
    body = CurveWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    h = body.sizeHint().height()
    proxy.resize(node_item.width, h)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + h


def build_ports(node_item) -> None:
    _ensure_param(node_item, "curve_type", "line")
    _ensure_hidden_params(getattr(node_item, "model", None), ["curve_type"])


CURVE_SPEC = Spec(
    stripe_color="#22d3ee",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "CURVE_SPEC",
    "build_curve_scene_asset",
    "render_node_body",
    "build_ports",
]
