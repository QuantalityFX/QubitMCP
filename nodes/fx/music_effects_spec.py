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


KIND_ALIASES = {
    "fx_music_effects",
    "fx music effects",
    "music_effects",
    "music effects",
    "musiceffects",
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
_SPLAT_COLORIZE_KIND_ALIASES = {
    "colorize",
    "splat_colorize",
    "splat colorize",
    "fx_splat_colorize",
    "fx splat colorize",
    "gaussian_colorize",
    "gaussian colorize",
}
_GUIDE_TUBE_SPLAT_PROXY_TYPES = {"guide_tube_splat", "groom_guide_tube_splat"}
_ANIM_RETARGET_KIND_ALIASES = {"anim_retarget", "anim retarget", "animretarget", "retarget"}
_FBX_KIND_ALIASES = {"fbx_import", "fbx import", "fbximport"}
_COPY_TO_POINTS_KIND_ALIASES = {
    "copy_to_points",
    "copy to points",
    "copy_to_point",
    "copy to point",
    "copytopoints",
}

MUSIC_EFFECTS_NODE_W = 288
MUSIC_EFFECTS_BODY_INSET_X = 8
MUSIC_EFFECTS_BODY_INSET_TOP = 2
MUSIC_EFFECTS_BODY_INSET_BOTTOM = 4
MUSIC_EFFECTS_WIDGET_HINT_H = 244
MUSIC_EFFECTS_NODE_BODY_H = (
    MUSIC_EFFECTS_BODY_INSET_TOP
    + MUSIC_EFFECTS_WIDGET_HINT_H
    + MUSIC_EFFECTS_BODY_INSET_BOTTOM
)


@dataclass(frozen=True)
class MusicEffectsBuildOutcome:
    asset: Optional[Dict[str, Any]]
    status: str
    detail: str


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            return str(entry.get("value") or "")
    return ""


def _param_bool(model, name: str, default: bool = False) -> bool:
    raw = _param_value(model, name).strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _param_float(model, name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(_param_value(model, name).strip())
    except Exception:
        value = float(default)
    return max(float(minimum), min(float(maximum), float(value)))


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
        params = list(params)
        try:
            setattr(model, "params", params)
        except Exception:
            return
    key = name.strip().lower()
    for entry in params:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _set_param(node_item, name: str, value: str, *, notify_scene: bool = True) -> bool:
    try:
        setter = getattr(node_item, "_set_param_value", None)
        if callable(setter):
            setter(name, str(value), rebuild=False, notify_scene=bool(notify_scene))
            return True
    except Exception:
        pass
    model = getattr(node_item, "model", None)
    if model is None:
        return False
    _ensure_param(node_item, name, str(value))
    key = (name or "").strip().lower()
    for entry in getattr(model, "params", None) or []:
        if isinstance(entry, dict) and (entry.get("name") or "").strip().lower() == key:
            entry["value"] = str(value)
            return True
    return False


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    entry = None
    for param in params:
        if isinstance(param, dict) and (param.get("name") or "").strip().lower() == store_key:
            entry = param
            break
    if entry is None:
        entry = {"name": store_key, "value": ""}
        params.append(entry)
    hidden = {
        part.strip().lower()
        for part in str(entry.get("value") or "").split(",")
        if part.strip()
    }
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    entry["value"] = ",".join(sorted(hidden))
    try:
        model.params = params
    except Exception:
        pass


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
        with (_repo_logs_dir() / "fx_music_effects_debug.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")
    except Exception:
        pass


def _edge_dst_name(edge) -> str:
    return str(
        getattr(edge, "dst_port_name", None)
        or getattr(edge, "dst_label", None)
        or getattr(edge, "dst_name", None)
        or ""
    ).strip()


def _ordered_in_edges(scene, item) -> list:
    if scene is None or item is None:
        return []
    try:
        return list(scene._ordered_in_edges(item))
    except Exception:
        try:
            return list(scene._in_edges(item))
        except Exception:
            return []


def _connected_input_item(node_item, names: set[str]):
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    edges = _ordered_in_edges(scene, node_item)
    for edge in edges:
        if _edge_dst_name(edge).lower() in names:
            return getattr(edge, "src", None)
    return None


def _node_kind(item) -> str:
    return str(getattr(getattr(item, "model", None), "kind", "") or "").strip().lower()


def _node_name(item) -> str:
    return str(getattr(getattr(item, "model", None), "name", "") or "").strip()


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
        win = node_item.window()
        if win is not None:
            return win
    except Exception:
        pass
    try:
        active = QtWidgets.QApplication.activeWindow()
        if active is not None and active.isWindow():
            return active
    except Exception:
        pass
    return None


def _audio_path_from_node(node_item) -> str:
    audio_item = _connected_input_item(node_item, {"audio"})
    audio_model = getattr(audio_item, "model", None)
    for name in ("path", "audio", "source"):
        raw = _param_value(audio_model, name).strip() if audio_model is not None else ""
        if raw:
            return raw
    return _param_value(getattr(node_item, "model", None), "audio_path").strip()


def _load_preview_audio_into_timeline(win, audio_path: str) -> None:
    path = str(audio_path or "").strip()
    if not path or win is None:
        return
    controller = getattr(win, "_timeline_controller", None)
    toggle_audio = getattr(controller, "toggle_audio_panel_from_menu", None)
    if callable(toggle_audio):
        try:
            toggle_audio(True)
        except Exception:
            pass
    gl_view = getattr(win, "gl_view", None)
    if gl_view is None:
        return
    set_timeline_visible = getattr(gl_view, "set_timeline_visible", None)
    set_audio_visible = getattr(gl_view, "set_timeline_audio_visible", None)
    set_audio_path = getattr(gl_view, "_timeline_audio_set_path", None)
    try:
        if callable(set_timeline_visible):
            set_timeline_visible(True)
        if callable(set_audio_visible):
            set_audio_visible(True)
        if callable(set_audio_path):
            set_audio_path(path, save=True)
    except Exception:
        pass


def _enter_preview_timeline_context(win, prefix: str, node_name: str = "") -> Dict[str, str]:
    raw = "_".join(part for part in (str(prefix or "").strip(), str(node_name or "").strip()) if part)
    safe = "".join(ch if (ch.isalnum() or ch in ("_", "-")) else "_" for ch in raw).strip("_")
    if not safe:
        safe = "preview"
    context = {
        "kind": str(prefix or "preview"),
        "node": str(node_name or ""),
        "scene_name": safe,
    }
    if win is None:
        return context
    try:
        setattr(win, "_active_scene_preview_context", context)
        setattr(win, "_active_scene_node", None)
        timer = getattr(win, "_active_scene_refresh_timer", None)
        if timer is not None:
            timer.stop()
    except Exception:
        pass
    gl_view = getattr(win, "gl_view", None)
    set_context = getattr(gl_view, "set_timeline_scene_context", None) if gl_view is not None else None
    if not callable(set_context):
        return
    try:
        project_path = str(getattr(win, "_current_path", "") or "").strip() or None
    except Exception:
        project_path = None
    try:
        set_context(scene_name=safe, project_path=project_path, owner_name=None)
    except Exception:
        pass
    return context


def _source_asset_from_item(source_item) -> tuple[Optional[Dict[str, Any]], str]:
    if source_item is None:
        return None, "Connect a model or splat source."
    kind = _node_kind(source_item)
    if kind in KIND_ALIASES:
        return build_music_effects_scene_asset(source_item).asset, ""
    if kind in _COPY_TO_POINTS_KIND_ALIASES:
        try:
            from nodes.copy_to_points import spec as copy_to_points_spec  # type: ignore

            outcome = copy_to_points_spec.build_copy_to_points_scene_asset(source_item)
            return getattr(outcome, "asset", None), str(getattr(outcome, "detail", "") or "")
        except Exception as exc:
            return None, f"Copy To Points asset build failed: {exc}"
    if kind in _FX_SPLAT_PHYSICS_KIND_ALIASES:
        try:
            from nodes.fx import splat_physics_spec as splat_fx_spec  # type: ignore

            outcome = splat_fx_spec.build_splat_physics_scene_asset(source_item)
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
    if kind in _SPLAT_COLORIZE_KIND_ALIASES:
        try:
            from nodes.fx import splat_colorize_spec as splat_colorize_spec  # type: ignore

            outcome = splat_colorize_spec.build_splat_colorize_scene_asset(source_item)
            return getattr(outcome, "asset", None), str(getattr(outcome, "detail", "") or "")
        except Exception as exc:
            return None, f"Splat Colorize asset build failed: {exc}"
    if kind in _SKINNED_SPLAT_PROXY_KIND_ALIASES:
        try:
            from nodes.skinned_splat_proxy import spec as proxy_spec  # type: ignore

            outcome = proxy_spec.build_skinned_splat_proxy_scene_asset(source_item, generate=False)
            return getattr(outcome, "asset", None), str(getattr(outcome, "detail", "") or "")
        except Exception as exc:
            return None, f"Skinned Splat Proxy asset build failed: {exc}"
    if kind in _ANIM_RETARGET_KIND_ALIASES:
        try:
            from nodes.anim_retarget import spec as retarget_spec  # type: ignore

            asset = retarget_spec.build_anim_retarget_scene_asset(source_item)
            return asset if isinstance(asset, dict) else None, ""
        except Exception as exc:
            return None, f"Anim Retarget asset build failed: {exc}"

    model = getattr(source_item, "model", None)
    path = _param_value(model, "path").strip()
    rig_context = None
    if kind in _FBX_KIND_ALIASES:
        try:
            from nodes.scene import spec as scene_spec  # type: ignore

            if not path:
                path = scene_spec._fbx_import_resolved_rest_path(model)
            rig_context = scene_spec._fbx_import_rig_context(model)
        except Exception:
            rig_context = None
    if not path:
        path = _param_value(model, "mesh").strip() or _param_value(model, "source").strip()
    if not path:
        return None, f"Input node does not provide a mesh path: {kind or '<none>'}."
    asset: Dict[str, Any] = {
        "path": path,
        "ext": Path(path).suffix.lower(),
        "node": _node_name(source_item) or Path(path).stem,
        "kind": kind,
        "visible": True,
    }
    texture = _param_value(model, "texture").strip()
    if texture:
        asset["texture"] = texture
    if isinstance(rig_context, dict):
        asset["fbx_rig_context"] = rig_context
    return asset, ""


def _connected_geometry_item(node_item):
    item = _connected_input_item(node_item, {"mesh", "geometry", "source", "splats", "splat"})
    if item is not None:
        return item
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    for edge in _ordered_in_edges(scene, node_item):
        if _edge_dst_name(edge).lower() != "audio":
            return getattr(edge, "src", None)
    return None


def music_effects_config_from_model(
    model,
    *,
    audio_path: str,
    analysis_cache_path: str = "",
    analysis_duration_s: float = 0.0,
    analysis_frame_count: int = 0,
) -> Dict[str, Any]:
    return {
        "schema": "qubit.music_effects.v1",
        "enabled": _param_bool(model, "enabled", True),
        "audio_path": str(audio_path or ""),
        "analysis_cache_path": str(analysis_cache_path or ""),
        "analysis": {
            "mode": "beat_pulse",
            "gain": _param_float(model, "analysis_gain", 1.0, minimum=0.0, maximum=8.0),
            "smoothing_ms": _param_float(model, "analysis_smoothing_ms", 80.0, minimum=0.0, maximum=1000.0),
            "threshold": _param_float(model, "analysis_threshold", 0.05, minimum=0.0, maximum=0.95),
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
        "mesh": {
            "displacement": _param_float(model, "mesh_displacement", 0.15, minimum=0.0, maximum=1000.0),
            "max_displacement": _param_float(
                model,
                "mesh_max_displacement",
                1.0,
                minimum=0.0,
                maximum=1000.0,
            ),
            "outward_only": _param_bool(model, "mesh_outward_only", True),
        },
    }


def _is_guide_tube_splat_proxy(render_proxy: Dict[str, Any]) -> bool:
    return str((render_proxy or {}).get("type") or "").strip().lower() in _GUIDE_TUBE_SPLAT_PROXY_TYPES


def build_music_effects_scene_asset(node_item) -> MusicEffectsBuildOutcome:
    model = getattr(node_item, "model", None)
    source_item = _connected_geometry_item(node_item)
    source_asset, source_detail = _source_asset_from_item(source_item)
    if not isinstance(source_asset, dict):
        _debug_log(model, "resolve_source_failed", source=_node_name(source_item), error=source_detail)
        return MusicEffectsBuildOutcome(None, "error", source_detail or "No supported geometry input.")

    audio_path = _audio_path_from_node(node_item)
    analysis_cache_path = ""
    analysis_duration_s = 0.0
    analysis_frame_count = 0
    status = "ok"
    detail = "Music effects ready."
    if audio_path:
        try:
            analysis = analyze_audio_file(
                audio_path,
                settings=MusicAnalysisSettings(
                    smoothing_ms=_param_float(
                        model,
                        "analysis_smoothing_ms",
                        80.0,
                        minimum=0.0,
                        maximum=1000.0,
                    )
                ),
            )
            analysis_cache_path = str(analysis.cache_path)
            analysis_duration_s = float(analysis.duration_s)
            analysis_frame_count = int(analysis.frame_count)
            detail = "Music effects ready."
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
    effect_node_name = str(getattr(model, "name", "") or "").strip()
    source_node_name = str(asset.get("node") or "").strip()
    if source_node_name:
        asset["music_effects_source_node"] = source_node_name
    render_proxy = asset.get("render_proxy") if isinstance(asset.get("render_proxy"), dict) else {}
    proxy_type = str(render_proxy.get("type") or "").strip().lower()
    guide_tube_splat_proxy = _is_guide_tube_splat_proxy(render_proxy)
    if effect_node_name and proxy_type not in {"skinned_splat", "skinned_gaussian_splat"} and not guide_tube_splat_proxy:
        asset["node"] = effect_node_name
    asset["music_effects"] = music_effects_config_from_model(
        model,
        audio_path=audio_path,
        analysis_cache_path=analysis_cache_path,
        analysis_duration_s=analysis_duration_s,
        analysis_frame_count=analysis_frame_count,
    )
    asset["music_effects_node"] = effect_node_name
    if guide_tube_splat_proxy:
        asset["kind"] = "groom_guides"
        asset["source_kind"] = str(asset.get("source_kind") or "groom_guide_tube")
    else:
        asset["kind"] = "fx_music_effects"
    if _debug_enabled(model):
        asset["debug_log"] = True
    return MusicEffectsBuildOutcome(asset, status, detail)


def build_ports(node_item) -> None:
    for name, default in (
        ("audio_path", ""),
        ("enabled", "1"),
        ("analysis_gain", "1.0"),
        ("analysis_smoothing_ms", "80.0"),
        ("analysis_threshold", "0.05"),
        ("audio_start_offset_ms", "0.0"),
        ("mesh_displacement", "0.15"),
        ("mesh_max_displacement", "1.0"),
        ("mesh_outward_only", "1"),
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
            "mesh_displacement",
            "mesh_max_displacement",
            "mesh_outward_only",
            "debug_log",
        ],
    )
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("audio")
        node_item.ensure_input("mesh")


class MusicEffectsWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._pending = False
        self._updating = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("Connect a mesh and choose audio")
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
        self._offset = _box(-600000.0, 600000.0, 10.0, decimals=1)
        self._displace = _box(0.0, 1000.0, 0.01)
        self._max_displace = _box(0.0, 1000.0, 0.01)

        rows = (
            ("Gain", self._gain),
            ("Smooth", self._smooth),
            ("Threshold", self._threshold),
            ("Offset ms", self._offset),
            ("Displace", self._displace),
            ("Max", self._max_displace),
        )
        for idx, (label, widget) in enumerate(rows):
            grid.addWidget(QtWidgets.QLabel(label), idx // 2, (idx % 2) * 2)
            grid.addWidget(widget, idx // 2, ((idx % 2) * 2) + 1)
        layout.addLayout(grid, 0)

        self._outward_only = QtWidgets.QCheckBox("Outward only")
        self._outward_only.stateChanged.connect(self._on_outward_changed)
        layout.addWidget(self._outward_only, 0)

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
            (self._offset, "audio_start_offset_ms"),
            (self._displace, "mesh_displacement"),
            (self._max_displace, "mesh_max_displacement"),
        ):
            widget.valueChanged.connect(lambda value, name=key: self._set_param(name, f"{float(value):.3f}"))

        self._sync_from_params()
        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._refresh_status)

    def sizeHint(self):
        return QtCore.QSize(236, MUSIC_EFFECTS_WIDGET_HINT_H)

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
            self._offset,
            self._displace,
            self._max_displace,
            self._outward_only,
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
            self._gain.setValue(_param_float(model, "analysis_gain", 1.0, minimum=0.0, maximum=8.0))
            self._smooth.setValue(
                _param_float(model, "analysis_smoothing_ms", 80.0, minimum=0.0, maximum=1000.0)
            )
            self._threshold.setValue(
                _param_float(model, "analysis_threshold", 0.05, minimum=0.0, maximum=0.95)
            )
            self._offset.setValue(
                _param_float(model, "audio_start_offset_ms", 0.0, minimum=-600000.0, maximum=600000.0)
            )
            self._displace.setValue(
                _param_float(model, "mesh_displacement", 0.15, minimum=0.0, maximum=1000.0)
            )
            self._max_displace.setValue(
                _param_float(model, "mesh_max_displacement", 1.0, minimum=0.0, maximum=1000.0)
            )
            self._outward_only.setChecked(_param_bool(model, "mesh_outward_only", True))
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
        outcome = build_music_effects_scene_asset(self._node_item)
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

    def _on_outward_changed(self, _state: int):
        self._set_param("mesh_outward_only", "1" if self._outward_only.isChecked() else "0")

    def _on_audio_changed(self):
        self._set_param("audio_path", self._audio_path.text().strip())
        self._schedule_refresh()

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
        outcome = build_music_effects_scene_asset(self._node_item)
        if not outcome.asset or outcome.status != "ok":
            QtWidgets.QMessageBox.warning(
                _resolve_window(self._node_item) or self,
                "Music Effects",
                outcome.detail or "Connect a mesh and choose a valid audio file first.",
            )
            self._schedule_refresh()
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if not callable(handler):
            QtWidgets.QMessageBox.warning(
                _resolve_window(self._node_item) or self,
                "Music Effects",
                "No viewport is available for preview.",
            )
            return
        node_name = str(getattr(getattr(self._node_item, "model", None), "name", "") or "")
        preview_context = _enter_preview_timeline_context(
            win,
            "fx_music_effects_preview",
            node_name,
        )
        asset = dict(outcome.asset)
        asset["preview_context"] = dict(preview_context)
        if handler([asset], frame=True):
            effect_cfg = outcome.asset.get("music_effects")
            if isinstance(effect_cfg, dict):
                _load_preview_audio_into_timeline(win, str(effect_cfg.get("audio_path") or ""))


def render_node_body(node_item, y_cursor: int) -> int:
    inset_x = MUSIC_EFFECTS_BODY_INSET_X
    inset_top = MUSIC_EFFECTS_BODY_INSET_TOP
    inset_bottom = MUSIC_EFFECTS_BODY_INSET_BOTTOM
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
    body = MusicEffectsWidget(node_item)
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


MUSIC_EFFECTS_SPEC = Spec(
    stripe_color="#84cc16",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "KIND_ALIASES",
    "MUSIC_EFFECTS_NODE_BODY_H",
    "MUSIC_EFFECTS_NODE_W",
    "MUSIC_EFFECTS_SPEC",
    "MusicEffectsBuildOutcome",
    "build_music_effects_scene_asset",
    "build_ports",
    "music_effects_config_from_model",
    "render_node_body",
]
