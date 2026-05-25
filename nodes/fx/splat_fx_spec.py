from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from echograph.audio.music_analysis import (
    MusicAnalysisError,
    MusicAnalysisSettings,
    analyze_audio_file,
)
from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

from .music_effects_spec import (
    _audio_path_from_node,
    _connected_input_item,
    _ensure_hidden_params,
    _ensure_param,
    _enter_preview_timeline_context,
    _load_preview_audio_into_timeline,
    _node_kind,
    _node_name,
    _param_bool,
    _param_float,
    _param_value,
    _resolve_window,
    _set_param,
)


KIND_ALIASES = {
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

SPLAT_FX_NODE_W = 288
SPLAT_FX_BODY_INSET_X = 8
SPLAT_FX_BODY_INSET_TOP = 2
SPLAT_FX_BODY_INSET_BOTTOM = 4
SPLAT_FX_WIDGET_HINT_H = 304
SPLAT_FX_NODE_BODY_H = (
    SPLAT_FX_BODY_INSET_TOP
    + SPLAT_FX_WIDGET_HINT_H
    + SPLAT_FX_BODY_INSET_BOTTOM
)


@dataclass(frozen=True)
class SplatFxBuildOutcome:
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
        with (_repo_logs_dir() / "fx_splat_fx_debug.log").open("a", encoding="utf-8") as handle:
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
    for edge in edges:
        name = str(
            getattr(edge, "dst_port_name", None)
            or getattr(edge, "dst_label", None)
            or getattr(edge, "dst_name", None)
            or ""
        ).strip().lower()
        if name != "audio":
            return getattr(edge, "src", None)
    return None


def _source_asset_from_item(source_item) -> tuple[Optional[Dict[str, Any]], str]:
    if source_item is None:
        return None, "Connect this node after a Skinned Splat Proxy or FX Splat Physics."
    kind = _node_kind(source_item)
    if kind in KIND_ALIASES:
        return build_splat_fx_scene_asset(source_item).asset, ""
    if kind in _FX_SPLAT_PHYSICS_KIND_ALIASES:
        try:
            from nodes.fx import splat_physics_spec as splat_physics_spec  # type: ignore

            outcome = splat_physics_spec.build_splat_physics_scene_asset(source_item)
            return getattr(outcome, "asset", None), str(getattr(outcome, "detail", "") or "")
        except Exception as exc:
            return None, f"FX Splat Physics asset build failed: {exc}"
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


def splat_fx_config_from_model(
    model,
    *,
    audio_path: str,
    analysis_cache_path: str = "",
    analysis_duration_s: float = 0.0,
    analysis_frame_count: int = 0,
) -> Dict[str, Any]:
    return {
        "schema": "qubit.splat_fx.v1",
        "enabled": _param_bool(model, "enabled", True),
        "audio_path": str(audio_path or ""),
        "analysis_cache_path": str(analysis_cache_path or ""),
        "analysis": {
            "mode": "beat_wave_glow",
            "gain": _param_float(model, "analysis_gain", 1.25, minimum=0.0, maximum=8.0),
            "smoothing_ms": _param_float(model, "analysis_smoothing_ms", 70.0, minimum=0.0, maximum=1000.0),
            "threshold": _param_float(model, "analysis_threshold", 0.04, minimum=0.0, maximum=0.95),
            "audio_start_offset_ms": _param_float(
                model,
                "audio_start_offset_ms",
                0.0,
                minimum=-600_000.0,
                maximum=600_000.0,
            ),
            "duration_s": max(0.0, float(analysis_duration_s or 0.0)),
            "frame_count": max(0, int(analysis_frame_count or 0)),
        },
        "glow": {
            "intensity": _param_float(model, "glow_intensity", 1.25, minimum=0.0, maximum=8.0),
            "radius_boost": _param_float(model, "glow_radius_boost", 0.35, minimum=0.0, maximum=4.0),
            "saturation": _param_float(model, "glow_saturation", 0.45, minimum=0.0, maximum=4.0),
            "wave_strength": _param_float(model, "wave_strength", 0.65, minimum=0.0, maximum=1.0),
            "wave_width": _param_float(model, "wave_width", 0.32, minimum=0.01, maximum=2.0),
            "wave_speed": _param_float(model, "wave_speed", 1.0, minimum=0.0, maximum=8.0),
            "wave_axis": str(_param_value(model, "wave_axis") or "y").strip().lower() or "y",
        },
    }


def build_splat_fx_scene_asset(node_item) -> SplatFxBuildOutcome:
    model = getattr(node_item, "model", None)
    source_item = _connected_splat_item(node_item)
    source_asset, source_detail = _source_asset_from_item(source_item)
    if not isinstance(source_asset, dict):
        _debug_log(model, "resolve_source_failed", source=_node_name(source_item), error=source_detail)
        return SplatFxBuildOutcome(None, "error", source_detail or "No supported splat input.")

    audio_path = _audio_path_from_node(node_item)
    analysis_cache_path = ""
    analysis_duration_s = 0.0
    analysis_frame_count = 0
    status = "ok"
    detail = "Splat FX ready."
    if audio_path:
        try:
            analysis = analyze_audio_file(
                audio_path,
                settings=MusicAnalysisSettings(
                    smoothing_ms=_param_float(
                        model,
                        "analysis_smoothing_ms",
                        70.0,
                        minimum=0.0,
                        maximum=1000.0,
                    )
                ),
            )
            analysis_cache_path = str(analysis.cache_path)
            analysis_duration_s = float(analysis.duration_s)
            analysis_frame_count = int(analysis.frame_count)
            _debug_log(
                model,
                "analysis_ready",
                audio_path=audio_path,
                cache_path=analysis_cache_path,
                frames=int(analysis.frame_count),
                duration_s=float(analysis.duration_s),
                from_cache=bool(analysis.from_cache),
            )
        except MusicAnalysisError as exc:
            status = "warning"
            detail = str(exc)
            _debug_log(model, "analysis_failed", audio_path=audio_path, error=str(exc))
    else:
        status = "warning"
        detail = "Choose an audio file or connect an audio input."

    asset = dict(source_asset)
    render_proxy = asset.get("render_proxy") if isinstance(asset.get("render_proxy"), dict) else {}
    render_proxy = dict(render_proxy)
    render_proxy["splat_fx"] = splat_fx_config_from_model(
        model,
        audio_path=audio_path,
        analysis_cache_path=analysis_cache_path,
        analysis_duration_s=analysis_duration_s,
        analysis_frame_count=analysis_frame_count,
    )
    asset["render_proxy"] = render_proxy
    asset["splat_fx_node"] = str(getattr(model, "name", "") or "")
    asset["kind"] = "fx_splat_fx"
    if _debug_enabled(model):
        asset["debug_log"] = True
    return SplatFxBuildOutcome(asset, status, detail)


def build_ports(node_item) -> None:
    for name, default in (
        ("audio_path", ""),
        ("enabled", "1"),
        ("analysis_gain", "1.25"),
        ("analysis_smoothing_ms", "70.0"),
        ("analysis_threshold", "0.04"),
        ("audio_start_offset_ms", "0.0"),
        ("glow_intensity", "1.25"),
        ("glow_radius_boost", "0.35"),
        ("glow_saturation", "0.45"),
        ("wave_strength", "0.65"),
        ("wave_width", "0.32"),
        ("wave_speed", "1.0"),
        ("wave_axis", "y"),
        ("debug_log", "0"),
    ):
        _ensure_param(node_item, name, default)
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [
            "audio_path",
            "enabled",
            "analysis_gain",
            "analysis_smoothing_ms",
            "analysis_threshold",
            "audio_start_offset_ms",
            "glow_intensity",
            "glow_radius_boost",
            "glow_saturation",
            "wave_strength",
            "wave_width",
            "wave_speed",
            "wave_axis",
            "debug_log",
        ],
    )
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("audio")
        node_item.ensure_input("splats")


class SplatFxWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._pending = False
        self._updating = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("Connect splats and choose audio")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setWordWrap(True)
        layout.addWidget(self._status, 0)

        self._enabled = QtWidgets.QCheckBox("Enabled")
        self._enabled.stateChanged.connect(self._on_enabled_changed)
        layout.addWidget(self._enabled, 0)

        audio_row = QtWidgets.QHBoxLayout()
        audio_row.setContentsMargins(0, 0, 0, 0)
        audio_row.setSpacing(4)
        self._audio_path = QtWidgets.QLineEdit()
        self._audio_path.setPlaceholderText("Audio file (.wav or .mp3)")
        self._audio_path.editingFinished.connect(self._on_audio_changed)
        browse_btn = QtWidgets.QPushButton("Browse")
        browse_btn.clicked.connect(self._on_browse_clicked)
        audio_row.addWidget(self._audio_path, 1)
        audio_row.addWidget(browse_btn, 0)
        layout.addLayout(audio_row, 0)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        def _box(minimum: float, maximum: float, step: float, decimals: int = 3):
            box = QtWidgets.QDoubleSpinBox()
            box.setDecimals(int(decimals))
            box.setRange(float(minimum), float(maximum))
            box.setSingleStep(float(step))
            box.setKeyboardTracking(False)
            box.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            box.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            return box

        self._gain = _box(0.0, 8.0, 0.05)
        self._smooth = _box(0.0, 1000.0, 5.0, decimals=1)
        self._threshold = _box(0.0, 0.95, 0.01)
        self._intensity = _box(0.0, 8.0, 0.05)
        self._radius = _box(0.0, 4.0, 0.05)
        self._saturation = _box(0.0, 4.0, 0.05)
        self._wave_strength = _box(0.0, 1.0, 0.05)
        self._wave_width = _box(0.01, 2.0, 0.01)
        self._wave_speed = _box(0.0, 8.0, 0.05)

        rows = (
            ("Gain", self._gain),
            ("Smooth", self._smooth),
            ("Threshold", self._threshold),
            ("Glow", self._intensity),
            ("Size", self._radius),
            ("Saturate", self._saturation),
            ("Wave", self._wave_strength),
            ("Width", self._wave_width),
            ("Speed", self._wave_speed),
        )
        for idx, (label, widget) in enumerate(rows):
            grid.addWidget(QtWidgets.QLabel(label), idx // 2, (idx % 2) * 2)
            grid.addWidget(widget, idx // 2, ((idx % 2) * 2) + 1)
        layout.addLayout(grid, 0)

        axis_row = QtWidgets.QHBoxLayout()
        axis_row.setContentsMargins(0, 0, 0, 0)
        axis_row.setSpacing(6)
        axis_row.addWidget(QtWidgets.QLabel("Axis"), 0)
        self._axis = QtWidgets.QComboBox()
        for key, label in (("x", "X"), ("y", "Y"), ("z", "Z")):
            self._axis.addItem(label, key)
        self._axis.currentIndexChanged.connect(self._on_axis_changed)
        axis_row.addWidget(self._axis, 1)
        layout.addLayout(axis_row, 0)

        self._view_btn = QtWidgets.QPushButton("View")
        self._view_btn.setMinimumHeight(24)
        self._view_btn.setStyleSheet(
            "QPushButton{background:#2563eb;color:#f8fafc;border-radius:4px;padding:2px 8px;}"
            "QPushButton:hover{background:#1d4ed8;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._view_btn.clicked.connect(self._on_view_clicked)
        layout.addWidget(self._view_btn, 0)

        for widget, key in (
            (self._gain, "analysis_gain"),
            (self._smooth, "analysis_smoothing_ms"),
            (self._threshold, "analysis_threshold"),
            (self._intensity, "glow_intensity"),
            (self._radius, "glow_radius_boost"),
            (self._saturation, "glow_saturation"),
            (self._wave_strength, "wave_strength"),
            (self._wave_width, "wave_width"),
            (self._wave_speed, "wave_speed"),
        ):
            widget.valueChanged.connect(lambda value, name=key: self._set_param(name, f"{float(value):.3f}"))

        self._sync_from_params()
        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._refresh_status)

    def sizeHint(self):
        return QtCore.QSize(236, SPLAT_FX_WIDGET_HINT_H)

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

    def _sync_from_params(self) -> None:
        model = getattr(self._node_item, "model", None)
        widgets = (
            self._enabled,
            self._audio_path,
            self._gain,
            self._smooth,
            self._threshold,
            self._intensity,
            self._radius,
            self._saturation,
            self._wave_strength,
            self._wave_width,
            self._wave_speed,
            self._axis,
        )
        for widget in widgets:
            try:
                widget.blockSignals(True)
            except Exception:
                pass
        try:
            self._updating = True
            self._enabled.setChecked(_param_bool(model, "enabled", True))
            self._audio_path.setText(_param_value(model, "audio_path"))
            self._gain.setValue(_param_float(model, "analysis_gain", 1.25, minimum=0.0, maximum=8.0))
            self._smooth.setValue(_param_float(model, "analysis_smoothing_ms", 70.0, minimum=0.0, maximum=1000.0))
            self._threshold.setValue(_param_float(model, "analysis_threshold", 0.04, minimum=0.0, maximum=0.95))
            self._intensity.setValue(_param_float(model, "glow_intensity", 1.25, minimum=0.0, maximum=8.0))
            self._radius.setValue(_param_float(model, "glow_radius_boost", 0.35, minimum=0.0, maximum=4.0))
            self._saturation.setValue(_param_float(model, "glow_saturation", 0.45, minimum=0.0, maximum=4.0))
            self._wave_strength.setValue(_param_float(model, "wave_strength", 0.65, minimum=0.0, maximum=1.0))
            self._wave_width.setValue(_param_float(model, "wave_width", 0.32, minimum=0.01, maximum=2.0))
            self._wave_speed.setValue(_param_float(model, "wave_speed", 1.0, minimum=0.0, maximum=8.0))
            axis = (_param_value(model, "wave_axis") or "y").strip().lower()
            idx = self._axis.findData(axis if axis in {"x", "y", "z"} else "y")
            self._axis.setCurrentIndex(max(0, idx))
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
        outcome = build_splat_fx_scene_asset(self._node_item)
        self._view_btn.setEnabled(bool(outcome.asset and outcome.status == "ok"))
        if outcome.status == "ok":
            self._status.setStyleSheet("color:#22c55e;font-size:11px;")
        elif outcome.status == "warning":
            self._status.setStyleSheet("color:#f59e0b;font-size:11px;")
        else:
            self._status.setStyleSheet("color:#ef4444;font-size:11px;")
        self._status.setText(outcome.detail)

    def _on_enabled_changed(self, _state: int):
        self._set_param("enabled", "1" if self._enabled.isChecked() else "0")

    def _on_audio_changed(self):
        self._set_param("audio_path", self._audio_path.text().strip())
        self._schedule_refresh()

    def _on_axis_changed(self, _index: int):
        self._set_param("wave_axis", str(self._axis.currentData() or "y"))

    def _on_browse_clicked(self):
        current = self._audio_path.text().strip()
        start = str(Path(current).parent) if current else str(Path.home())
        parent = _resolve_window(self._node_item) or self
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            "Choose Audio",
            start,
            "Audio files (*.wav *.mp3)",
        )
        if not path:
            return
        self._audio_path.setText(path)
        self._set_param("audio_path", path)
        self._schedule_refresh()

    def _on_view_clicked(self):
        outcome = build_splat_fx_scene_asset(self._node_item)
        if not outcome.asset or outcome.status != "ok":
            QtWidgets.QMessageBox.warning(
                _resolve_window(self._node_item) or self,
                "Splat FX",
                outcome.detail or "Connect splats and choose a valid audio file first.",
            )
            self._schedule_refresh()
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if not callable(handler):
            QtWidgets.QMessageBox.warning(
                _resolve_window(self._node_item) or self,
                "Splat FX",
                "No viewport is available for preview.",
            )
            return
        node_name = str(getattr(getattr(self._node_item, "model", None), "name", "") or "")
        preview_context = _enter_preview_timeline_context(
            win,
            "fx_splat_fx_preview",
            node_name,
        )
        asset = dict(outcome.asset)
        asset["preview_context"] = dict(preview_context)
        if handler([asset], frame=True):
            cfg = (outcome.asset.get("render_proxy") or {}).get("splat_fx")
            if isinstance(cfg, dict):
                _load_preview_audio_into_timeline(win, str(cfg.get("audio_path") or ""))


def render_node_body(node_item, y_cursor: int) -> int:
    inset_x = SPLAT_FX_BODY_INSET_X
    inset_top = SPLAT_FX_BODY_INSET_TOP
    inset_bottom = SPLAT_FX_BODY_INSET_BOTTOM
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
    body = SplatFxWidget(node_item)
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


SPLAT_FX_SPEC = Spec(
    stripe_color="#f472b6",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "KIND_ALIASES",
    "SPLAT_FX_NODE_BODY_H",
    "SPLAT_FX_NODE_W",
    "SPLAT_FX_SPEC",
    "SplatFxBuildOutcome",
    "build_ports",
    "build_splat_fx_scene_asset",
    "render_node_body",
    "splat_fx_config_from_model",
]
