from __future__ import annotations

from pathlib import Path
from typing import Dict, List

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except Exception:
    from PySide2 import QtWidgets, QtCore, QtGui  # type: ignore

from nodes.core import Spec
import traceback

SUPPORTED_EXTS = {".fbx", ".obj", ".gltf", ".glb", ".ply", ".stl", ".off", ".om"}

_EYE_ICON_CACHE = {}


def _eye_icon(visible: bool) -> QtGui.QIcon:
    key = "on" if visible else "off"
    icon = _EYE_ICON_CACHE.get(key)
    if icon is not None:
        return icon
    size = 14
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pm)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    base_color = QtGui.QColor("#e2e8f0")
    pen = QtGui.QPen(base_color)
    pen.setWidthF(1.2)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    rect = QtCore.QRectF(1.6, 4.0, size - 3.2, size - 8.0)
    painter.drawEllipse(rect)
    if visible:
        painter.setBrush(base_color)
        painter.drawEllipse(QtCore.QPointF(size * 0.5, size * 0.5), 2.0, 2.0)
    else:
        hide_pen = QtGui.QPen(QtGui.QColor("#fca5a5"))
        hide_pen.setWidthF(1.4)
        painter.setPen(hide_pen)
        painter.drawLine(3.0, size - 3.0, size - 3.0, 3.0)
    painter.end()
    icon = QtGui.QIcon(pm)
    _EYE_ICON_CACHE[key] = icon
    return icon


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return entry.get("value") or ""
    return ""


def _collect_assets(node_item) -> List[Dict[str, str]]:
    scene = node_item.scene()
    if scene is None:
        return []
    hidden = set()
    xforms = {}
    try:
        raw_hidden = getattr(getattr(node_item, "model", None), "_scene_hidden", None)
        if isinstance(raw_hidden, set):
            hidden = raw_hidden
        elif isinstance(raw_hidden, (list, tuple)):
            hidden = {str(x) for x in raw_hidden if x}
    except Exception:
        hidden = set()
    try:
        raw_xforms = getattr(getattr(node_item, "model", None), "_scene_xforms", None)
        if isinstance(raw_xforms, dict):
            xforms = raw_xforms
    except Exception:
        xforms = {}
    try:
        in_edges = list(scene._ordered_in_edges(node_item))
    except Exception:
        try:
            in_edges = list(scene._in_edges(node_item))
        except Exception:
            in_edges = []

    assets: List[Dict[str, str]] = []
    seen = set()
    for edge in in_edges:
        src_item = getattr(edge, "src", None)
        model = getattr(src_item, "model", None)
        if model is None:
            continue
        path = _param_value(model, "path")
        if not path:
            continue
        ext = Path(path).suffix.lower()
        if ext not in SUPPORTED_EXTS:
            continue
        key = path.strip()
        if key in seen:
            continue
        seen.add(key)
        texture = _param_value(model, "texture") if ext == ".obj" else ""
        node_name = getattr(model, "name", "") or ""
        xf = None
        try:
            if node_name and node_name in xforms:
                xf = xforms.get(node_name)
            elif node_name:
                # fallback case-insensitive match
                nl = node_name.lower()
                for k, v in xforms.items():
                    if str(k).strip().lower() == nl:
                        xf = v
                        break
        except Exception:
            xf = None
        assets.append(
            {
                "path": path,
                "texture": texture,
                "node": node_name,
                "ext": ext,
                "visible": node_name not in hidden,
                "xform": xf if isinstance(xf, dict) else None,
            }
        )
    return assets


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
    try:
        aw = QtWidgets.QApplication.activeWindow()
        if aw is not None and aw.isWindow():
            return aw
    except Exception:
        pass
    return None


class SceneAssemblyWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._scene = None
        self._scene_connected = False
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color:#94a3b8;font-size:11px;")

        view_btn = QtWidgets.QPushButton("View Scene")
        view_btn.clicked.connect(self._on_view_clicked)
        view_btn.setStyleSheet(
            "QPushButton{background:#1f2937;color:#e2e8f0;border:1px solid #475569;"
            "border-radius:4px;padding:6px 10px;}"
            "QPushButton:hover{background:#273449;}"
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(view_btn, 0)
        layout.addWidget(self._status, 0)

        self._ensure_scene()
        self._update_status()

    def sizeHint(self):
        return QtCore.QSize(200, 54)

    def _ensure_scene(self):
        if self._scene is None:
            self._scene = self._node_item.scene()
        if self._scene is None or self._scene_connected:
            return
        if hasattr(self._scene, "linksChanged"):
            try:
                self._scene.linksChanged.connect(self._update_status)
            except Exception:
                pass
        if hasattr(self._scene, "paramChanged"):
            try:
                self._scene.paramChanged.connect(lambda *_: self._update_status())
            except Exception:
                pass
        self._scene_connected = True

    def _update_status(self, *_):
        assets = _collect_assets(self._node_item)
        if not assets:
            self._status.setText("No 3D assets connected.")
            return
        mesh_count = sum(1 for a in assets if a.get("ext") != ".ply")
        splat_count = len(assets) - mesh_count
        label = f"{len(assets)} connected (mesh {mesh_count}"
        if splat_count:
            label += f", splat {splat_count}"
        label += ")"
        self._status.setText(label)

    def _on_view_clicked(self):
        assets = _collect_assets(self._node_item)
        if not assets:
            QtWidgets.QMessageBox.information(
                self,
                "Scene",
                "Connect one or more 3D import nodes first.",
            )
            return
        # Ensure splats start visible on open (avoid auto-hidden splats)
        try:
            raw_hidden = getattr(self._node_item, "model", None)
            raw_hidden = getattr(raw_hidden, "_scene_hidden", None)
            if isinstance(raw_hidden, set):
                hidden_set = raw_hidden
            elif isinstance(raw_hidden, (list, tuple)):
                hidden_set = {str(x) for x in raw_hidden if x}
            else:
                hidden_set = set()
            changed = False
            for a in assets:
                if str(a.get("ext", "")).lower() == ".ply":
                    name = (a.get("node") or "").strip()
                    if name and name in hidden_set:
                        hidden_set.discard(name)
                        changed = True
                    a["visible"] = True
            if changed:
                try:
                    setattr(self._node_item.model, "_scene_hidden", hidden_set)
                except Exception:
                    pass
        except Exception:
            pass
        win = _resolve_window(self._node_item)
        handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if not callable(handler):
            QtWidgets.QMessageBox.warning(
                self,
                "Scene",
                "3D view is not available.",
            )
            return
        handler(assets)


def augment_infocard_footer(card, footer_layout) -> bool:
    node = getattr(card, "_node_ref", None)
    print("[SceneSpec] augment_infocard_footer called from:", __file__, flush=True)
    if not node:
        return False

    try:
        footer_layout.setContentsMargins(0, 0, 0, 0)
    except Exception:
        pass

    # Reuse existing UI to avoid vertical jitter and reflow chaos
    container = getattr(card, "_scene_footer_container", None)
    if container is not None:
        if footer_layout.count() == 0:
            footer_layout.addWidget(container, 1)

        # keep data current
        sc = getattr(card, "_graph_scene", None)
        try:
            fn = getattr(card, "_scene_outliner_connect", None)
            if callable(fn) and sc is not None:
                fn(sc)
        except Exception:
            pass
        try:
            fn = getattr(card, "_scene_outliner_refresh", None)
            if callable(fn):
                fn(sc)
        except Exception:
            pass

        return True

    # -------- build UI once --------
    container = QtWidgets.QWidget()
    container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    card._scene_footer_container = container

    layout = QtWidgets.QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)

    footer_layout.addWidget(container, 1)

    try:
        # --- Scene Outliner ---
        title = QtWidgets.QLabel("Scene Outliner")
        title.setStyleSheet("color:#94a3b8;font-size:11px;")
        title.setMinimumWidth(0)
        title.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(title)

        outliner = QtWidgets.QListWidget()
        outliner.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        outliner.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        outliner.setMinimumWidth(0)
        outliner.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        outliner.setStyleSheet(
            "QListWidget{background:#0f1216;color:#e2e8f0;border:1px solid #3c4450;border-radius:6px;}"
            "QListWidget::item{padding:2px 6px;}"
            "QListWidget::item:selected{background:#334155;}"
        )
        outliner.setMinimumHeight(70)
        outliner.setMaximumHeight(140)
        layout.addWidget(outliner)
        card._scene_outliner_widget = outliner

        # --- Render Settings ---
        render_title = QtWidgets.QLabel("Render Settings")
        render_title.setStyleSheet("color:#94a3b8;font-size:11px;")
        render_title.setMinimumWidth(0)
        render_title.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(render_title)

        render_panel = QtWidgets.QWidget()
        render_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        render_panel.setStyleSheet("QWidget{background:transparent;border:none;}")

        rp = QtWidgets.QHBoxLayout(render_panel)
        rp.setContentsMargins(6, 4, 6, 4)
        rp.setSpacing(8)

        lab = QtWidgets.QLabel("Depth Test (Splats)")
        lab.setStyleSheet("color:#cbd5e1;")
        lab.setWordWrap(True)
        lab.setMinimumWidth(0)
        lab.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        rp.addWidget(lab, 1, QtCore.Qt.AlignLeft)

        raw = ""
        try:
            raw = _param_value(node, "splat_depth_test").strip().lower()
        except Exception:
            raw = ""
        depth_on = True if raw in ("1", "true", "yes", "on") else False

        chk = QtWidgets.QCheckBox()
        chk.setText("")
        chk.setChecked(depth_on)
        chk.setStyleSheet("QCheckBox{padding:0;margin:0;}")

        box = QtWidgets.QWidget()
        box.setFixedSize(18, 18)
        box.setStyleSheet("QWidget{border:1px solid #3c4450;border-radius:3px;background:transparent;}")

        box_lay = QtWidgets.QHBoxLayout(box)
        box_lay.setContentsMargins(0, 0, 0, 0)
        box_lay.setSpacing(0)
        box_lay.setAlignment(QtCore.Qt.AlignCenter)
        box_lay.addWidget(chk)

        rp.addWidget(box, 0, QtCore.Qt.AlignLeft)
        box.mousePressEvent = lambda e: chk.toggle()
        rp.addStretch(1)

        def _apply_depth(v: bool):
            try:
                params = list(getattr(node, "params", None) or [])
                found = False
                for p in params:
                    if (p.get("name") or "").strip().lower() == "splat_depth_test":
                        p["value"] = "1" if v else "0"
                        found = True
                        break
                if not found:
                    params.append({"name": "splat_depth_test", "value": "1" if v else "0"})
                node.params = params
            except Exception:
                pass

            try:
                win = card.window()
                glv = getattr(win, "gl_view", None) if win is not None else None
                if glv is not None:
                    glv._mgl_splat_depth_test = ("1" if v else "0")
                    glv.update()
            except Exception:
                pass

        chk.toggled.connect(lambda v: QtCore.QTimer.singleShot(0, lambda: _apply_depth(v)))
        layout.addWidget(render_panel)

        # --- Transforms ---
        xform_title = QtWidgets.QLabel("Transforms")
        xform_title.setStyleSheet("color:#94a3b8;font-size:11px;")
        xform_title.setMinimumWidth(0)
        xform_title.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(xform_title)

        xform_panel = QtWidgets.QWidget()
        xform_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        xform_panel.setMinimumWidth(0)
        xform_panel.setStyleSheet(
            "QWidget{background:#0f1216;color:#e2e8f0;border:1px solid #3c4450;border-radius:6px;}"
        )

        fp = QtWidgets.QFormLayout(xform_panel)
        fp.setContentsMargins(6, 6, 6, 6)
        fp.setHorizontalSpacing(6)
        fp.setVerticalSpacing(4)
        fp.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldsStayAtSizeHint)
        fp.setRowWrapPolicy(QtWidgets.QFormLayout.DontWrapRows)

        def _mk_spin():
            sb = QtWidgets.QDoubleSpinBox()
            sb.setDecimals(3)                 # fewer digits = smaller control
            sb.setRange(-1e9, 1e9)
            sb.setSingleStep(0.01)
            sb.setKeyboardTracking(False)

            sb.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
            sb.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

            # keep it compact and stable, no vertical weirdness
            sb.setMinimumHeight(22)
            sb.setFixedWidth(72)              # tweak: try 64, 68, 72
            sb.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

            sb.setStyleSheet(
                "QDoubleSpinBox{background:#12151a;color:#e2e8f0;border:1px solid #3c4450;border-radius:4px;padding:2px 6px;}"
            )
            return sb

        def _xyz_row(default=(0.0, 0.0, 0.0)):
            w = QtWidgets.QWidget()
            w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            l = QtWidgets.QHBoxLayout(w)
            l.setContentsMargins(0, 0, 0, 0)
            l.setSpacing(6)

            a = _mk_spin()
            b = _mk_spin()
            c = _mk_spin()
            a.setValue(float(default[0]))
            b.setValue(float(default[1]))
            c.setValue(float(default[2]))

            # do NOT stretch the spinboxes
            l.addWidget(a)
            l.addWidget(b)
            l.addWidget(c)
            l.addStretch(1)

            return w, (a, b, c)


        pos_w, pos_xyz = _xyz_row((0.0, 0.0, 0.0))
        rot_w, rot_xyz = _xyz_row((0.0, 0.0, 0.0))
        scl_w, scl_xyz = _xyz_row((1.0, 1.0, 1.0))

        fp.addRow("Position", pos_w)
        fp.addRow("Rotation", rot_w)
        fp.addRow("Scale", scl_w)

        card._xform_panel = xform_panel
        card._xform_pos = pos_xyz
        card._xform_rot = rot_xyz
        card._xform_scl = scl_xyz

        xform_panel.setEnabled(False)
        layout.addWidget(xform_panel)

        # --- Selection -> Transforms, and Transforms -> Viewport ---
        card._scene_selected_owner = None
        card._xform_updating = False
        card._scene_outliner_user_selected = False

        def _get_glv():
            win = card.window()
            return getattr(win, "gl_view", None) if win is not None else None

        def _set_xyz(spins, xyz):
            try:
                card._xform_updating = True
                for sb, v in zip(spins, xyz):
                    sb.blockSignals(True)
                    sb.setValue(float(v))
                    sb.blockSignals(False)
            finally:
                card._xform_updating = False

        def _load_xform_from_view(owner: str):
            glv = _get_glv()
            if glv is None:
                return
            # Prefer splat xform when owner is a splat
            getf = getattr(glv, "_mgl_get_scene_asset_xform", None)
            try:
                splat_map = getattr(glv, "_mgl_scene_splats", None)
                if isinstance(splat_map, dict) and owner in splat_map:
                    getf = getattr(glv, "_mgl_get_scene_splat_xform", getf)
            except Exception:
                pass
            if not callable(getf):
                return
            x = getf(owner)
            _set_xyz(card._xform_pos, x.get("pos", (0.0, 0.0, 0.0)))
            _set_xyz(card._xform_rot, x.get("rot", (0.0, 0.0, 0.0)))
            _set_xyz(card._xform_scl, x.get("scl", (1.0, 1.0, 1.0)))
        # expose for external refresh (e.g., gizmo drag)
        card._scene_xform_refresh = _load_xform_from_view

        def _apply_xform(kind: str):
            if card._xform_updating:
                return
            owner = getattr(card, "_scene_selected_owner", None)
            if not owner:
                return
            glv = _get_glv()
            if glv is None:
                return
            setf = getattr(glv, "_mgl_set_scene_asset_xform", None)
            if not callable(setf):
                return

            pos = (card._xform_pos[0].value(), card._xform_pos[1].value(), card._xform_pos[2].value())
            rot = (card._xform_rot[0].value(), card._xform_rot[1].value(), card._xform_rot[2].value())
            scl = (card._xform_scl[0].value(), card._xform_scl[1].value(), card._xform_scl[2].value())

            is_splat = False
            try:
                splat_map = getattr(glv, "_mgl_scene_splats", None)
                if isinstance(splat_map, dict) and owner in splat_map:
                    is_splat = True
            except Exception:
                is_splat = False

            if kind == "pos":
                setf(owner, pos=pos, apply_to_scene_models=not is_splat, use_splat_xform=bool(is_splat))
            elif kind == "rot":
                setf(owner, rot=rot, apply_to_scene_models=not is_splat, use_splat_xform=bool(is_splat))
            elif kind == "scl":
                setf(owner, scl=scl, apply_to_scene_models=not is_splat, use_splat_xform=bool(is_splat))
            else:
                setf(owner, pos=pos, rot=rot, scl=scl, apply_to_scene_models=not is_splat, use_splat_xform=bool(is_splat))
            # Persist updated xform back to the scene node model (for workflow save)
            try:
                win = card.window()
                if win is not None and hasattr(win, "update_scene_asset_xform"):
                    win.update_scene_asset_xform(owner)
            except Exception:
                pass

        def _on_outliner_select():
            it = outliner.currentItem()
            if it is None:
                # Qt can briefly report None during list refresh; don’t clear selection
                return

            owner = it.data(QtCore.Qt.UserRole)
            owner = str(owner) if owner else None
            if not owner:
                return

            card._scene_selected_owner = owner
            card._scene_outliner_user_selected = True
            xform_panel.setEnabled(True)

            # tell viewport which owner is selected (for gizmo draw)
            try:
                win = card.window()
                glv = getattr(win, "gl_view", None) if win is not None else None
                if glv is not None:
                    glv._xform_gizmo_owner = owner

                    # compute a stable gizmo position:
                    # 1) use stored xform pos if it's non-zero
                    # 2) otherwise use bounds center
                    pos = getattr(glv, "_xform_gizmo_pos", (0.0, 0.0, 0.0))
                    try:
                        renderer = getattr(glv, "_mgl_renderer", None) or glv

                        xf = {}
                        get_xf = getattr(renderer, "_mgl_get_scene_asset_xform", None)
                        try:
                            splat_map = getattr(renderer, "_mgl_scene_splats", None)
                            if isinstance(splat_map, dict) and owner in splat_map:
                                get_xf = getattr(renderer, "_mgl_get_scene_splat_xform", get_xf)
                        except Exception:
                            pass
                        if callable(get_xf):
                            xf = get_xf(owner) or {}

                        xf_pos = tuple((xf or {}).get("pos", (0.0, 0.0, 0.0)))
                        if xf_pos != (0.0, 0.0, 0.0):
                            pos = xf_pos
                        else:
                            get_bounds = getattr(renderer, "get_scene_owner_bounds", None)
                            if callable(get_bounds):
                                mins, maxs = get_bounds(owner)
                                pos = (
                                    (float(mins[0]) + float(maxs[0])) * 0.5,
                                    (float(mins[1]) + float(maxs[1])) * 0.5,
                                    (float(mins[2]) + float(maxs[2])) * 0.5,
                                )
                    except Exception:
                        pass

                    if not getattr(glv, "_xform_gizmo_pos_locked", False):
                        glv._xform_gizmo_pos = pos

                    try:
                        glv.update()
                    except Exception:
                        pass
            except Exception:
                pass

            _load_xform_from_view(owner)


        outliner.currentItemChanged.connect(lambda *_: _on_outliner_select())

        # Push edits on commit
        for sb in card._xform_pos:
            sb.editingFinished.connect(lambda k="pos": _apply_xform(k))
        for sb in card._xform_rot:
            sb.editingFinished.connect(lambda k="rot": _apply_xform(k))
        for sb in card._xform_scl:
            sb.editingFinished.connect(lambda k="scl": _apply_xform(k))


        # ---------- Outliner logic ----------
        def _hidden_set() -> set:
            raw2 = getattr(node, "_scene_hidden", None)
            if isinstance(raw2, set):
                return raw2
            if isinstance(raw2, (list, tuple)):
                out = {str(x) for x in raw2 if x}
                setattr(node, "_scene_hidden", out)
                return out
            out = set()
            setattr(node, "_scene_hidden", out)
            return out

        def _apply_visibility(name: str, visible: bool):
            hidden = _hidden_set()
            if visible:
                hidden.discard(name)
            else:
                hidden.add(name)
            # Log visibility changes for debugging (splat/mesh eye toggle)
            try:
                win = card.window()
                glv = getattr(win, "gl_view", None) if win is not None else None
                if glv is not None:
                    glv._mgl_log(
                        "outliner: eye name="
                        + str(name)
                        + " visible="
                        + str(bool(visible))
                        + " hidden_count="
                        + str(len(hidden))
                    )
            except Exception:
                pass
            win = card.window()
            handler = getattr(win, "set_scene_asset_visible", None) if win is not None else None
            if callable(handler):
                handler(name, visible)

        def _apply_rename(scene, old_name: str, new_name: str) -> bool:
            if not new_name or new_name == old_name:
                return False
            ok, msg = scene.rename_node(old_name, new_name)
            if not ok:
                if msg:
                    QtWidgets.QMessageBox.warning(card, "Rename", msg)
                return False
            hidden = _hidden_set()
            if old_name in hidden:
                hidden.remove(old_name)
                hidden.add(new_name)
            win = card.window()
            handler = getattr(win, "rename_scene_asset_owner", None) if win is not None else None
            if callable(handler):
                handler(old_name, new_name)
            return True

        def _refresh(scene_override=None):
            prev_owner = getattr(card, "_scene_selected_owner", None)
            try:
                outliner.blockSignals(True)
            except Exception:
                pass
            outliner.clear()
            scene = scene_override if scene_override is not None else getattr(card, "_graph_scene", None)
            if scene is None:
                empty = QtWidgets.QListWidgetItem("(scene not attached)")
                empty.setFlags(QtCore.Qt.NoItemFlags)
                outliner.addItem(empty)
                try:
                    outliner.blockSignals(False)
                except Exception:
                    pass
                return

            try:
                item = getattr(scene, "_node_items", {}).get(node.name)
            except Exception:
                item = None
            if item is None:
                empty = QtWidgets.QListWidgetItem("(scene node not found)")
                empty.setFlags(QtCore.Qt.NoItemFlags)
                outliner.addItem(empty)
                try:
                    outliner.blockSignals(False)
                except Exception:
                    pass
                return

            try:
                in_edges = list(scene._ordered_in_edges(item))
            except Exception:
                try:
                    in_edges = list(scene._in_edges(item))
                except Exception:
                    in_edges = []

            rows = []
            seen = set()
            for edge in in_edges:
                src_item = getattr(edge, "src", None)
                model = getattr(src_item, "model", None)
                if model is None:
                    continue
                if (getattr(model, "kind", "") or "").strip().lower() != "import":
                    continue
                path = _param_value(model, "path")
                if not path:
                    continue
                ext = Path(path).suffix.lower()
                if ext not in SUPPORTED_EXTS:
                    continue
                name = (getattr(model, "name", "") or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                rows.append({"name": name, "path": path})

            if not rows:
                empty = QtWidgets.QListWidgetItem("(no connected imports)")
                empty.setFlags(QtCore.Qt.NoItemFlags)
                outliner.addItem(empty)
                try:
                    outliner.blockSignals(False)
                except Exception:
                    pass
                return

            hidden = _hidden_set()
            for idx, entry in enumerate(rows, start=1):
                name = entry["name"]
                visible = name not in hidden

                row_widget = QtWidgets.QWidget()
                row_layout = QtWidgets.QHBoxLayout(row_widget)
                row_layout.setContentsMargins(4, 0, 4, 0)
                row_layout.setSpacing(6)

                eye_btn = QtWidgets.QToolButton()
                eye_btn.setAutoRaise(True)
                eye_btn.setCheckable(True)
                # Avoid firing toggled during list rebuild
                try:
                    eye_btn.blockSignals(True)
                    eye_btn.setChecked(visible)
                finally:
                    eye_btn.blockSignals(False)
                eye_btn.setIcon(_eye_icon(visible))
                eye_btn.setToolTip("Toggle visibility")
                def _on_eye_clicked(checked, n=name, b=eye_btn):
                    # Update the icon immediately; defer visibility side-effects to avoid re-entrancy.
                    try:
                        b.setIcon(_eye_icon(checked))
                    except Exception:
                        pass
                    prev_owner = getattr(card, "_scene_selected_owner", None)
                    QtCore.QTimer.singleShot(0, lambda: _apply_visibility(n, checked))
                    # Keep current selection/gizmo stable when clicking the eye
                    if prev_owner:
                        def _restore_selection(owner=prev_owner):
                            try:
                                for i in range(outliner.count()):
                                    it = outliner.item(i)
                                    if it is None:
                                        continue
                                    if (it.data(QtCore.Qt.UserRole) or "") == owner:
                                        outliner.setCurrentRow(i)
                                        return
                            except Exception:
                                pass
                        QtCore.QTimer.singleShot(0, _restore_selection)
                # Use clicked so programmatic setChecked() during refresh doesn't fire visibility changes.
                eye_btn.clicked.connect(_on_eye_clicked)
                row_layout.addWidget(eye_btn, 0)

                idx_label = QtWidgets.QLabel(f"{idx}.")
                idx_label.setStyleSheet("color:#64748b;")
                row_layout.addWidget(idx_label, 0)

                name_edit = QtWidgets.QLineEdit(name)
                name_edit.setReadOnly(True)
                name_edit.setFrame(False)
                name_edit.setMinimumWidth(0)
                name_edit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                name_edit.setStyleSheet("QLineEdit{background:transparent;color:#e2e8f0;}")
                name_edit.setProperty("scene_node_name", name)

                def _start_edit(edit=name_edit):
                    edit.setReadOnly(False)
                    edit.setFrame(True)
                    edit.setStyleSheet(
                        "QLineEdit{background:#12151a;color:#e2e8f0;border:1px solid #3c4450;"
                        "border-radius:4px;padding:2px 6px;}"
                    )
                    edit.selectAll()
                    edit.setFocus(QtCore.Qt.MouseFocusReason)

                def _finish_edit(edit=name_edit, scn=scene):
                    old_name = edit.property("scene_node_name") or ""
                    new_name = edit.text().strip()
                    edit.setReadOnly(True)
                    edit.setFrame(False)
                    edit.setStyleSheet("QLineEdit{background:transparent;color:#e2e8f0;}")
                    if not new_name or new_name == old_name:
                        edit.setText(old_name)
                        return
                    if _apply_rename(scn, old_name, new_name):
                        edit.setProperty("scene_node_name", new_name)
                        _refresh(scn)
                    else:
                        edit.setText(old_name)

                def _on_double_click(ev, edit=name_edit):
                    _start_edit(edit)
                    try:
                        QtWidgets.QLineEdit.mouseDoubleClickEvent(edit, ev)
                    except Exception:
                        pass

                name_edit.mouseDoubleClickEvent = _on_double_click  # type: ignore[assignment]
                name_edit.editingFinished.connect(_finish_edit)
                row_layout.addWidget(name_edit, 1)

                row_item = QtWidgets.QListWidgetItem()
                row_item.setData(QtCore.Qt.UserRole, name)
                if entry.get("path"):
                    row_item.setToolTip(entry["path"])
                row_item.setSizeHint(row_widget.sizeHint())
                outliner.addItem(row_item)
                outliner.setItemWidget(row_item, row_widget)

            # Restore prior selection if possible; otherwise clear selection/gizmo.
            selected_row = None
            if prev_owner and bool(getattr(card, "_scene_outliner_user_selected", False)):
                try:
                    for i in range(outliner.count()):
                        it = outliner.item(i)
                        if it is None:
                            continue
                        if (it.data(QtCore.Qt.UserRole) or "") == prev_owner:
                            selected_row = i
                            break
                except Exception:
                    selected_row = None

            try:
                if selected_row is not None:
                    outliner.setCurrentRow(selected_row)
                else:
                    outliner.setCurrentRow(-1)
                    outliner.clearSelection()
                    card._scene_selected_owner = None
                    card._scene_outliner_user_selected = False
                    try:
                        xform_panel.setEnabled(False)
                    except Exception:
                        pass
                    # Hide gizmo when nothing is selected
                    try:
                        glv = _get_glv()
                        if glv is not None:
                            glv._xform_gizmo_owner = None
                            glv._xform_gizmo_owner_kind = None
                            glv._xform_gizmo_pos_locked = False
                            # When nothing is selected, park the gizmo at world origin.
                            glv._xform_gizmo_pos = (0.0, 0.0, 0.0)
                            glv.update()
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                outliner.blockSignals(False)
            except Exception:
                pass

            # If we restored a selection, sync panels/gizmo now.
            if selected_row is not None:
                _on_outliner_select()

        def _connect(scene):
            if scene is None:
                return
            if getattr(card, "_scene_outliner_connected", False):
                return
            try:
                if hasattr(scene, "linksChanged"):
                    scene.linksChanged.connect(lambda *_: _refresh(scene))
                if hasattr(scene, "paramChanged"):
                    scene.paramChanged.connect(lambda *_: _refresh(scene))
                card._scene_outliner_connected = True
            except Exception:
                pass

        card._scene_outliner_refresh = _refresh
        card._scene_outliner_connect = _connect

        # initial populate
        sc = getattr(card, "_graph_scene", None)
        if sc is not None:
            _connect(sc)
            _refresh(sc)
        else:
            _refresh()

        return True

    except Exception:
        print("[SceneSpec] augment_infocard_footer ERROR", flush=True)
        traceback.print_exc()
        return True


def render_node_body(node_item, y_cursor: int) -> int:
    body = SceneAssemblyWidget(node_item, node_item)
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


SCENE_SPEC = Spec(
    stripe_color="#38bdf8",
    render_node_body=render_node_body,
    augment_infocard_footer=augment_infocard_footer,
)
