from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from echograph.rigging.skinned_volume_mesh import (
    MAX_REMESH_QUALITY,
    MAX_VOLUME_RESOLUTION,
    MIN_REMESH_QUALITY,
    MIN_VOLUME_RESOLUTION,
    SkinnedVolumeMeshError,
    SkinnedVolumeMeshSettings,
    build_skinned_volume_mesh_from_rig_context,
    load_skinned_volume_mesh_asset,
)
from nodes.core import Spec
from nodes.skinned_splat_proxy.spec import (
    _connected_source_item,
    _ensure_hidden_params,
    _ensure_param,
    _param_bool,
    _param_int,
    _param_value,
    _set_param,
    _source_asset_from_item,
    _workflow_dir_for_node,
)
from nodes.util_graph import param_change_relevant as _param_change_relevant


KIND_ALIASES = {
    "skinned_volume_mesh",
    "skinned volume mesh",
    "skinned_collision_mesh",
    "skinned collision mesh",
    "fbx_to_skinned_volume_mesh",
}
_DEFAULT_VOLUME_RESOLUTION = 32
_DEFAULT_REMESH_QUALITY = 2
_DEFAULT_MAX_INFLUENCES = 4


@dataclass(frozen=True)
class VolumeMeshBuildOutcome:
    asset: Optional[Dict[str, Any]]
    status: str
    detail: str
    generated: bool = False


def _output_dir(node_item) -> Path:
    model = getattr(node_item, "model", None)
    raw = _param_value(model, "output_dir").strip()
    if raw:
        out = Path(raw)
    else:
        base = _workflow_dir_for_node(node_item)
        if base is None:
            base = Path(tempfile.gettempdir()) / "EchoGraph"
        out = base / "skinned_volume_mesh"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _settings_from_model(model) -> SkinnedVolumeMeshSettings:
    return SkinnedVolumeMeshSettings(
        volume_resolution=_param_int(
            model,
            "volume_resolution",
            _DEFAULT_VOLUME_RESOLUTION,
            min_value=MIN_VOLUME_RESOLUTION,
            max_value=MAX_VOLUME_RESOLUTION,
        ),
        remesh_quality=_param_int(
            model,
            "remesh_quality",
            _DEFAULT_REMESH_QUALITY,
            min_value=MIN_REMESH_QUALITY,
            max_value=MAX_REMESH_QUALITY,
        ),
        max_influences=_param_int(
            model,
            "max_influences",
            _DEFAULT_MAX_INFLUENCES,
            min_value=1,
            max_value=16,
        ),
        fill_interior=_param_bool(model, "fill_interior", True),
    )


def _artifact_paths(model) -> tuple[str, str, str, str]:
    return (
        _param_value(model, "volume_manifest").strip(),
        _param_value(model, "volume_mesh_obj").strip(),
        _param_value(model, "volume_grid").strip(),
        _param_value(model, "volume_skin").strip(),
    )


def _artifacts_exist(paths: tuple[str, str, str, str]) -> bool:
    try:
        return bool(all(value and Path(value).exists() for value in paths))
    except Exception:
        return False


def _asset_name(node_item, source_asset: Dict[str, Any], settings: SkinnedVolumeMeshSettings) -> str:
    model = getattr(node_item, "model", None)
    node_name = str(getattr(model, "name", "") or "").strip()
    source_name = str(source_asset.get("node") or "").strip()
    base = node_name or source_name or "SkinnedVolumeMesh"
    return f"{base}_d{int(settings.volume_resolution):02d}_q{int(settings.remesh_quality)}"


def _artifact_is_stale(
    manifest_path: str,
    source_path: str,
    settings: SkinnedVolumeMeshSettings,
) -> bool:
    try:
        payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        stored = dict((payload.get("generator") or {}).get("settings") or {})
        if int(stored.get("volume_resolution", -1)) != int(settings.volume_resolution):
            return True
        if int(stored.get("remesh_quality", -1)) != int(settings.remesh_quality):
            return True
        if int(stored.get("max_influences", -1)) != int(settings.max_influences):
            return True
        if bool(stored.get("fill_interior", True)) != bool(settings.fill_interior):
            return True
        stored_source = str(payload.get("source_fbx") or "").strip()
        return bool(stored_source and source_path and stored_source != source_path)
    except Exception:
        return True


