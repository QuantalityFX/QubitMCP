from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

from .music_effects_spec import (
    _connected_input_item,
    _ensure_hidden_params,
    _ensure_param,
    _node_kind,
    _node_name,
    _param_bool,
    _param_float,
    _param_value,
    _resolve_window,
    _set_param,
)


KIND_ALIASES = {
    "colorize",
    "splat_colorize",
    "splat colorize",
    "fx_splat_colorize",
    "fx splat colorize",
    "gaussian_colorize",
    "gaussian colorize",
}

_SKINNED_SPLAT_PROXY_KIND_ALIASES = {
    "skinned_splat_proxy",
    "skinned splat proxy",
    "skinnedsplatproxy",
    "fbx_to_skinned_splat_proxy",
    "fbx skinned splat proxy",
}
_FX_SPLAT_PHYSICS_KIND_ALIASES = {
    "fx_splat_physics",
    "fx splat physics",
    "splat_physics",
    "splat physics",
    "splatphysics",
}
_FX_SPLAT_FX_KIND_ALIASES = {
    "fx_splat_fx",
    "fx splat fx",
    "splat_fx",
    "splat fx",
    "fx_splat_glow",
    "fx splat glow",
    "splat_glow",
    "splat glow",
    "splatglow",
}

SPLAT_COLORIZE_NODE_W = 288
SPLAT_COLORIZE_BODY_INSET_X = 8
SPLAT_COLORIZE_BODY_INSET_TOP = 2
SPLAT_COLORIZE_BODY_INSET_BOTTOM = 4
SPLAT_COLORIZE_WIDGET_HINT_H = 246
SPLAT_COLORIZE_NODE_BODY_H = (
    SPLAT_COLORIZE_BODY_INSET_TOP
    + SPLAT_COLORIZE_WIDGET_HINT_H
    + SPLAT_COLORIZE_BODY_INSET_BOTTOM
)

_DEFAULT_LOW = "#1d4ed8"
_DEFAULT_MID = "#14b8a6"
_DEFAULT_HIGH = "#f59e0b"


@dataclass(frozen=True)
class SplatColorizeBuildOutcome:
    asset: Optional[Dict[str, Any]]
    status: str
    detail: str


def _repo_logs_dir() -> Path:
    try:
        root = Path(__file__).resolve().parents[2]
    except Exception:
        root = Path.cwd()
    out = root / "logs"
    try:
        out.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return out


def _debug_enabled(model) -> bool:
    return _param_bool(model, "debug_log", False)


def _debug_log(model, event: str, **fields) -> None:
    if not _debug_enabled(model):
        return
    record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": str(event or "")}
    record.update(fields or {})
    try:
        with (_repo_logs_dir() / "splat_colorize_debug.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")
    except Exception:
        pass


def _connected_splat_item(node_item):
    item = _connected_input_item(node_item, {"splats", "splat", "mesh", "source", "path"})
    if item is not None:
        return item
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is None:
        return None
    try:
        edges = list(scene._ordered_in_edges(node_item))
    except Exception:
        try:
            edges = list(scene._in_edges(node_item))
        except Exception:
            edges = []
    return getattr(edges[0], "src", None) if edges else None


