from __future__ import annotations

import math
import re
import shutil
import tempfile
from pathlib import Path

try:
    from PySide6 import QtCore, QtWidgets
except Exception:
    from PySide2 import QtCore, QtWidgets  # type: ignore

from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

SUPPORTED_MESH_EXTS = {".obj", ".fbx", ".gltf", ".glb", ".stl", ".off", ".om"}


def _sanitize_name(name: str, fallback: str = "object") -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip())
    return safe.strip("_") or fallback


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
        return Path(workflow_path).parent
    except Exception:
        return None


def _export_dir(node_item) -> Path:
    base = _workflow_dir_for_node(node_item)
    if base is None:
        base = Path(tempfile.gettempdir()) / "EchoGraph"
    out = base / "exports"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for p in (getattr(model, "params", None) or []):
        if (p.get("name") or "").strip().lower() == key:
            return p.get("value", "") or ""
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
    hidden_entry = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == store_key:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": store_key, "value": ""}
        params.append(hidden_entry)

    raw = hidden_entry.get("value", "")
    hidden = {tok.strip().lower() for tok in str(raw).split(",") if tok.strip()}
    for name in (names or []):
        if name:
            hidden.add(str(name).strip().lower())
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _set_param_value(node_item, name: str, value: str, notify_scene: bool = True) -> None:
    try:
        node_item._set_param_value(name, value, rebuild=False, notify_scene=notify_scene)
    except Exception:
        pass


def _param_bool(model, name: str, default: bool = False) -> bool:
    raw = _param_value(model, name).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "y"}


def build_ports(node_item) -> None:
    _ensure_param(node_item, "output", "")
    _ensure_param(node_item, "include_hidden", "1")
    _ensure_hidden_params(getattr(node_item, "model", None), ["output", "include_hidden"])
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input("scene")


def _ordered_in_edges(scene, item):
    try:
        return list(scene._ordered_in_edges(item))
    except Exception:
        try:
            return list(scene._in_edges(item))
        except Exception:
            return []


def _resolve_scene_input_item(node_item):
    scene = node_item.scene()
    if scene is None:
        return None

    edges = _ordered_in_edges(scene, node_item)
    if not edges:
        return None

    selected = None
    for edge in edges:
        name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None)
        if (name or "").strip().lower() == "scene":
            selected = edge
            break
    if selected is None:
        selected = edges[0]

    src = getattr(selected, "src", None)
    visited = set()
    depth = 0
    passthrough = {"switch"}
    while src is not None and src not in visited and depth < 8:
        visited.add(src)
        depth += 1
        model = getattr(src, "model", None)
        if model is None:
            return None
        kind = (getattr(model, "kind", "") or "").strip().lower()
        if kind in {"scene", "scene_assembly", "scene_outliner"}:
            return src
        if kind not in passthrough:
            break
        upstream = _ordered_in_edges(scene, src)
        src = getattr(upstream[0], "src", None) if upstream else None
    return None


def _resolve_existing_path(raw_path: str, base_dir: Path | None) -> Path | None:
    if not raw_path:
        return None
    try:
        path = Path(raw_path).expanduser()
    except Exception:
        return None

    try:
        if path.exists():
            return path.resolve()
    except Exception:
        pass

    if base_dir is None or path.is_absolute():
        return None

    try:
        alt = (base_dir / path).resolve()
        if alt.exists():
            return alt
    except Exception:
        return None
    return None


