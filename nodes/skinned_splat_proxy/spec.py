from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant
from echograph.rigging.skinned_splat_proxy import (
    SkinnedSplatProxyError,
    SkinnedSplatProxySettings,
    build_skinned_splat_proxy_from_rig_context,
)


KIND_ALIASES = {
    "skinned_splat_proxy",
    "skinned splat proxy",
    "skinnedsplatproxy",
    "fbx_to_skinned_splat_proxy",
    "fbx skinned splat proxy",
}
_FBX_KIND_ALIASES = {"fbx_import", "fbx import", "fbximport"}
_ANIM_RETARGET_KIND_ALIASES = {"anim_retarget", "anim retarget", "animretarget", "retarget"}
_DEFAULT_SAMPLE_COUNT = 50_000
_DEFAULT_MAX_INFLUENCES = 4


@dataclass(frozen=True)
class ProxyBuildOutcome:
    asset: Optional[Dict[str, Any]]
    status: str
    detail: str
    generated: bool = False


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


def _param_int(model, name: str, default: int, *, min_value: int, max_value: int) -> int:
    try:
        value = int(round(float(_param_value(model, name).strip())))
    except Exception:
        value = int(default)
    return max(int(min_value), min(int(max_value), int(value)))


def _param_float(model, name: str, default: float) -> float:
    try:
        value = float(_param_value(model, name).strip())
    except Exception:
        value = float(default)
    return float(value)


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
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": str(event or ""),
    }
    record.update(fields or {})
    try:
        with (_repo_logs_dir() / "skinned_splat_proxy_debug.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")
    except Exception:
        pass


def _workflow_dir_for_node(node_item) -> Path | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    workflow_path = getattr(scene, "_filename", None) if scene is not None else None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                workflow_path = getattr(views[0].window(), "_current_path", None) or workflow_path
        except Exception:
            pass
    if not workflow_path:
        return None
    try:
        return Path(workflow_path).parent
    except Exception:
        return None


def _proxy_output_dir(node_item) -> Path:
    model = getattr(node_item, "model", None)
    raw = _param_value(model, "output_dir").strip()
    if raw:
        out = Path(raw)
    else:
        base = _workflow_dir_for_node(node_item)
        if base is None:
            base = Path(tempfile.gettempdir()) / "EchoGraph"
        out = base / "skinned_splat_proxy"
    out.mkdir(parents=True, exist_ok=True)
    return out


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


def _connected_source_item(node_item):
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    edges = _ordered_in_edges(scene, node_item)
    chosen = None
    for edge in edges:
        if _edge_dst_name(edge).lower() in {"mesh", "source", "path", "rig"}:
            chosen = edge
            break
    if chosen is None and edges:
        chosen = edges[0]
    return getattr(chosen, "src", None) if chosen is not None else None


def _node_kind(item) -> str:
    return str(getattr(getattr(item, "model", None), "kind", "") or "").strip().lower()


def _node_name(item) -> str:
    return str(getattr(getattr(item, "model", None), "name", "") or "").strip()


def _source_asset_from_item(source_item) -> tuple[Optional[Dict[str, Any]], str]:
    if source_item is None:
        return None, "Connect this node after Anim Retarget or FBX Import."
    kind = _node_kind(source_item)
    model = getattr(source_item, "model", None)

    if kind in _ANIM_RETARGET_KIND_ALIASES:
        try:
            from nodes.anim_retarget import spec as anim_spec  # type: ignore

            build_asset = getattr(anim_spec, "build_anim_retarget_scene_asset", None)
            asset = build_asset(source_item) if callable(build_asset) else None
        except Exception as exc:
            return None, f"Anim Retarget asset build failed: {exc}"
        if not isinstance(asset, dict):
            return None, "Anim Retarget did not produce a valid target asset."
        return dict(asset), ""

    if kind in _FBX_KIND_ALIASES:
        try:
            from nodes.fbx_import import spec as fbx_spec  # type: ignore

            result = fbx_spec.resolve_fbx_import_sources(
                source_item,
                persist=True,
                validate_bind_data=True,
                validate_animation_data=False,
            )
            if getattr(result, "status", "") == "error":
                lines = getattr(result, "message_lines", lambda: [])()
                return None, "\n".join(lines) or "FBX Import validation failed."
            build_preview = getattr(fbx_spec, "_build_preview_asset", None)
            asset = build_preview(model, result) if callable(build_preview) else None
        except Exception as exc:
            return None, f"FBX Import asset build failed: {exc}"
        if not isinstance(asset, dict):
            return None, "FBX Import did not produce a valid asset."
        return dict(asset), ""

    return None, f"Unsupported input node kind: {kind or '<none>'}."