def _source_asset_from_item(source_item) -> tuple[Optional[Dict[str, Any]], str]:
    if source_item is None:
        return None, "Connect this node after a Skinned Splat Proxy or Splat FX node."
    kind = _node_kind(source_item)
    if kind in KIND_ALIASES:
        outcome = build_splat_colorize_scene_asset(source_item)
        return outcome.asset, outcome.detail
    if kind in _FX_SPLAT_PHYSICS_KIND_ALIASES:
        try:
            from nodes.fx import splat_physics_spec as splat_physics_spec  # type: ignore

            outcome = splat_physics_spec.build_splat_physics_scene_asset(source_item)
            return getattr(outcome, "asset", None), str(getattr(outcome, "detail", "") or "")
        except Exception as exc:
            return None, f"FX Splat Physics asset build failed: {exc}"
    if kind in _FX_SPLAT_FX_KIND_ALIASES:
        try:
            from nodes.fx import splat_fx_spec as splat_fx_spec  # type: ignore

            outcome = splat_fx_spec.build_splat_fx_scene_asset(source_item)
            return getattr(outcome, "asset", None), str(getattr(outcome, "detail", "") or "")
        except Exception as exc:
            return None, f"FX Splat FX asset build failed: {exc}"
    if kind not in _SKINNED_SPLAT_PROXY_KIND_ALIASES:
        return None, f"Unsupported input node kind: {kind or '<none>'}."
    try:
        from nodes.skinned_splat_proxy import spec as proxy_spec  # type: ignore

        build_asset = getattr(proxy_spec, "build_skinned_splat_proxy_scene_asset", None)
        outcome = build_asset(source_item, generate=False) if callable(build_asset) else None
        asset = getattr(outcome, "asset", None)
        detail = str(getattr(outcome, "detail", "") or "")
    except Exception as exc:
        return None, f"Skinned Splat Proxy asset build failed: {exc}"
    if not isinstance(asset, dict):
        return None, detail or "Skinned Splat Proxy did not produce a valid asset."
    render_proxy = asset.get("render_proxy") if isinstance(asset.get("render_proxy"), dict) else None
    if not isinstance(render_proxy, dict):
        return None, "Input asset does not contain a skinned splat render proxy."
    if str(render_proxy.get("type") or "").strip().lower() not in {"skinned_splat", "skinned_gaussian_splat"}:
        return None, "Input render proxy is not a skinned splat proxy."
    return dict(asset), detail


def _hex_to_rgb(value: str, fallback: str) -> list[float]:
    color = QtGui.QColor(str(value or fallback).strip())
    if not color.isValid():
        color = QtGui.QColor(fallback)
    return [
        float(color.redF()),
        float(color.greenF()),
        float(color.blueF()),
    ]


def _clean_metric(value: str) -> str:
    metric = str(value or "thickness").strip().lower()
    if metric in {"thick", "feature", "feature_radius"}:
        return "thickness"
    if metric in {"dense", "inverse_radius"}:
        return "density"
    if metric not in {"thickness", "density", "radius", "area", "height_y"}:
        return "thickness"
    return metric


def splat_colorize_config_from_model(model) -> Dict[str, Any]:
    low_pct = _param_float(model, "low_percentile", 2.0, minimum=0.0, maximum=99.0)
    high_pct = _param_float(model, "high_percentile", 98.0, minimum=1.0, maximum=100.0)
    if high_pct <= low_pct:
        high_pct = min(100.0, low_pct + 1.0)
    return {
        "schema": "qubit.splat_colorize.v1",
        "enabled": _param_bool(model, "enabled", True),
        "metric": _clean_metric(_param_value(model, "metric") or "thickness"),
        "ramp": [
            {"position": 0.0, "color": _hex_to_rgb(_param_value(model, "low_color"), _DEFAULT_LOW)},
            {"position": 0.5, "color": _hex_to_rgb(_param_value(model, "mid_color"), _DEFAULT_MID)},
            {"position": 1.0, "color": _hex_to_rgb(_param_value(model, "high_color"), _DEFAULT_HIGH)},
        ],
        "normalize": {
            "low_percentile": float(low_pct),
            "high_percentile": float(high_pct),
            "invert": _param_bool(model, "invert", False),
            "gamma": _param_float(model, "gamma", 1.0, minimum=0.05, maximum=8.0),
        },
        "blend": _param_float(model, "blend", 1.0, minimum=0.0, maximum=1.0),
    }