def _collect_scene_assets_for_export(node_item, include_hidden: bool = False):
    src_scene_item = _resolve_scene_input_item(node_item)
    if src_scene_item is None:
        return [], "Connect a Scene node to this Export FBX node first."

    try:
        from nodes.scene import spec as scene_spec
    except Exception:
        return [], "Scene module is unavailable."

    collector = getattr(scene_spec, "_collect_assets", None)
    if not callable(collector):
        return [], "Scene collector is unavailable."

    try:
        raw_assets = list(collector(src_scene_item) or [])
    except Exception:
        return [], "Failed to read assets from the connected Scene node."
    if not raw_assets:
        return [], "Connected Scene has no assets."

    base_dir = _workflow_dir_for_node(node_item)
    out = []
    used = set()
    for entry in raw_assets:
        if not isinstance(entry, dict):
            continue
        if (not include_hidden) and (not bool(entry.get("visible", True))):
            continue
        if bool(entry.get("wire_only")) or bool(entry.get("volume")):
            continue

        raw_path = (entry.get("path") or "").strip()
        if not raw_path:
            continue
        ext = Path(raw_path).suffix.lower()
        if ext == ".ply":
            continue
        if ext not in SUPPORTED_MESH_EXTS:
            continue

        resolved = _resolve_existing_path(raw_path, base_dir)
        if resolved is None:
            continue

        texture_path = None
        raw_tex = (entry.get("texture") or "").strip()
        if raw_tex:
            texture_path = _resolve_existing_path(raw_tex, base_dir)

        base_name = _sanitize_name(entry.get("node") or resolved.stem, "object")
        name = base_name
        suffix = 2
        while name in used:
            name = f"{base_name}_{suffix}"
            suffix += 1
        used.add(name)

        xform = entry.get("xform")
        out.append(
            {
                "name": name,
                "path": resolved,
                "texture_path": texture_path,
                "xform_offset": bool(entry.get("xform_offset")),
                "xform": xform if isinstance(xform, dict) else None,
            }
        )

    if not out:
        return [], "No exportable mesh assets found (splats and wire-only volumes are skipped)."
    return out, ""


def _parse_vec3(value, default):
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return float(value[0]), float(value[1]), float(value[2])
        except Exception:
            pass
    try:
        parts = [p.strip() for p in str(value or "").split(",")]
        if len(parts) >= 3:
            return float(parts[0]), float(parts[1]), float(parts[2])
    except Exception:
        pass
    return default


def _load_mesh_as_trimesh(path: Path):
    try:
        import trimesh
    except Exception:
        return None

    loaded = None
    try:
        loaded = trimesh.load(str(path), force="scene")
    except Exception:
        try:
            loaded = trimesh.load(str(path))
        except Exception:
            return None

    mesh = None
    if isinstance(loaded, trimesh.Trimesh):
        mesh = loaded.copy()
    elif isinstance(loaded, trimesh.Scene):
        try:
            dumped = loaded.dump(concatenate=True)
            if isinstance(dumped, trimesh.Trimesh):
                mesh = dumped
            elif isinstance(dumped, (list, tuple)):
                meshes = [m for m in dumped if isinstance(m, trimesh.Trimesh)]
                if meshes:
                    mesh = trimesh.util.concatenate(meshes)
        except Exception:
            mesh = None
    elif isinstance(loaded, (list, tuple)):
        try:
            meshes = [m for m in loaded if isinstance(m, trimesh.Trimesh)]
            if meshes:
                mesh = trimesh.util.concatenate(meshes)
        except Exception:
            mesh = None

    if mesh is None or not isinstance(mesh, trimesh.Trimesh):
        return None
    try:
        if mesh.vertices is None or mesh.faces is None:
            return None
        if len(mesh.vertices) == 0 or len(mesh.faces) == 0:
            return None
    except Exception:
        return None
    try:
        mesh.remove_unreferenced_vertices()
    except Exception:
        pass
    return mesh