def _load_cached_mesh_asset(model, skin_path: str, skeleton):
    try:
        modified = Path(skin_path).stat().st_mtime_ns
    except Exception:
        modified = 0
    key = (str(skin_path), int(modified), id(skeleton))
    cached = getattr(model, "_skinned_volume_mesh_cache", None) if model is not None else None
    if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == key:
        return cached[1]
    mesh_asset = load_skinned_volume_mesh_asset(skin_path, skeleton)
    if model is not None:
        try:
            setattr(model, "_skinned_volume_mesh_cache", (key, mesh_asset))
        except Exception:
            pass
    return mesh_asset


def build_skinned_volume_mesh_scene_asset(
    node_item,
    *,
    generate: bool = False,
) -> VolumeMeshBuildOutcome:
    model = getattr(node_item, "model", None)
    source_item = _connected_source_item(node_item)
    source_asset, source_error = _source_asset_from_item(source_item)
    if not isinstance(source_asset, dict):
        return VolumeMeshBuildOutcome(None, "error", source_error or "No supported input asset.")
    rig_context = source_asset.get("fbx_rig_context")
    if not isinstance(rig_context, dict):
        return VolumeMeshBuildOutcome(None, "error", "Input asset does not contain an FBX rig context.")
    skeleton = rig_context.get("skeleton")
    if skeleton is None:
        return VolumeMeshBuildOutcome(None, "error", "Input rig context does not contain a skeleton.")

    settings = _settings_from_model(model)
    manifest, mesh_obj, volume_grid, skin_npz = _artifact_paths(model)
    generated = False
    if generate:
        try:
            result = build_skinned_volume_mesh_from_rig_context(
                rig_context,
                _output_dir(node_item),
                asset_name=_asset_name(node_item, source_asset, settings),
                source_fbx=str(source_asset.get("path") or ""),
                settings=settings,
            )
        except SkinnedVolumeMeshError as exc:
            return VolumeMeshBuildOutcome(None, "error", str(exc))
        except Exception as exc:
            return VolumeMeshBuildOutcome(None, "error", f"Volume mesh generation failed: {exc}")
        manifest = str(result.manifest_path)
        mesh_obj = str(result.mesh_obj_path)
        volume_grid = str(result.volume_path)
        skin_npz = str(result.skin_npz_path)
        _set_param(node_item, "volume_manifest", manifest, notify_scene=False)
        _set_param(node_item, "volume_mesh_obj", mesh_obj, notify_scene=False)
        _set_param(node_item, "volume_grid", volume_grid, notify_scene=False)
        _set_param(node_item, "volume_skin", skin_npz, notify_scene=False)
        _set_param(node_item, "source", str(source_asset.get("path") or ""), notify_scene=False)
        try:
            setattr(model, "_skinned_volume_mesh_cache", None)
        except Exception:
            pass
        generated = True

    paths = (manifest, mesh_obj, volume_grid, skin_npz)
    if not _artifacts_exist(paths):
        asset = dict(source_asset)
        asset["volume_mesh"] = {
            "type": "skinned_volume_mesh",
            "status": "not_generated",
            "volume_resolution": int(settings.volume_resolution),
            "remesh_quality": int(settings.remesh_quality),
        }
        asset["skinned_volume_mesh_node"] = str(getattr(model, "name", "") or "")
        return VolumeMeshBuildOutcome(
            asset,
            "warning",
            "Connect Anim Retarget, then click Generate.",
        )

    try:
        mesh_asset = _load_cached_mesh_asset(model, skin_npz, skeleton)
    except SkinnedVolumeMeshError as exc:
        return VolumeMeshBuildOutcome(None, "error", str(exc))
    output_rig_context = dict(rig_context)
    output_rig_context["meshes"] = [mesh_asset]
    output_rig_context["mesh_skinning_enabled"] = True

    source_path = str(source_asset.get("path") or "")
    sample_owner = ""
    for key in ("fbx_sample_owner", "sample_owner", "deformer_owner", "source_owner", "rig_owner", "node"):
        candidate = str(source_asset.get(key) or "").strip()
        if candidate:
            sample_owner = candidate
            break
    stale = _artifact_is_stale(manifest, source_path, settings)
    asset = dict(source_asset)
    asset.update(
        {
            "path": mesh_obj,
            "source_path": source_path,
            "texture": "",
            "node": str(getattr(model, "name", "") or "Skinned Volume Mesh"),
            "kind": "skinned_volume_mesh",
            "ext": ".obj",
            "visible": True,
            "has_skeleton": True,
            "fbx_rig_context": output_rig_context,
            # The generated OBJ is in bind space.  Preserve the source
            # animation owner so viewport skinning and guide collision sample
            # the same retimed/composed animation frame.
            "fbx_sample_owner": sample_owner,
            "volume_mesh": {
                "type": "skinned_volume_mesh",
                "manifest": manifest,
                "mesh_obj": mesh_obj,
                "volume": volume_grid,
                "skin_npz": skin_npz,
                "volume_resolution": int(settings.volume_resolution),
                "remesh_quality": int(settings.remesh_quality),
                "stale": bool(stale),
            },
            "skinned_volume_mesh_node": str(getattr(model, "name", "") or ""),
            "skinned_volume_mesh_manifest": manifest,
        }
    )
    if stale:
        return VolumeMeshBuildOutcome(
            asset,
            "warning",
            "Settings or source changed; Generate again.",
            generated=generated,
        )
    return VolumeMeshBuildOutcome(
        asset,
        "ok",
        "Collision mesh generated." if generated else "Collision mesh ready.",
        generated=generated,
    )


