from __future__ import annotations

import json
from pathlib import Path

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except Exception:
    from PySide2 import QtWidgets, QtCore, QtGui  # type: ignore

from nodes.core import Spec


DEFAULT_PROFILE_POINTS = [
    (0.0, 0.20),
    (0.25, 0.95),
    (0.50, 0.20),
    (0.75, 0.95),
    (1.00, 0.20),
]

DEFAULT_AGE_SCALE_POINTS = [
    (0.0, 0.25),
    (1.0, 1.0),
]


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
    cur = {part.strip().lower() for part in str(raw).split(",") if part.strip()}
    for name in names or []:
        if name:
            cur.add(str(name).strip().lower())
    existing["value"] = ",".join(sorted(cur))
    model.params = params


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _parse_int(raw, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(float(str(raw or "").strip()))
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), int(value)))


def _parse_float(raw, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(str(raw or "").strip())
    except Exception:
        value = float(default)
    return max(float(minimum), min(float(maximum), float(value)))


def _normalize_color(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return "#b7ff6a"
    if not text.startswith("#"):
        text = f"#{text}"
    if len(text) == 4:
        text = "#" + "".join(ch * 2 for ch in text[1:])
    if len(text) != 7:
        return "#b7ff6a"
    try:
        int(text[1:], 16)
    except Exception:
        return "#b7ff6a"
    return text.lower()


def _normalize_profile_points(points) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    for raw in points or []:
        x_val = None
        y_val = None
        if isinstance(raw, dict):
            x_val = raw.get("x")
            y_val = raw.get("y")
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            x_val = raw[0]
            y_val = raw[1]
        try:
            x = _clamp(float(x_val), 0.0, 1.0)
            y = _clamp(float(y_val), 0.0, 1.0)
        except Exception:
            continue
        rows.append((x, y))
    if len(rows) < 2:
        rows = list(DEFAULT_PROFILE_POINTS)
    rows.sort(key=lambda item: (float(item[0]), float(item[1])))
    dedup: list[tuple[float, float]] = []
    for x, y in rows:
        if dedup and abs(float(dedup[-1][0]) - float(x)) < 1.0e-5:
            dedup[-1] = (float(x), float(y))
        else:
            dedup.append((float(x), float(y)))
    if not dedup:
        dedup = list(DEFAULT_PROFILE_POINTS)
    if dedup[0][0] > 0.0:
        dedup.insert(0, (0.0, float(dedup[0][1])))
    else:
        dedup[0] = (0.0, float(dedup[0][1]))
    if dedup[-1][0] < 1.0:
        dedup.append((1.0, float(dedup[-1][1])))
    else:
        dedup[-1] = (1.0, float(dedup[-1][1]))
    if len(dedup) < 2:
        dedup = list(DEFAULT_PROFILE_POINTS)
    return dedup


def _profile_from_json(raw: str) -> list[tuple[float, float]]:
    try:
        payload = json.loads(str(raw or "").strip() or "[]")
    except Exception:
        payload = []
    return _normalize_profile_points(payload)


def _profile_to_json(points) -> str:
    payload = [{"x": float(x), "y": float(y)} for x, y in _normalize_profile_points(points)]
    return json.dumps(payload, separators=(",", ":"))


def trail_asset_config_from_model(model) -> dict:
    profile_points = _profile_from_json(_param_value(model, "profile"))
    raw_age_scale_profile = _param_value(model, "age_scale_profile")
    if raw_age_scale_profile.strip():
        age_scale_points = _profile_from_json(raw_age_scale_profile)
    else:
        age_scale_points = list(DEFAULT_AGE_SCALE_POINTS)
    samples = _parse_int(_param_value(model, "samples"), 28, minimum=4, maximum=1000)
    spawn_rate_raw = _param_value(model, "spawn_rate") or _param_value(model, "frame_step") or "1"
    spawn_rate = _parse_float(spawn_rate_raw, 1.0, minimum=0.01, maximum=24.0)
    substeps = _parse_int(_param_value(model, "substeps"), 1, minimum=1, maximum=64)
    lifespan_default = max(1, int(round(float(samples) * float(spawn_rate))))
    return {
        "enabled": _param_value(model, "enabled").strip() not in {"0", "false", "False", "off", "no"},
        "global_space": _param_value(model, "global_space").strip() in {"1", "true", "True", "on", "yes"},
        "samples": samples,
        "frame_step": max(1, int(round(float(spawn_rate)))),
        "spawn_rate": spawn_rate,
        "substeps": substeps,
        "lifespan": _parse_int(_param_value(model, "lifespan"), lifespan_default, minimum=1, maximum=480),
        "repeats": _parse_int(_param_value(model, "repeats"), 1, minimum=1, maximum=64),
        "radius": _parse_float(_param_value(model, "radius"), 0.35, minimum=0.01, maximum=1000.0),
        "sides": _parse_int(_param_value(model, "sides"), 28, minimum=6, maximum=96),
        "color": _normalize_color(_param_value(model, "color")),
        "line_width": _parse_float(_param_value(model, "line_width"), 2.0, minimum=0.5, maximum=8.0),
        "profile_points": [{"x": float(x), "y": float(y)} for x, y in profile_points],
        "age_scale_min": _parse_float(_param_value(model, "age_scale_min"), 1.0, minimum=0.0, maximum=10.0),
        "age_scale_max": _parse_float(_param_value(model, "age_scale_max"), 1.0, minimum=0.0, maximum=10.0),
        "age_scale_points": [{"x": float(x), "y": float(y)} for x, y in age_scale_points],
    }


def build_ports(node_item) -> None:
    _ensure_param(node_item, "mesh", "")
    _ensure_param(node_item, "instance", "")
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    _ensure_param(node_item, "enabled", "1")
    _ensure_param(node_item, "global_space", "0")
    _ensure_param(node_item, "samples", "28")
    _ensure_param(node_item, "frame_step", "1")
    _ensure_param(node_item, "spawn_rate", "1.0")
    _ensure_param(node_item, "substeps", "1")
    _ensure_param(node_item, "lifespan", "28")
    _ensure_param(node_item, "repeats", "1")
    _ensure_param(node_item, "radius", "0.35")
    _ensure_param(node_item, "sides", "28")
    _ensure_param(node_item, "color", "#b7ff6a")
    _ensure_param(node_item, "line_width", "2.0")
    _ensure_param(node_item, "profile", _profile_to_json(DEFAULT_PROFILE_POINTS))
    _ensure_param(node_item, "age_scale_min", "1.0")
    _ensure_param(node_item, "age_scale_max", "1.0")
    _ensure_param(node_item, "age_scale_profile", _profile_to_json(DEFAULT_AGE_SCALE_POINTS))
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [
            "mesh",
            "instance",
            "source",
            "path",
            "enabled",
            "global_space",
            "samples",
            "frame_step",
            "spawn_rate",
            "substeps",
            "lifespan",
            "repeats",
            "radius",
            "sides",
            "color",
            "line_width",
            "profile",
            "age_scale_min",
            "age_scale_max",
            "age_scale_profile",
        ],
    )
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")
        node_item.ensure_input("instance")


class FxRampWidget(QtWidgets.QWidget):
    pointsChanged = QtCore.Signal(object)

    def __init__(self, points=None, parent=None):
        super().__init__(parent)
        self._points = _normalize_profile_points(points)
        self._drag_index = None
        self.setMinimumHeight(84)
        self.setMouseTracking(True)

    def sizeHint(self):
        return QtCore.QSize(220, 92)

    def points(self) -> list[tuple[float, float]]:
        return list(self._points)

    def set_points(self, points) -> None:
        self._points = _normalize_profile_points(points)
        self.update()

    def _graph_rect(self):
        return QtCore.QRectF(10.0, 8.0, max(40.0, float(self.width()) - 20.0), max(34.0, float(self.height()) - 20.0))

    def _point_to_pos(self, x: float, y: float) -> QtCore.QPointF:
        rect = self._graph_rect()
        px = float(rect.left()) + (_clamp(x, 0.0, 1.0) * float(rect.width()))
        py = float(rect.bottom()) - (_clamp(y, 0.0, 1.0) * float(rect.height()))
        return QtCore.QPointF(px, py)

    def _pos_to_point(self, posf) -> tuple[float, float]:
        rect = self._graph_rect()
        x = 0.0
        y = 0.0
        try:
            if rect.width() > 0.0:
                x = (float(posf.x()) - float(rect.left())) / float(rect.width())
            if rect.height() > 0.0:
                y = (float(rect.bottom()) - float(posf.y())) / float(rect.height())
        except Exception:
            pass
        return (_clamp(x, 0.0, 1.0), _clamp(y, 0.0, 1.0))

    def _event_pos(self, ev):
        try:
            if hasattr(ev, "position"):
                return ev.position()
        except Exception:
            pass
        return ev.pos()

    def _point_index_at(self, posf) -> int | None:
        best_idx = None
        best_d2 = None
        for idx, (x, y) in enumerate(self._points):
            pp = self._point_to_pos(x, y)
            dx = float(pp.x()) - float(posf.x())
            dy = float(pp.y()) - float(posf.y())
            d2 = (dx * dx) + (dy * dy)
            if d2 > 120.0:
                continue
            if best_idx is None or best_d2 is None or d2 < best_d2:
                best_idx = int(idx)
                best_d2 = float(d2)
        return best_idx

    def _emit_points(self) -> None:
        self._points = _normalize_profile_points(self._points)
        self.pointsChanged.emit(list(self._points))
        self.update()

    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        except Exception:
            pass
        rect = self._graph_rect()
        p.fillRect(self.rect(), QtGui.QColor("#0f1216"))
        p.fillRect(rect, QtGui.QColor("#111827"))
        p.setPen(QtGui.QPen(QtGui.QColor(71, 85, 105, 180), 1))
        for frac in (0.25, 0.50, 0.75):
            y = float(rect.top()) + (float(rect.height()) * frac)
            p.drawLine(QtCore.QPointF(rect.left(), y), QtCore.QPointF(rect.right(), y))
        for frac in (0.25, 0.50, 0.75):
            x = float(rect.left()) + (float(rect.width()) * frac)
            p.drawLine(QtCore.QPointF(x, rect.top()), QtCore.QPointF(x, rect.bottom()))
        p.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1))
        p.drawRect(rect)

        if self._points:
            path = QtGui.QPainterPath()
            first = self._point_to_pos(self._points[0][0], self._points[0][1])
            path.moveTo(first)
            for x, y in self._points[1:]:
                path.lineTo(self._point_to_pos(x, y))
            p.setPen(QtGui.QPen(QtGui.QColor("#b7ff6a"), 2))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawPath(path)
            for idx, (x, y) in enumerate(self._points):
                posf = self._point_to_pos(x, y)
                if idx == self._drag_index:
                    p.setPen(QtGui.QPen(QtGui.QColor("#fef08a"), 1.2))
                    p.setBrush(QtGui.QBrush(QtGui.QColor("#facc15")))
                    p.drawEllipse(posf, 5.0, 5.0)
                else:
                    p.setPen(QtGui.QPen(QtGui.QColor(15, 23, 42, 220), 1.0))
                    p.setBrush(QtGui.QBrush(QtGui.QColor("#d9f99d")))
                    p.drawEllipse(posf, 4.0, 4.0)

        p.setPen(QtGui.QPen(QtGui.QColor("#94a3b8"), 1))
        p.drawText(QtCore.QRectF(12.0, 6.0, 24.0, 14.0), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "1")
        p.drawText(
            QtCore.QRectF(12.0, float(rect.bottom()) - 12.0, 24.0, 12.0),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            "0",
        )
        p.end()

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            self._drag_index = self._point_index_at(self._event_pos(ev))
            if self._drag_index is not None:
                ev.accept()
                return
        if ev.button() == QtCore.Qt.RightButton:
            idx = self._point_index_at(self._event_pos(ev))
            if idx is not None and 0 < int(idx) < (len(self._points) - 1):
                del self._points[int(idx)]
                self._drag_index = None
                self._emit_points()
                ev.accept()
                return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._drag_index is None:
            return super().mouseMoveEvent(ev)
        idx = int(self._drag_index)
        x, y = self._pos_to_point(self._event_pos(ev))
        if idx == 0:
            x = 0.0
        elif idx == (len(self._points) - 1):
            x = 1.0
        else:
            prev_x = float(self._points[idx - 1][0]) + 0.01
            next_x = float(self._points[idx + 1][0]) - 0.01
            x = _clamp(x, prev_x, next_x)
        self._points[idx] = (float(x), float(y))
        self.update()
        ev.accept()

    def mouseReleaseEvent(self, ev):
        if self._drag_index is not None:
            self._drag_index = None
            self._emit_points()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() != QtCore.Qt.LeftButton:
            return super().mouseDoubleClickEvent(ev)
        x, y = self._pos_to_point(self._event_pos(ev))
        self._points.append((float(x), float(y)))
        self._emit_points()
        ev.accept()


class FxTrailWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._refresh_pending = False
        self._updating = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self._status = QtWidgets.QLabel("No mesh input.")
        self._status.setMinimumWidth(0)
        self._status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        header.addWidget(self._status, 1)
        layout.addLayout(header)

        toggle_row = QtWidgets.QHBoxLayout()
        toggle_row.setContentsMargins(0, 0, 0, 0)
        toggle_row.setSpacing(10)
        self._global_space = QtWidgets.QCheckBox("Global")
        self._global_space.stateChanged.connect(self._on_global_space_changed)
        toggle_row.addWidget(self._global_space, 0)
        self._enabled = QtWidgets.QCheckBox("Enabled")
        self._enabled.stateChanged.connect(self._on_enabled_changed)
        toggle_row.addWidget(self._enabled, 0)
        toggle_row.addStretch(1)
        layout.addLayout(toggle_row)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        def _mk_int(minimum: int, maximum: int):
            sb = QtWidgets.QSpinBox()
            sb.setRange(int(minimum), int(maximum))
            sb.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            sb.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            sb.setStyleSheet(
                "QSpinBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
                "border-radius:4px;padding:1px 4px;}"
            )
            return sb

        def _mk_float(minimum: float, maximum: float, step: float):
            sb = QtWidgets.QDoubleSpinBox()
            sb.setDecimals(3)
            sb.setRange(float(minimum), float(maximum))
            sb.setSingleStep(float(step))
            sb.setKeyboardTracking(False)
            sb.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            sb.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            sb.setStyleSheet(
                "QDoubleSpinBox{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
                "border-radius:4px;padding:1px 4px;}"
            )
            return sb

        self._spawn_rate = _mk_float(0.01, 24.0, 0.05)
        self._substeps = _mk_int(1, 64)
        self._lifespan = _mk_int(1, 480)
        self._repeats = _mk_int(1, 64)
        self._sides = _mk_int(6, 96)
        self._radius = _mk_float(0.01, 1000.0, 0.01)
        self._line_width = _mk_float(0.5, 8.0, 0.1)
        self._age_scale_min = _mk_float(0.0, 10.0, 0.05)
        self._age_scale_max = _mk_float(0.0, 10.0, 0.05)
        self._color = QtWidgets.QLineEdit()
        self._color.setPlaceholderText("#b7ff6a")
        self._color.setStyleSheet(
            "QLineEdit{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;"
            "border-radius:4px;padding:2px 6px;}"
        )
        self._color.editingFinished.connect(self._on_color_changed)

        grid.addWidget(QtWidgets.QLabel("Spawn"), 0, 0)
        grid.addWidget(self._spawn_rate, 0, 1)
        grid.addWidget(QtWidgets.QLabel("Substeps"), 0, 2)
        grid.addWidget(self._substeps, 0, 3)
        grid.addWidget(QtWidgets.QLabel("Life"), 1, 0)
        grid.addWidget(self._lifespan, 1, 1)
        grid.addWidget(QtWidgets.QLabel("Repeat"), 1, 2)
        grid.addWidget(self._repeats, 1, 3)
        grid.addWidget(QtWidgets.QLabel("Sides"), 2, 0)
        grid.addWidget(self._sides, 2, 1)
        grid.addWidget(QtWidgets.QLabel("Radius"), 2, 2)
        grid.addWidget(self._radius, 2, 3)
        grid.addWidget(QtWidgets.QLabel("Line"), 3, 0)
        grid.addWidget(self._line_width, 3, 1)
        grid.addWidget(QtWidgets.QLabel("Color"), 3, 2)
        grid.addWidget(self._color, 3, 3)
        grid.addWidget(QtWidgets.QLabel("Age Min"), 4, 0)
        grid.addWidget(self._age_scale_min, 4, 1)
        grid.addWidget(QtWidgets.QLabel("Age Max"), 4, 2)
        grid.addWidget(self._age_scale_max, 4, 3)
        layout.addLayout(grid)

        preset_row = QtWidgets.QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(4)
        preset_label = QtWidgets.QLabel("Pattern")
        preset_label.setStyleSheet("color:#cbd5e1;")
        preset_row.addWidget(preset_label, 0)
        preset_row.addStretch(1)
        reset_btn = QtWidgets.QPushButton("Wave")
        reset_btn.setFixedHeight(20)
        reset_btn.setStyleSheet(
            "QPushButton{background:#1e293b;color:#e2e8f0;border-radius:4px;padding:1px 8px;}"
            "QPushButton:hover{background:#334155;}"
        )
        reset_btn.clicked.connect(self._reset_profile_wave)
        preset_row.addWidget(reset_btn, 0)
        layout.addLayout(preset_row)

        self._profile = FxRampWidget(DEFAULT_PROFILE_POINTS)
        self._profile.setStyleSheet("background:#0f1216;border:1px solid #334155;border-radius:6px;")
        self._profile.pointsChanged.connect(self._on_profile_changed)
        layout.addWidget(self._profile, 0)

        scale_row = QtWidgets.QHBoxLayout()
        scale_row.setContentsMargins(0, 0, 0, 0)
        scale_row.setSpacing(4)
        scale_label = QtWidgets.QLabel("Age Scale")
        scale_label.setStyleSheet("color:#cbd5e1;")
        scale_row.addWidget(scale_label, 0)
        scale_row.addStretch(1)
        scale_reset_btn = QtWidgets.QPushButton("Linear")
        scale_reset_btn.setFixedHeight(20)
        scale_reset_btn.setStyleSheet(
            "QPushButton{background:#1e293b;color:#e2e8f0;border-radius:4px;padding:1px 8px;}"
            "QPushButton:hover{background:#334155;}"
        )
        scale_reset_btn.clicked.connect(self._reset_age_scale_profile_linear)
        scale_row.addWidget(scale_reset_btn, 0)
        layout.addLayout(scale_row)

        self._age_scale_profile = FxRampWidget(DEFAULT_AGE_SCALE_POINTS)
        self._age_scale_profile.setStyleSheet("background:#0f1216;border:1px solid #334155;border-radius:6px;")
        self._age_scale_profile.pointsChanged.connect(self._on_age_scale_profile_changed)
        layout.addWidget(self._age_scale_profile, 0)

        hint = QtWidgets.QLabel("Drag points. Double-click to add. Right-click to remove.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#64748b;font-size:10px;")
        layout.addWidget(hint, 0)
        age_hint = QtWidgets.QLabel("Pattern sets each ring's base size by spawn order. Age Scale multiplies that size over the ring lifetime.")
        age_hint.setWordWrap(True)
        age_hint.setStyleSheet("color:#64748b;font-size:10px;")
        layout.addWidget(age_hint, 0)
        spawn_hint = QtWidgets.QLabel("Spawn is ring spacing in frames. Values below 1 need higher Substeps.")
        spawn_hint.setWordWrap(True)
        spawn_hint.setStyleSheet("color:#64748b;font-size:10px;")
        layout.addWidget(spawn_hint, 0)

        for widget, key, is_float in (
            (self._spawn_rate, "spawn_rate", True),
            (self._substeps, "substeps", False),
            (self._lifespan, "lifespan", False),
            (self._repeats, "repeats", False),
            (self._sides, "sides", False),
            (self._radius, "radius", True),
            (self._line_width, "line_width", True),
            (self._age_scale_min, "age_scale_min", True),
            (self._age_scale_max, "age_scale_max", True),
        ):
            if is_float:
                widget.valueChanged.connect(lambda value, name=key: self._set_param(name, f"{float(value):.3f}"))
            else:
                widget.valueChanged.connect(lambda value, name=key: self._set_param(name, str(int(value))))

        self._sync_from_model()
        self._ensure_scene()
        self._schedule_refresh()

    def sizeHint(self):
        return QtCore.QSize(252, 438)

    def _set_param(self, name: str, value: str, *, notify_scene: bool = True) -> None:
        if self._updating:
            return
        try:
            current = _param_value(getattr(self._node_item, "model", None), name)
            if current == value:
                return
            self._node_item._set_param_value(name, value, rebuild=False, notify_scene=notify_scene)
        except Exception:
            pass

    def _ensure_scene(self):
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None or self._scene_connected:
            return
        try:
            if hasattr(self._scene, "linksChanged"):
                self._scene.linksChanged.connect(self._schedule_refresh)
            if hasattr(self._scene, "paramChanged"):
                self._scene.paramChanged.connect(lambda *_: self._schedule_refresh())
            self._scene_connected = True
        except Exception:
            pass

    def _schedule_refresh(self):
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QtCore.QTimer.singleShot(0, self._refresh_input_state)

    def _resolve_input(self, port_names=None):
        scene = self._scene
        if scene is None:
            return None, "", ""

        def _trace(item, depth=0, visited=None):
            if item is None or depth > 8:
                return None, "", ""
            if visited is None:
                visited = set()
            if item in visited:
                return None, "", ""
            visited.add(item)
            model = getattr(item, "model", None)
            if model is None:
                return None, "", ""
            kind = (getattr(model, "kind", "") or "").strip().lower()
            if kind in {"switch", "fx", "fx_trail", "transforms", "mnaterial", "material", "texture", "texture_pro", "texture_layer", "uv_unwrap"}:
                try:
                    edges = list(scene._ordered_in_edges(item))
                except Exception:
                    try:
                        edges = list(scene._in_edges(item))
                    except Exception:
                        edges = []
                if edges:
                    return _trace(getattr(edges[0], "src", None), depth + 1, visited)
            path = _param_value(model, "path")
            return item, kind, path

        try:
            in_edges = list(scene._ordered_in_edges(self._node_item))
        except Exception:
            try:
                in_edges = list(scene._in_edges(self._node_item))
            except Exception:
                in_edges = []
        chosen = None
        wanted = {str(name).strip().lower() for name in (port_names or []) if str(name).strip()}
        if wanted:
            for edge in in_edges:
                name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
                if (name or "").strip().lower() in wanted:
                    chosen = edge
                    break
            if chosen is None:
                return None, "", ""
        if chosen is None:
            for edge in in_edges:
                name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
                if (name or "").strip().lower() in {"mesh", "path"}:
                    chosen = edge
                    break
        if chosen is None:
            for edge in in_edges:
                name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
                if not (name or "").strip():
                    chosen = edge
                    break
        if chosen is None and in_edges:
            chosen = in_edges[0]
        if chosen is None:
            return None, "", ""
        return _trace(getattr(chosen, "src", None), 0, set())

    def _refresh_input_state(self):
        self._refresh_pending = False
        self._ensure_scene()
        src_item, _kind, src_path = self._resolve_input()
        src_model = getattr(src_item, "model", None) if src_item is not None else None
        owner_name = (getattr(src_model, "name", "") or "").strip()
        display_name = owner_name or (Path(src_path).name if src_path else "")
        inst_item, _inst_kind, inst_path = self._resolve_input({"instance"})
        inst_model = getattr(inst_item, "model", None) if inst_item is not None else None
        inst_name = (getattr(inst_model, "name", "") or "").strip() or (Path(inst_path).name if inst_path else "")
        if src_path:
            spawn_text = inst_name or "rings"
            self._status.setText(f"Target: {display_name or 'mesh'} | Spawn: {spawn_text}")
        else:
            self._status.setText("No mesh input.")
        self._set_param("source", src_path, notify_scene=False)
        self._set_param("path", src_path, notify_scene=True)

    def _sync_from_model(self):
        model = getattr(self._node_item, "model", None)
        if model is None:
            return
        profile = _profile_from_json(_param_value(model, "profile"))
        raw_age_scale_profile = _param_value(model, "age_scale_profile")
        if raw_age_scale_profile.strip():
            age_scale_profile = _profile_from_json(raw_age_scale_profile)
        else:
            age_scale_profile = list(DEFAULT_AGE_SCALE_POINTS)
        samples = _parse_int(_param_value(model, "samples"), 28, minimum=4, maximum=1000)
        spawn_rate = _parse_float(
            _param_value(model, "spawn_rate") or _param_value(model, "frame_step") or "1",
            1.0,
            minimum=0.01,
            maximum=24.0,
        )
        substeps = _parse_int(_param_value(model, "substeps"), 1, minimum=1, maximum=64)
        lifespan_default = max(1, int(round(float(samples) * float(spawn_rate))))
        try:
            self._updating = True
            self._enabled.blockSignals(True)
            self._global_space.blockSignals(True)
            self._spawn_rate.blockSignals(True)
            self._substeps.blockSignals(True)
            self._lifespan.blockSignals(True)
            self._repeats.blockSignals(True)
            self._sides.blockSignals(True)
            self._radius.blockSignals(True)
            self._line_width.blockSignals(True)
            self._age_scale_min.blockSignals(True)
            self._age_scale_max.blockSignals(True)
            self._enabled.setChecked(_param_value(model, "enabled").strip() not in {"0", "false", "False", "off", "no"})
            self._global_space.setChecked(_param_value(model, "global_space").strip() in {"1", "true", "True", "on", "yes"})
            self._spawn_rate.setValue(spawn_rate)
            self._substeps.setValue(substeps)
            self._lifespan.setValue(_parse_int(_param_value(model, "lifespan"), lifespan_default, minimum=1, maximum=480))
            self._repeats.setValue(_parse_int(_param_value(model, "repeats"), 1, minimum=1, maximum=64))
            self._sides.setValue(_parse_int(_param_value(model, "sides"), 28, minimum=6, maximum=96))
            self._radius.setValue(_parse_float(_param_value(model, "radius"), 0.35, minimum=0.01, maximum=1000.0))
            self._line_width.setValue(_parse_float(_param_value(model, "line_width"), 2.0, minimum=0.5, maximum=8.0))
            self._age_scale_min.setValue(_parse_float(_param_value(model, "age_scale_min"), 1.0, minimum=0.0, maximum=10.0))
            self._age_scale_max.setValue(_parse_float(_param_value(model, "age_scale_max"), 1.0, minimum=0.0, maximum=10.0))
            self._color.setText(_normalize_color(_param_value(model, "color")))
            self._profile.set_points(profile)
            self._age_scale_profile.set_points(age_scale_profile)
        finally:
            for widget in (
                self._enabled,
                self._global_space,
                self._spawn_rate,
                self._substeps,
                self._lifespan,
                self._repeats,
                self._sides,
                self._radius,
                self._line_width,
                self._age_scale_min,
                self._age_scale_max,
            ):
                try:
                    widget.blockSignals(False)
                except Exception:
                    pass
            self._updating = False

    def _on_enabled_changed(self, state: int):
        self._set_param("enabled", "1" if bool(state) else "0")

    def _on_global_space_changed(self, state: int):
        self._set_param("global_space", "1" if bool(state) else "0")

    def _on_color_changed(self):
        color = _normalize_color(self._color.text())
        self._color.setText(color)
        self._set_param("color", color)

    def _on_profile_changed(self, points):
        self._set_param("profile", _profile_to_json(points))

    def _on_age_scale_profile_changed(self, points):
        self._set_param("age_scale_profile", _profile_to_json(points))

    def _reset_profile_wave(self):
        self._profile.set_points(DEFAULT_PROFILE_POINTS)
        self._set_param("profile", _profile_to_json(DEFAULT_PROFILE_POINTS))

    def _reset_age_scale_profile_linear(self):
        self._age_scale_profile.set_points(DEFAULT_AGE_SCALE_POINTS)
        self._set_param("age_scale_profile", _profile_to_json(DEFAULT_AGE_SCALE_POINTS))


def render_node_body(node_item, y_cursor: int) -> int:
    inset_x = 8
    inset_top = 2
    inset_bottom = 22
    try:
        before = {
            (entry.get("name") or "").strip().lower()
            for entry in (getattr(getattr(node_item, "model", None), "params", None) or [])
            if isinstance(entry, dict)
        }
        build_ports(node_item)
        after = {
            (entry.get("name") or "").strip().lower()
            for entry in (getattr(getattr(node_item, "model", None), "params", None) or [])
            if isinstance(entry, dict)
        }
        if after != before and hasattr(node_item, "_rebuild_deferred"):
            node_item._rebuild_deferred()
    except Exception:
        pass
    try:
        setattr(node_item, "_show_default_input_with_named", True)
    except Exception:
        pass
    body = FxTrailWidget(node_item)
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


FX_SPEC = Spec(
    stripe_color="#84cc16",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