def _scene_xform_matrix(mesh, xform, xform_offset: bool = False, xform_space: str = "local"):
    import numpy as np

    if not isinstance(xform, dict):
        return np.eye(4, dtype="f8")
    pos = _parse_vec3(xform.get("pos"), (0.0, 0.0, 0.0))
    rot = _parse_vec3(xform.get("rot"), (0.0, 0.0, 0.0))
    scl = _parse_vec3(xform.get("scl"), (1.0, 1.0, 1.0))

    sx, sy, sz = scl
    rx, ry, rz = rot
    rx = -float(rx)
    ry = -float(ry)
    rz = -float(rz)

    def _t(tx, ty, tz):
        m = np.eye(4, dtype="f8")
        m[3, 0] = tx
        m[3, 1] = ty
        m[3, 2] = tz
        return m

    def _s(sx_v, sy_v, sz_v):
        m = np.eye(4, dtype="f8")
        m[0, 0] = sx_v
        m[1, 1] = sy_v
        m[2, 2] = sz_v
        return m

    def _rx(a):
        a = math.radians(a)
        c, s = math.cos(a), math.sin(a)
        m = np.eye(4, dtype="f8")
        m[1, 1] = c
        m[1, 2] = s
        m[2, 1] = -s
        m[2, 2] = c
        return m

    def _ry(a):
        a = math.radians(a)
        c, s = math.cos(a), math.sin(a)
        m = np.eye(4, dtype="f8")
        m[0, 0] = c
        m[0, 2] = -s
        m[2, 0] = s
        m[2, 2] = c
        return m

    def _rz(a):
        a = math.radians(a)
        c, s = math.cos(a), math.sin(a)
        m = np.eye(4, dtype="f8")
        m[0, 0] = c
        m[0, 1] = s
        m[1, 0] = -s
        m[1, 1] = c
        return m

    try:
        bmin, bmax = mesh.bounds
        center = (np.asarray(bmin, dtype="f8") + np.asarray(bmax, dtype="f8")) * 0.5
    except Exception:
        center = np.zeros(3, dtype="f8")
    if xform_offset:
        try:
            pos = (
                float(pos[0]) + float(center[0]),
                float(pos[1]) + float(center[1]),
                float(pos[2]) + float(center[2]),
            )
        except Exception:
            pass

    cx, cy, cz = float(center[0]), float(center[1]), float(center[2])
    px, py, pz = float(pos[0]), float(pos[1]), float(pos[2])
    rot_scale = _rx(rx) @ _ry(ry) @ _rz(rz)

    # Match renderer row-vector matrix and transpose for trimesh (column-vector).
    space = str(xform_space or "local").strip().lower()
    if space == "world":
        model_row = _t(-cx, -cy, -cz) @ rot_scale @ _s(float(sx), float(sy), float(sz)) @ _t(px, py, pz)
    else:
        model_row = _t(-cx, -cy, -cz) @ _s(float(sx), float(sy), float(sz)) @ rot_scale @ _t(px, py, pz)
    return np.asarray(model_row, dtype="f8").T


def _apply_texture_material(mesh, texture_path: Path | None, material_name: str) -> None:
    if texture_path is None:
        return
    try:
        import trimesh
        from PIL import Image
    except Exception:
        return
    try:
        uv = getattr(getattr(mesh, "visual", None), "uv", None)
        if uv is None:
            return
        image = Image.open(str(texture_path)).convert("RGBA")
        material = trimesh.visual.material.PBRMaterial(
            name=f"{material_name}_mat",
            baseColorTexture=image,
        )
        mesh.visual = trimesh.visual.texture.TextureVisuals(
            uv=uv,
            image=image,
            material=material,
        )
    except Exception:
        return


def _build_export_scene(asset_rows, xform_space: str = "local"):
    try:
        import numpy as np
        import trimesh
    except Exception:
        return None, 0, list(asset_rows or [])

    scene = trimesh.Scene()
    exported = 0
    skipped = []
    for row in list(asset_rows or []):
        path = row.get("path")
        if not isinstance(path, Path):
            skipped.append(row)
            continue
        mesh = _load_mesh_as_trimesh(path)
        if mesh is None:
            skipped.append(row)
            continue
        xform = row.get("xform")
        xform_offset = bool(row.get("xform_offset"))
        if isinstance(xform, dict):
            try:
                mesh.apply_transform(
                    _scene_xform_matrix(
                        mesh,
                        xform,
                        xform_offset=xform_offset,
                        xform_space=xform_space,
                    )
                )
            except Exception:
                pass
        name = _sanitize_name(row.get("name") or path.stem, "object")
        tex_path = row.get("texture_path")
        if isinstance(tex_path, Path):
            _apply_texture_material(mesh, tex_path, name)
        try:
            scene.add_geometry(mesh, node_name=name, geom_name=name, transform=np.eye(4))
        except Exception:
            skipped.append(row)
            continue
        exported += 1
    return scene, exported, skipped


def _export_trimesh_scene_to_fbx(scene_obj, output_path: Path) -> None:
    from echograph.ui.gl_loaders import ensure_assimp_dll

    ensure_assimp_dll()
    import pyassimp

    tmp_dir = Path(tempfile.mkdtemp(prefix="echograph_export_fbx_"))
    glb_path = tmp_dir / "scene.glb"
    try:
        scene_obj.export(glb_path)
        with pyassimp.load(str(glb_path), file_type="glb") as ai_scene:
            pyassimp.export(ai_scene, str(output_path), file_type="fbx")
    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