def build_ports(node_item) -> None:
    defaults = {
        "mesh": "",
        "source": "",
        "output_dir": "",
        "volume_manifest": "",
        "volume_mesh_obj": "",
        "volume_grid": "",
        "volume_skin": "",
        "volume_resolution": str(_DEFAULT_VOLUME_RESOLUTION),
        "remesh_quality": str(_DEFAULT_REMESH_QUALITY),
        "max_influences": str(_DEFAULT_MAX_INFLUENCES),
        "fill_interior": "1",
    }
    for name, value in defaults.items():
        _ensure_param(node_item, name, value)
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [
            "mesh",
            "source",
            "output_dir",
            "volume_manifest",
            "volume_mesh_obj",
            "volume_grid",
            "volume_skin",
            "volume_resolution",
            "remesh_quality",
            "max_influences",
            "fill_interior",
        ],
    )
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("mesh")


def _resolve_window(node_item):
    try:
        scene = node_item.scene()
        views = scene.views() if scene is not None else []
        if views:
            return views[0].window()
    except Exception:
        pass
    return QtWidgets.QApplication.activeWindow()


class SkinnedVolumeMeshWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._pending = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(5)

        self._status = QtWidgets.QLabel("Connect Anim Retarget")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        layout.addWidget(self._status)

        self._density_slider, self._density_input = self._add_slider_row(
            layout,
            "Volume resolution",
            MIN_VOLUME_RESOLUTION,
            MAX_VOLUME_RESOLUTION,
        )
        self._density_slider.setSingleStep(1)
        self._density_slider.setPageStep(16)
        self._density_slider.setToolTip(
            "Voxel resolution along the model's longest axis. Higher values add surface detail."
        )
        self._density_input.setToolTip(self._density_slider.toolTip())
        self._density_slider.valueChanged.connect(self._density_input.setValue)
        self._density_input.valueChanged.connect(self._density_slider.setValue)
        self._density_input.valueChanged.connect(self._on_density_changed)

        self._quality_slider, self._quality_input = self._add_slider_row(
            layout,
            "Surface smoothing",
            MIN_REMESH_QUALITY,
            MAX_REMESH_QUALITY,
        )
        self._quality_slider.setPageStep(5)
        self._quality_slider.setToolTip(
            "Number of smoothing passes. This rounds voxel edges; it does not add polygons."
        )
        self._quality_input.setToolTip(self._quality_slider.toolTip())
        self._quality_slider.valueChanged.connect(self._quality_input.setValue)
        self._quality_input.valueChanged.connect(self._quality_slider.setValue)
        self._quality_input.valueChanged.connect(self._on_quality_changed)

        note = QtWidgets.QLabel("Resolution adds detail; smoothing rounds voxel edges.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b;font-size:10px;")
        layout.addWidget(note)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        self._generate_btn = QtWidgets.QPushButton("Generate")
        self._generate_btn.setMinimumHeight(24)
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        buttons.addWidget(self._generate_btn)
        self._view_btn = QtWidgets.QPushButton("View Mesh")
        self._view_btn.setMinimumHeight(24)
        self._view_btn.clicked.connect(self._on_view_clicked)
        buttons.addWidget(self._view_btn)
        layout.addLayout(buttons)

        self._sync_from_params()
        self._ensure_scene()
        QtCore.QTimer.singleShot(0, self._refresh_status)

    @staticmethod
    def _add_slider_row(layout, label: str, minimum: int, maximum: int):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QtWidgets.QLabel(label), 0)
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(minimum, maximum)
        row.addWidget(slider, 1)
        value_input = QtWidgets.QSpinBox()
        value_input.setRange(minimum, maximum)
        value_input.setMinimumWidth(64)
        value_input.setKeyboardTracking(False)
        value_input.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        row.addWidget(value_input, 0)
        layout.addLayout(row)
        return slider, value_input

    def sizeHint(self):
        return QtCore.QSize(288, 184)

    def minimumSizeHint(self):
        return QtCore.QSize(272, 176)

    def _ensure_scene(self) -> None:
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
        settings = _settings_from_model(getattr(self._node_item, "model", None))
        self._density_slider.blockSignals(True)
        self._density_input.blockSignals(True)
        self._quality_slider.blockSignals(True)
        self._quality_input.blockSignals(True)
        try:
            self._density_slider.setValue(int(settings.volume_resolution))
            self._density_input.setValue(int(settings.volume_resolution))
            self._quality_slider.setValue(int(settings.remesh_quality))
            self._quality_input.setValue(int(settings.remesh_quality))
        finally:
            self._density_slider.blockSignals(False)
            self._density_input.blockSignals(False)
            self._quality_slider.blockSignals(False)
            self._quality_input.blockSignals(False)

    def _refresh_status(self):
        self._pending = False
        self._sync_from_params()
        outcome = build_skinned_volume_mesh_scene_asset(self._node_item, generate=False)
        volume_mesh = (outcome.asset or {}).get("volume_mesh") if outcome.asset else None
        self._view_btn.setEnabled(bool(isinstance(volume_mesh, dict) and volume_mesh.get("mesh_obj")))
        color = "#22c55e" if outcome.status == "ok" else "#f59e0b" if outcome.status == "warning" else "#ef4444"
        self._status.setStyleSheet(f"color:{color};font-size:11px;")
        self._status.setText(outcome.detail)

    def _on_density_changed(self, value: int):
        _set_param(self._node_item, "volume_resolution", str(int(value)), notify_scene=True)
        self._schedule_refresh()

    def _on_quality_changed(self, value: int):
        _set_param(self._node_item, "remesh_quality", str(int(value)), notify_scene=True)
        self._schedule_refresh()

    def _set_busy(self, active: bool) -> None:
        setter = getattr(self._node_item, "setBusyState", None)
        if callable(setter):
            try:
                setter(bool(active), "voxelizing" if active else "")
            except Exception:
                pass
        try:
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass

    def _on_generate_clicked(self):
        self._generate_btn.setEnabled(False)
        self._set_busy(True)
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setText("Voxelizing and transferring skin weights...")
        try:
            outcome = build_skinned_volume_mesh_scene_asset(self._node_item, generate=True)
            color = "#22c55e" if outcome.status == "ok" else "#ef4444"
            self._status.setStyleSheet(f"color:{color};font-size:11px;")
            self._status.setText(outcome.detail)
        finally:
            self._set_busy(False)
            self._generate_btn.setEnabled(True)
            self._schedule_refresh()

    def _on_view_clicked(self):
        outcome = build_skinned_volume_mesh_scene_asset(self._node_item, generate=False)
        if not isinstance(outcome.asset, dict):
            self._refresh_status()
            return
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if not callable(handler):
            return
        try:
            handler([dict(outcome.asset)], frame=True)
        except TypeError:
            handler([dict(outcome.asset)])


def render_node_body(node_item, y_cursor: int) -> int:
    inset_x = 6
    inset_top = 2
    inset_bottom = 10
    body = SkinnedVolumeMeshWidget(node_item)
    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    required_width = float(hint.width() + (inset_x * 2))
    try:
        if float(getattr(node_item, "width", 0.0) or 0.0) < required_width:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.width = required_width
    except Exception:
        pass
    body.setMinimumSize(hint)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(inset_x, y_cursor + inset_top)
    body_height = max(36, int(hint.height()))
    proxy.resize(max(40, int(node_item.width) - (inset_x * 2)), body_height)
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass

    required_height = float(y_cursor + inset_top + body_height + inset_bottom)
    try:
        if float(getattr(node_item, "height", 0.0) or 0.0) < required_height:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = required_height
    except Exception:
        pass
    return int(required_height)


SKINNED_VOLUME_MESH_SPEC = Spec(
    stripe_color="#14b8a6",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "KIND_ALIASES",
    "SKINNED_VOLUME_MESH_SPEC",
    "VolumeMeshBuildOutcome",
    "build_ports",
    "build_skinned_volume_mesh_scene_asset",
    "render_node_body",
]