def build_splat_colorize_scene_asset(node_item) -> SplatColorizeBuildOutcome:
    model = getattr(node_item, "model", None)
    source_item = _connected_splat_item(node_item)
    source_asset, source_detail = _source_asset_from_item(source_item)
    if not isinstance(source_asset, dict):
        _debug_log(model, "resolve_source_failed", source=_node_name(source_item), error=source_detail)
        return SplatColorizeBuildOutcome(None, "error", source_detail or "No supported splat input.")

    asset = dict(source_asset)
    render_proxy = asset.get("render_proxy") if isinstance(asset.get("render_proxy"), dict) else {}
    render_proxy = dict(render_proxy)
    render_proxy["splat_colorize"] = splat_colorize_config_from_model(model)
    asset["render_proxy"] = render_proxy
    asset["splat_colorize_node"] = str(getattr(model, "name", "") or "")
    asset["kind"] = "colorize"
    if _debug_enabled(model):
        asset["debug_log"] = True
    status = "ok"
    detail = "Colorize ready."
    if str(render_proxy.get("status") or "").strip().lower() == "not_generated":
        status = "warning"
        detail = source_detail or "Proxy files are not generated yet."
    _debug_log(
        model,
        "asset_ready",
        source=_node_name(source_item),
        status=status,
        colorize=render_proxy.get("splat_colorize", {}),
    )
    return SplatColorizeBuildOutcome(asset, status, detail)


def build_ports(node_item) -> None:
    for name, default in (
        ("splats", ""),
        ("source", ""),
        ("path", ""),
        ("enabled", "1"),
        ("metric", "thickness"),
        ("low_color", _DEFAULT_LOW),
        ("mid_color", _DEFAULT_MID),
        ("high_color", _DEFAULT_HIGH),
        ("low_percentile", "2.0"),
        ("high_percentile", "98.0"),
        ("invert", "0"),
        ("gamma", "1.0"),
        ("blend", "1.0"),
        ("debug_log", "0"),
    ):
        _ensure_param(node_item, name, default)
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [
            "splats",
            "source",
            "path",
            "enabled",
            "metric",
            "low_color",
            "mid_color",
            "high_color",
            "low_percentile",
            "high_percentile",
            "invert",
            "gamma",
            "blend",
            "debug_log",
        ],
    )
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("splats")


class SplatColorizeWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._pending = False
        self._updating = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("Connect splats")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setWordWrap(True)
        layout.addWidget(self._status, 0)

        self._enabled = QtWidgets.QCheckBox("Enabled")
        self._enabled.stateChanged.connect(self._on_enabled_changed)
        layout.addWidget(self._enabled, 0)

        metric_row = QtWidgets.QHBoxLayout()
        metric_row.setContentsMargins(0, 0, 0, 0)
        metric_row.addWidget(QtWidgets.QLabel("Metric"), 0)
        self._metric = QtWidgets.QComboBox()
        for key, label in (
            ("thickness", "Thickness"),
            ("density", "Density"),
            ("radius", "Radius"),
            ("area", "Area"),
            ("height_y", "Height Y"),
        ):
            self._metric.addItem(label, key)
        self._metric.currentIndexChanged.connect(self._on_metric_changed)
        metric_row.addWidget(self._metric, 1)
        layout.addLayout(metric_row, 0)

        ramp_row = QtWidgets.QHBoxLayout()
        ramp_row.setContentsMargins(0, 0, 0, 0)
        ramp_row.setSpacing(5)
        self._low_color = self._color_button("Low", "low_color")
        self._mid_color = self._color_button("Mid", "mid_color")
        self._high_color = self._color_button("High", "high_color")
        ramp_row.addWidget(self._low_color, 1)
        ramp_row.addWidget(self._mid_color, 1)
        ramp_row.addWidget(self._high_color, 1)
        layout.addLayout(ramp_row, 0)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        def _box(minimum: float, maximum: float, step: float, decimals: int = 2):
            box = QtWidgets.QDoubleSpinBox()
            box.setDecimals(int(decimals))
            box.setRange(float(minimum), float(maximum))
            box.setSingleStep(float(step))
            box.setKeyboardTracking(False)
            box.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            box.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            return box

        self._low_pct = _box(0.0, 99.0, 1.0)
        self._high_pct = _box(1.0, 100.0, 1.0)
        self._gamma = _box(0.05, 8.0, 0.05)
        self._blend = _box(0.0, 1.0, 0.05)
        for row, (label, widget) in enumerate(
            (
                ("Low %", self._low_pct),
                ("High %", self._high_pct),
                ("Gamma", self._gamma),
                ("Blend", self._blend),
            )
        ):
            grid.addWidget(QtWidgets.QLabel(label), row // 2, (row % 2) * 2)
            grid.addWidget(widget, row // 2, ((row % 2) * 2) + 1)
        layout.addLayout(grid, 0)

        self._invert = QtWidgets.QCheckBox("Invert ramp")
        self._invert.stateChanged.connect(self._on_invert_changed)
        layout.addWidget(self._invert, 0)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setMinimumHeight(24)
        self._view_btn.clicked.connect(self._on_view_clicked)
        layout.addWidget(self._view_btn, 0)

        for widget, key in (
            (self._low_pct, "low_percentile"),
            (self._high_pct, "high_percentile"),
            (self._gamma, "gamma"),
            (self._blend, "blend"),
        ):
            widget.valueChanged.connect(lambda value, name=key: self._set_param(name, f"{float(value):.3f}"))

        self._sync_from_params()
        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._refresh_status)

    def sizeHint(self):
        return QtCore.QSize(236, SPLAT_COLORIZE_WIDGET_HINT_H)

    def _color_button(self, label: str, param_name: str):
        button = QtWidgets.QPushButton(label)
        button.setMinimumHeight(24)
        button.setProperty("param_name", param_name)
        button.clicked.connect(lambda _checked=False, btn=button: self._choose_color(btn))
        return button

    def _style_color_button(self, button, color_hex: str) -> None:
        color = QtGui.QColor(color_hex)
        if not color.isValid():
            color = QtGui.QColor("#334155")
        text = "#0f172a" if color.lightnessF() > 0.62 else "#f8fafc"
        button.setStyleSheet(
            "QPushButton{"
            f"background:{color.name(QtGui.QColor.HexRgb)};"
            f"color:{text};"
            "border:1px solid #475569;border-radius:4px;padding:2px 6px;"
            "}"
        )

    def _ensure_scene(self) -> None:
        if self._scene is None:
            try:
                self._scene = self._node_item.scene()
            except Exception:
                self._scene = None
        if self._scene is None:
            return
        try:
            if hasattr(self._scene, "linksChanged"):
                self._scene.linksChanged.connect(self._schedule_refresh)
            if hasattr(self._scene, "paramChanged"):
                self._scene.paramChanged.connect(self._on_scene_param_changed)
        except Exception:
            pass

    def _on_scene_param_changed(self, name=None, _params=None):
        if _param_change_relevant(self._node_item, name):
            self._schedule_refresh()

    def _schedule_refresh(self, *_args):
        if self._pending:
            return
        self._pending = True
        QtCore.QTimer.singleShot(80, self._refresh_status)

    def _set_param(self, name: str, value: str, *, notify_scene: bool = True) -> None:
        if self._updating:
            return
        if _param_value(getattr(self._node_item, "model", None), name) == value:
            return
        _set_param(self._node_item, name, value, notify_scene=notify_scene)
        self._schedule_refresh()

    def _sync_from_params(self) -> None:
        model = getattr(self._node_item, "model", None)
        widgets = (
            self._enabled,
            self._metric,
            self._low_pct,
            self._high_pct,
            self._gamma,
            self._blend,
            self._invert,
        )
        for widget in widgets:
            try:
                widget.blockSignals(True)
            except Exception:
                pass
        try:
            self._updating = True
            self._enabled.setChecked(_param_bool(model, "enabled", True))
            metric = _clean_metric(_param_value(model, "metric") or "thickness")
            idx = self._metric.findData(metric)
            self._metric.setCurrentIndex(max(0, idx))
            self._low_pct.setValue(_param_float(model, "low_percentile", 2.0, minimum=0.0, maximum=99.0))
            self._high_pct.setValue(_param_float(model, "high_percentile", 98.0, minimum=1.0, maximum=100.0))
            self._gamma.setValue(_param_float(model, "gamma", 1.0, minimum=0.05, maximum=8.0))
            self._blend.setValue(_param_float(model, "blend", 1.0, minimum=0.0, maximum=1.0))
            self._invert.setChecked(_param_bool(model, "invert", False))
            self._style_color_button(self._low_color, _param_value(model, "low_color") or _DEFAULT_LOW)
            self._style_color_button(self._mid_color, _param_value(model, "mid_color") or _DEFAULT_MID)
            self._style_color_button(self._high_color, _param_value(model, "high_color") or _DEFAULT_HIGH)
        finally:
            self._updating = False
            for widget in widgets:
                try:
                    widget.blockSignals(False)
                except Exception:
                    pass

    def _refresh_status(self):
        self._pending = False
        self._sync_from_params()
        outcome = build_splat_colorize_scene_asset(self._node_item)
        render_proxy = (outcome.asset or {}).get("render_proxy") if isinstance(outcome.asset, dict) else {}
        self._view_btn.setEnabled(bool(isinstance(render_proxy, dict) and render_proxy.get("splat_ply")))
        if outcome.status == "ok":
            self._status.setStyleSheet("color:#22c55e;font-size:11px;")
        elif outcome.status == "warning":
            self._status.setStyleSheet("color:#f59e0b;font-size:11px;")
        else:
            self._status.setStyleSheet("color:#ef4444;font-size:11px;")
        self._status.setText(outcome.detail)

    def _choose_color(self, button):
        param_name = str(button.property("param_name") or "")
        if not param_name:
            return
        model = getattr(self._node_item, "model", None)
        current = QtGui.QColor(_param_value(model, param_name) or "#ffffff")
        parent = _resolve_window(self._node_item) or self
        color = QtWidgets.QColorDialog.getColor(current, parent, "Choose Color")
        if not color.isValid():
            return
        value = color.name(QtGui.QColor.HexRgb)
        _set_param(self._node_item, param_name, value, notify_scene=True)
        self._sync_from_params()
        self._schedule_refresh()

    def _on_enabled_changed(self, _state: int):
        self._set_param("enabled", "1" if self._enabled.isChecked() else "0")

    def _on_metric_changed(self, _index: int):
        self._set_param("metric", str(self._metric.currentData() or "thickness"))

    def _on_invert_changed(self, _state: int):
        self._set_param("invert", "1" if self._invert.isChecked() else "0")

    def _on_view_clicked(self):
        outcome = build_splat_colorize_scene_asset(self._node_item)
        if not outcome.asset:
            QtWidgets.QMessageBox.warning(
                _resolve_window(self._node_item) or self,
                "Colorize",
                outcome.detail or "Connect a valid skinned splat input first.",
            )
            self._schedule_refresh()
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if not callable(handler):
            QtWidgets.QMessageBox.warning(
                _resolve_window(self._node_item) or self,
                "Colorize",
                "No viewport is available for preview.",
            )
            return
        try:
            handler([dict(outcome.asset)], frame=True)
        except TypeError:
            handler([dict(outcome.asset)])


def render_node_body(node_item, y_cursor: int) -> int:
    inset_x = SPLAT_COLORIZE_BODY_INSET_X
    inset_top = SPLAT_COLORIZE_BODY_INSET_TOP
    inset_bottom = SPLAT_COLORIZE_BODY_INSET_BOTTOM
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
    body = SplatColorizeWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(inset_x, y_cursor + inset_top)
    height = max(36, int(body.sizeHint().height()))
    proxy.resize(max(40, int(node_item.width) - (inset_x * 2)), height)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    return y_cursor + inset_top + height + inset_bottom


SPLAT_COLORIZE_SPEC = Spec(
    stripe_color="#22d3ee",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "KIND_ALIASES",
    "SPLAT_COLORIZE_NODE_BODY_H",
    "SPLAT_COLORIZE_NODE_W",
    "SPLAT_COLORIZE_SPEC",
    "SplatColorizeBuildOutcome",
    "build_ports",
    "build_splat_colorize_scene_asset",
    "render_node_body",
    "splat_colorize_config_from_model",
]