def _default_output_path(node_item) -> Path:
    scene_item = _resolve_scene_input_item(node_item)
    if scene_item is not None and getattr(scene_item, "model", None) is not None:
        scene_name = _sanitize_name(getattr(scene_item.model, "name", "") or "scene", "scene")
    else:
        scene_name = _sanitize_name(getattr(getattr(node_item, "model", None), "name", "") or "scene", "scene")
    return _export_dir(node_item) / f"{scene_name}_export.fbx"


def _dialog_parent(node_item):
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                if win is not None:
                    return win
        except Exception:
            pass
    try:
        win = node_item.window()
        if win is not None:
            return win
    except Exception:
        pass
    try:
        aw = QtWidgets.QApplication.activeWindow()
        if aw is not None and aw.isWindow():
            return aw
    except Exception:
        pass
    return None


def _scene_xform_space(node_item) -> str:
    parent = _dialog_parent(node_item)
    glv = getattr(parent, "gl_view", None) if parent is not None else None
    if glv is None:
        return "local"

    raw = str(getattr(glv, "_mgl_xform_space", "") or "").strip().lower()
    if raw in {"local", "world"}:
        return raw
    try:
        return "local" if bool(getattr(glv, "_xform_use_local", True)) else "world"
    except Exception:
        return "local"


class ExportFBXWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self._busy = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")
        self._status.setMinimumWidth(0)
        self._status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(self._status, 0)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._output_edit = QtWidgets.QLineEdit()
        self._output_edit.setPlaceholderText("Output FBX path")
        self._output_edit.setMinimumHeight(24)
        self._output_edit.setStyleSheet(
            "QLineEdit{background:#0f1216;color:#e6edf3;border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
        )
        self._output_edit.editingFinished.connect(self._on_output_edit_committed)
        row.addWidget(self._output_edit, 1)

        self._browse_btn = QtWidgets.QPushButton("Browse")
        self._browse_btn.setFixedWidth(70)
        self._browse_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:2px 6px;}"
            "QPushButton:hover{background:#273449;}"
        )
        self._browse_btn.clicked.connect(self._on_browse_clicked)
        row.addWidget(self._browse_btn, 0)
        layout.addLayout(row, 0)

        self._include_hidden = QtWidgets.QCheckBox("Include hidden")
        self._include_hidden.setStyleSheet("QCheckBox{color:#94a3b8;font-size:10px;}")
        self._include_hidden.stateChanged.connect(self._on_include_hidden_changed)
        layout.addWidget(self._include_hidden, 0)

        self._export_btn = QtWidgets.QPushButton("Export FBX")
        self._export_btn.setStyleSheet(
            "QPushButton{background:#0f766e;color:#f8fafc;border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#0d9488;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;}"
        )
        self._export_btn.clicked.connect(self._on_export_clicked)
        layout.addWidget(self._export_btn, 0)

        self._ensure_scene()
        self._sync_controls_from_params()
        self._refresh_status()

    def sizeHint(self):
        return QtCore.QSize(240, 96)

    def _ensure_scene(self):
        if self._scene is None:
            self._scene = self._node_item.scene()
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._refresh_status)
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
            self._sync_controls_from_params()
            self._refresh_status()

    def _sync_controls_from_params(self):
        model = getattr(self._node_item, "model", None)
        if model is None:
            return
        raw_output = (_param_value(model, "output") or "").strip()
        if not raw_output:
            raw_output = str(_default_output_path(self._node_item))
        try:
            self._output_edit.blockSignals(True)
            self._output_edit.setText(raw_output)
        finally:
            self._output_edit.blockSignals(False)

        include_hidden = _param_bool(model, "include_hidden", True)
        try:
            self._include_hidden.blockSignals(True)
            self._include_hidden.setChecked(include_hidden)
        finally:
            self._include_hidden.blockSignals(False)

    def _on_output_edit_committed(self):
        text = (self._output_edit.text() or "").strip()
        _set_param_value(self._node_item, "output", text, notify_scene=True)

    def _on_include_hidden_changed(self, _state):
        enabled = bool(self._include_hidden.isChecked())
        _set_param_value(self._node_item, "include_hidden", "1" if enabled else "0", notify_scene=True)
        self._refresh_status()

    def _set_busy(self, busy: bool):
        self._busy = bool(busy)
        self._export_btn.setEnabled(not self._busy)
        if self._busy:
            self._export_btn.setText("Exporting...")
        else:
            self._export_btn.setText("Export FBX")

    def _refresh_status(self):
        include_hidden = bool(self._include_hidden.isChecked())
        assets, err = _collect_scene_assets_for_export(self._node_item, include_hidden=include_hidden)
        if err:
            self._status.setText(err)
            if not self._busy:
                self._export_btn.setEnabled(False)
            return

        count = len(assets)
        if include_hidden:
            self._status.setText(f"{count} exportable object(s) including hidden.")
        else:
            self._status.setText(f"{count} visible exportable object(s).")
        if not self._busy:
            self._export_btn.setEnabled(count > 0)

    def _resolved_output_path(self) -> Path:
        raw = (self._output_edit.text() or "").strip()
        if not raw:
            out = _default_output_path(self._node_item)
        else:
            out = Path(raw).expanduser()
            if not out.is_absolute():
                base = _workflow_dir_for_node(self._node_item) or _export_dir(self._node_item)
                out = (base / out).resolve()
        if out.suffix.lower() != ".fbx":
            out = out.with_suffix(".fbx")
        return out

    def _on_browse_clicked(self):
        start = self._resolved_output_path()
        try:
            start.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        parent = _dialog_parent(self._node_item) or self
        opts = QtWidgets.QFileDialog.Options()
        try:
            opts |= QtWidgets.QFileDialog.DontUseNativeDialog
        except Exception:
            pass
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            parent,
            "Export FBX",
            str(start),
            "FBX Files (*.fbx)",
            options=opts,
        )
        if not path:
            return
        if not path.lower().endswith(".fbx"):
            path = f"{path}.fbx"
        self._output_edit.setText(path)
        _set_param_value(self._node_item, "output", path, notify_scene=True)

    def _show_popup(self, icon, text: str, details: str = "") -> None:
        parent = _dialog_parent(self._node_item) or self
        box = QtWidgets.QMessageBox(parent)
        box.setWindowTitle("Export FBX")
        box.setIcon(icon)
        box.setText(str(text or ""))
        try:
            box.setTextFormat(QtCore.Qt.PlainText)
        except Exception:
            pass
        if details:
            try:
                box.setDetailedText(str(details))
            except Exception:
                pass
        box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        box.setStyleSheet(
            "QMessageBox{background:#0f1216;color:#e6edf3;}"
            "QLabel{color:#e6edf3;}"
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;border-radius:4px;padding:4px 12px;min-width:72px;}"
            "QPushButton:hover{background:#273449;}"
        )
        try:
            box.exec()
        except Exception:
            box.exec_()

    def _on_export_clicked(self):
        include_hidden = bool(self._include_hidden.isChecked())
        assets, err = _collect_scene_assets_for_export(self._node_item, include_hidden=include_hidden)
        if err:
            self._show_popup(QtWidgets.QMessageBox.Warning, err)
            self._refresh_status()
            return

        out_path = self._resolved_output_path()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            self._show_popup(QtWidgets.QMessageBox.Critical, "Failed to create output directory.")
            return

        self._set_busy(True)
        try:
            scene_obj, exported_count, skipped = _build_export_scene(
                assets,
                xform_space=_scene_xform_space(self._node_item),
            )
            if scene_obj is None or exported_count <= 0:
                self._show_popup(QtWidgets.QMessageBox.Warning, "No mesh data could be prepared for export.")
                return

            _export_trimesh_scene_to_fbx(scene_obj, out_path)
            if not out_path.exists():
                raise RuntimeError("FBX file was not created.")

            _set_param_value(self._node_item, "output", str(out_path), notify_scene=True)
            _set_param_value(self._node_item, "include_hidden", "1" if include_hidden else "0", notify_scene=True)
            msg = f"Exported {exported_count} object(s) to:\n{out_path}"
            if skipped:
                msg += f"\n\nSkipped {len(skipped)} asset(s) that could not be meshed."
            self._show_popup(QtWidgets.QMessageBox.Information, msg)
        except Exception as exc:
            self._show_popup(QtWidgets.QMessageBox.Critical, "Export failed.", str(exc))
        finally:
            self._set_busy(False)
            self._refresh_status()


def render_node_body(node_item, y_cursor: int) -> int:
    body = ExportFBXWidget(node_item)
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


EXPORT_FBX_SPEC = Spec(
    stripe_color="#f97316",
    render_node_body=render_node_body,
    build_ports=build_ports,
)