def _settings_from_model(model) -> SkinnedSplatProxySettings:
    sample_count = _param_int(
        model,
        "sample_count",
        _DEFAULT_SAMPLE_COUNT,
        min_value=1,
        max_value=2_000_000,
    )
    max_influences = _param_int(
        model,
        "max_influences",
        _DEFAULT_MAX_INFLUENCES,
        min_value=1,
        max_value=16,
    )
    seed = _param_int(model, "seed", 1234, min_value=0, max_value=2_147_483_647)
    return SkinnedSplatProxySettings(
        sample_count=int(sample_count),
        max_influences=int(max_influences),
        seed=int(seed),
        color_mode=(_param_value(model, "color_mode").strip() or "skin_weights"),
        opacity=1.0,
        radius_scale=max(0.05, min(8.0, _param_float(model, "radius_scale", 1.0))),
        adaptive_radius=_param_bool(model, "adaptive_radius", True),
        min_radius=1.0e-4,
        normal_axis_scale=0.35,
    )


def _existing_proxy_paths(model) -> tuple[str, str, str]:
    manifest = _param_value(model, "proxy_manifest").strip()
    ply = _param_value(model, "proxy_ply").strip()
    skin = _param_value(model, "proxy_skin").strip()
    return manifest, ply, skin


def _proxy_paths_exist(manifest: str, ply: str, skin: str) -> bool:
    try:
        return bool(manifest and ply and skin and Path(manifest).exists() and Path(ply).exists() and Path(skin).exists())
    except Exception:
        return False


def _asset_name(node_item, source_asset: Dict[str, Any], settings: SkinnedSplatProxySettings) -> str:
    node_name = str(getattr(getattr(node_item, "model", None), "name", "") or "").strip()
    source_owner = str(source_asset.get("node") or "").strip()
    base = node_name or source_owner or "SkinnedSplatProxy"
    return f"{base}_{int(settings.sample_count):06d}"


def build_skinned_splat_proxy_scene_asset(
    node_item,
    *,
    generate: bool = False,
) -> ProxyBuildOutcome:
    model = getattr(node_item, "model", None)
    source_item = _connected_source_item(node_item)
    source_asset, source_error = _source_asset_from_item(source_item)
    if not isinstance(source_asset, dict):
        _debug_log(model, "resolve_source_failed", error=source_error)
        return ProxyBuildOutcome(None, "error", source_error or "No supported input asset.")

    rig_context = source_asset.get("fbx_rig_context")
    if not isinstance(rig_context, dict):
        detail = "Input asset does not contain an FBX rig context."
        _debug_log(model, "missing_rig_context", source=_node_name(source_item))
        return ProxyBuildOutcome(None, "error", detail)

    settings = _settings_from_model(model)
    manifest, ply, skin = _existing_proxy_paths(model)
    auto_generate = _param_bool(model, "auto_generate", False)
    should_generate = bool(generate) or (bool(auto_generate) and not _proxy_paths_exist(manifest, ply, skin))

    generated = False
    if should_generate:
        try:
            result = build_skinned_splat_proxy_from_rig_context(
                rig_context,
                _proxy_output_dir(node_item),
                asset_name=_asset_name(node_item, source_asset, settings),
                source_fbx=str(source_asset.get("path") or ""),
                settings=settings,
            )
        except SkinnedSplatProxyError as exc:
            _debug_log(model, "generate_failed", error=str(exc))
            return ProxyBuildOutcome(None, "error", str(exc))
        except Exception as exc:
            _debug_log(model, "generate_failed", error=repr(exc))
            return ProxyBuildOutcome(None, "error", f"Proxy generation failed: {exc}")

        manifest = str(result.manifest_path)
        ply = str(result.splat_ply_path)
        skin = str(result.skin_npz_path)
        _set_param(node_item, "proxy_manifest", manifest, notify_scene=False)
        _set_param(node_item, "proxy_ply", ply, notify_scene=False)
        _set_param(node_item, "proxy_skin", skin, notify_scene=False)
        _set_param(node_item, "path", str(source_asset.get("path") or ""), notify_scene=False)
        _set_param(node_item, "source", str(source_asset.get("path") or ""), notify_scene=False)
        generated = True
        _debug_log(
            model,
            "generated",
            manifest=manifest,
            ply=ply,
            skin=skin,
            samples=int(result.arrays.splats.shape[0]),
        )

    if not _proxy_paths_exist(manifest, ply, skin):
        asset = dict(source_asset)
        asset["render_proxy"] = {
            "type": "skinned_splat",
            "status": "not_generated",
            "hide_source_mesh": _param_bool(model, "hide_source_mesh", True),
            "sample_count": int(settings.sample_count),
            "max_influences": int(settings.max_influences),
            "color_mode": str(settings.color_mode),
        }
        asset["skinned_splat_proxy_node"] = str(getattr(model, "name", "") or "")
        return ProxyBuildOutcome(
            asset,
            "warning",
            "Proxy files are not generated yet.",
            generated=False,
        )

    asset = dict(source_asset)
    asset["render_proxy"] = {
        "type": "skinned_splat",
        "manifest": manifest,
        "splat_ply": ply,
        "skin_npz": skin,
        "hide_source_mesh": _param_bool(model, "hide_source_mesh", True),
        "sample_count": int(settings.sample_count),
        "max_influences": int(settings.max_influences),
        "color_mode": str(settings.color_mode),
    }
    asset["skinned_splat_proxy_node"] = str(getattr(model, "name", "") or "")
    asset["skinned_splat_proxy_manifest"] = manifest
    return ProxyBuildOutcome(
        asset,
        "ok",
        "Proxy ready." if not generated else "Proxy generated.",
        generated=generated,
    )


def build_ports(node_item) -> None:
    _ensure_param(node_item, "mesh", "")
    _ensure_param(node_item, "source", "")
    _ensure_param(node_item, "path", "")
    _ensure_param(node_item, "output_dir", "")
    _ensure_param(node_item, "proxy_manifest", "")
    _ensure_param(node_item, "proxy_ply", "")
    _ensure_param(node_item, "proxy_skin", "")
    _ensure_param(node_item, "sample_count", str(_DEFAULT_SAMPLE_COUNT))
    _ensure_param(node_item, "max_influences", str(_DEFAULT_MAX_INFLUENCES))
    _ensure_param(node_item, "seed", "1234")
    _ensure_param(node_item, "color_mode", "skin_weights")
    _ensure_param(node_item, "radius_scale", "1.0")
    _ensure_param(node_item, "adaptive_radius", "1")
    _ensure_param(node_item, "hide_source_mesh", "1")
    _ensure_param(node_item, "auto_generate", "0")
    _ensure_param(node_item, "debug_log", "0")
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [
            "mesh",
            "source",
            "path",
            "output_dir",
            "proxy_manifest",
            "proxy_ply",
            "proxy_skin",
            "seed",
            "color_mode",
            "debug_log",
        ],
    )
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")


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
        return node_item.window()
    except Exception:
        return QtWidgets.QApplication.activeWindow()


class SkinnedSplatProxyWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._pending = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("Connect Anim Retarget")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setWordWrap(True)
        layout.addWidget(self._status, 0)

        row_samples = QtWidgets.QHBoxLayout()
        row_samples.setContentsMargins(0, 0, 0, 0)
        row_samples.addWidget(QtWidgets.QLabel("Samples"), 0)
        self._sample_spin = QtWidgets.QSpinBox()
        self._sample_spin.setRange(1, 2_000_000)
        self._sample_spin.setSingleStep(5000)
        self._sample_spin.setKeyboardTracking(False)
        self._sample_spin.valueChanged.connect(self._on_sample_count_changed)
        row_samples.addWidget(self._sample_spin, 1)
        layout.addLayout(row_samples, 0)

        row_infl = QtWidgets.QHBoxLayout()
        row_infl.setContentsMargins(0, 0, 0, 0)
        row_infl.addWidget(QtWidgets.QLabel("Influences"), 0)
        self._influence_spin = QtWidgets.QSpinBox()
        self._influence_spin.setRange(1, 16)
        self._influence_spin.setKeyboardTracking(False)
        self._influence_spin.valueChanged.connect(self._on_max_influences_changed)
        row_infl.addWidget(self._influence_spin, 1)
        layout.addLayout(row_infl, 0)

        row_radius = QtWidgets.QHBoxLayout()
        row_radius.setContentsMargins(0, 0, 0, 0)
        row_radius.addWidget(QtWidgets.QLabel("Size"), 0)
        self._radius_spin = QtWidgets.QDoubleSpinBox()
        self._radius_spin.setRange(0.05, 8.0)
        self._radius_spin.setDecimals(2)
        self._radius_spin.setSingleStep(0.05)
        self._radius_spin.setKeyboardTracking(False)
        self._radius_spin.valueChanged.connect(self._on_radius_scale_changed)
        row_radius.addWidget(self._radius_spin, 1)
        layout.addLayout(row_radius, 0)

        self._adaptive_check = QtWidgets.QCheckBox("Adaptive size")
        self._adaptive_check.stateChanged.connect(self._on_adaptive_changed)
        layout.addWidget(self._adaptive_check, 0)

        self._hide_check = QtWidgets.QCheckBox("Hide source mesh")
        self._hide_check.stateChanged.connect(self._on_hide_changed)
        layout.addWidget(self._hide_check, 0)

        self._auto_check = QtWidgets.QCheckBox("Auto generate if missing")
        self._auto_check.stateChanged.connect(self._on_auto_changed)
        layout.addWidget(self._auto_check, 0)

        self._generate_btn = QtWidgets.QPushButton("Generate")
        self._generate_btn.setMinimumHeight(24)
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        layout.addWidget(self._generate_btn, 0)
        self._view_btn = QtWidgets.QPushButton("View Proxy")
        self._view_btn.setMinimumHeight(24)
        self._view_btn.clicked.connect(self._on_view_clicked)
        layout.addWidget(self._view_btn, 0)

        self._sync_from_params()
        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._refresh_status)

    def sizeHint(self):
        return QtCore.QSize(224, 238)

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

    def _sync_from_params(self) -> None:
        model = getattr(self._node_item, "model", None)
        self._sample_spin.blockSignals(True)
        self._influence_spin.blockSignals(True)
        self._radius_spin.blockSignals(True)
        self._adaptive_check.blockSignals(True)
        self._hide_check.blockSignals(True)
        self._auto_check.blockSignals(True)
        try:
            self._sample_spin.setValue(
                _param_int(model, "sample_count", _DEFAULT_SAMPLE_COUNT, min_value=1, max_value=2_000_000)
            )
            self._influence_spin.setValue(
                _param_int(model, "max_influences", _DEFAULT_MAX_INFLUENCES, min_value=1, max_value=16)
            )
            self._radius_spin.setValue(max(0.05, min(8.0, _param_float(model, "radius_scale", 1.0))))
            self._adaptive_check.setChecked(_param_bool(model, "adaptive_radius", True))
            self._hide_check.setChecked(_param_bool(model, "hide_source_mesh", True))
            self._auto_check.setChecked(_param_bool(model, "auto_generate", False))
        finally:
            self._sample_spin.blockSignals(False)
            self._influence_spin.blockSignals(False)
            self._radius_spin.blockSignals(False)
            self._adaptive_check.blockSignals(False)
            self._hide_check.blockSignals(False)
            self._auto_check.blockSignals(False)

    def _refresh_status(self):
        self._pending = False
        self._sync_from_params()
        outcome = build_skinned_splat_proxy_scene_asset(self._node_item, generate=False)
        self._view_btn.setEnabled(bool(outcome.asset and (outcome.asset.get("render_proxy") or {}).get("splat_ply")))
        if outcome.status == "ok":
            self._status.setStyleSheet("color:#22c55e;font-size:11px;")
        elif outcome.status == "warning":
            self._status.setStyleSheet("color:#f59e0b;font-size:11px;")
        else:
            self._status.setStyleSheet("color:#ef4444;font-size:11px;")
        self._status.setText(outcome.detail)

    def _on_sample_count_changed(self, value: int):
        _set_param(self._node_item, "sample_count", str(int(value)), notify_scene=True)
        self._schedule_refresh()

    def _on_max_influences_changed(self, value: int):
        _set_param(self._node_item, "max_influences", str(int(value)), notify_scene=True)
        self._schedule_refresh()

    def _on_radius_scale_changed(self, value: float):
        _set_param(self._node_item, "radius_scale", f"{float(value):.2f}", notify_scene=True)
        self._schedule_refresh()

    def _on_adaptive_changed(self, _state: int):
        _set_param(self._node_item, "adaptive_radius", "1" if self._adaptive_check.isChecked() else "0", notify_scene=True)
        self._schedule_refresh()

    def _on_hide_changed(self, _state: int):
        _set_param(self._node_item, "hide_source_mesh", "1" if self._hide_check.isChecked() else "0", notify_scene=True)
        self._schedule_refresh()

    def _on_auto_changed(self, _state: int):
        _set_param(self._node_item, "auto_generate", "1" if self._auto_check.isChecked() else "0", notify_scene=True)
        self._schedule_refresh()

    def _set_busy_border(self, active: bool) -> None:
        setter = getattr(self._node_item, "setBusyState", None)
        if callable(setter):
            try:
                setter(bool(active), "generating" if active else "")
            except Exception:
                pass
        try:
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass

    def _on_generate_clicked(self):
        self._generate_btn.setEnabled(False)
        self._set_busy_border(True)
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setText("Generating...")
        try:
            outcome = build_skinned_splat_proxy_scene_asset(self._node_item, generate=True)
            if outcome.status == "ok":
                self._status.setStyleSheet("color:#22c55e;font-size:11px;")
            else:
                self._status.setStyleSheet("color:#ef4444;font-size:11px;")
            self._status.setText(outcome.detail)
        finally:
            self._set_busy_border(False)
            self._generate_btn.setEnabled(True)
            self._schedule_refresh()

    def _on_view_clicked(self):
        model = getattr(self._node_item, "model", None)
        ply = _param_value(model, "proxy_ply").strip()
        if not ply or not Path(ply).exists():
            self._refresh_status()
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if not callable(handler):
            return
        node_name = str(getattr(model, "name", "") or "skinned_splat_proxy").strip()
        asset = {
            "path": ply,
            "texture": "",
            "node": f"{node_name}_proxy",
            "ext": ".ply",
            "visible": True,
        }
        try:
            handler([asset], frame=True)
        except TypeError:
            handler([asset])


def _ensure_body_space(node_item, body_h: int) -> None:
    try:
        extra = max(0.0, float(body_h) + 8.0)
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
    inset_x = 6
    inset_top = 2
    inset_bottom = 10
    body = SkinnedSplatProxyWidget(node_item)
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
    _ensure_body_space(node_item, height + inset_top + inset_bottom)
    return y_cursor + inset_top + height + inset_bottom


SKINNED_SPLAT_PROXY_SPEC = Spec(
    stripe_color="#38bdf8",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "KIND_ALIASES",
    "ProxyBuildOutcome",
    "SKINNED_SPLAT_PROXY_SPEC",
    "build_ports",
    "build_skinned_splat_proxy_scene_asset",
    "render_node_body",
]
