from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
from scipy.spatial.transform import Rotation
from PySide6 import QtCore, QtGui, QtWidgets

from nodes.core import Spec
from nodes.mocap_import import spec as mocap
from echograph.rigging.delete_joints import filter_joints
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time, evaluation_cache_token


def removed_names(model):
    try:
        values = json.loads(mocap._param_value(model, "removed_joints") or "[]")
        return set(value for value in values if isinstance(value, str)) if isinstance(values, list) else set()
    except ValueError:
        return set()


def input_context(item, errors, warnings, *, target=False, _depth=0, _visited=None):
    from nodes.anim_retarget import spec as retarget
    scene = item.scene()
    item = getattr(scene, "_node_items", {}).get(item.model.name, item)
    visited = set() if _visited is None else set(_visited)
    if id(item) in visited or _depth > 32:
        errors.append("Delete Joint: input chain contains a cycle or is too deep.")
        return None
    visited.add(id(item))
    upstream, issue = retarget._connected_item_for_port(scene, item, "rig")
    if issue:
        warnings.append(issue)
    if upstream is None:
        errors.append("Delete Joint: connect an FBX Import or Mocap Import to the rig input.")
        return None
    kind = str(upstream.model.kind).strip().lower()
    # Target resolution permits a static FBX; BVH works for either role.
    resolver = retarget._context_for_target_item if target else retarget._context_for_source_item
    context = resolver(upstream, kind, errors, warnings, _depth=_depth + 1, _visited=visited)
    if context is None and not errors:
        errors.append(f"Delete Joint: unsupported input '{kind}'.")
    return context


def filtered_context(context, removed, *, cache_owner=None):
    if not (set(removed) & set(context["skeleton"].joint_names)):
        return dict(context)
    original_clips = list(context.get("clips") or [])
    active = context.get("clip")
    capture = (context.get("rig_context") or {}).get("capture_clip")
    all_clips = list(original_clips)
    for candidate in (active, capture):
        if candidate is not None and not any(candidate is clip for clip in all_clips):
            all_clips.append(candidate)
    input_meshes = context.get("meshes") or []
    key = (evaluation_cache_token(context["skeleton"]), tuple(evaluation_cache_token(clip) for clip in all_clips),
           tuple(id(mesh) for mesh in input_meshes), tuple(sorted(removed)))
    cached = getattr(cache_owner, "_delete_joint_filter_cache", None)
    if cached is not None and cached[0] == key:
        skeleton, converted, meshes = cached[1]
    else:
        skeleton, converted, meshes = filter_joints(context["skeleton"], all_clips, input_meshes, removed)
        if cache_owner is not None:
            # One result per node, with input references to prevent identity reuse.
            cache_owner._delete_joint_filter_cache = (key, (skeleton, converted, meshes),
                                                     (context["skeleton"], all_clips, list(input_meshes)))
    def converted_clip(original):
        return next((converted[i] for i, clip in enumerate(all_clips) if clip is original), None)
    clips = converted[:len(original_clips)]
    clip = converted_clip(active)
    output = dict(context, skeleton=skeleton, clips=clips, clip=clip, meshes=meshes,
                  joint_count=len(skeleton.joints), clip_count=len(clips), mesh_count=len(meshes))
    rig = dict(context.get("rig_context") or {}, skeleton=skeleton, clips=clips, clip=clip, meshes=meshes)
    if capture is not None:
        rig["capture_clip"] = converted_clip(capture)
    output["rig_context"] = rig
    if context.get("preview_asset"):
        output["preview_asset"] = dict(context["preview_asset"], fbx_rig_context=rig)
    # Derived results must never expose the original hierarchy downstream.
    output["bind_result"] = None
    output["animation_result"] = None
    return output


def resolve_context(item, errors, warnings, *, target=False, _depth=0, _visited=None):
    context = input_context(item, errors, warnings, target=target, _depth=_depth, _visited=_visited)
    if context is None or errors:
        return None
    try:
        removed = removed_names(item.model)
        missing = removed - set(context["skeleton"].joint_names)
        if missing:
            warnings.append("Delete Joint: saved joints absent from this input: " + ", ".join(sorted(missing)))
        return filtered_context(context, removed, cache_owner=item.model)
    except (ValueError, KeyError, np.linalg.LinAlgError) as exc:
        errors.append(f"Delete Joint: {exc}")
        return None


def preview_joint_positions(context):
    """Use the same capture/inverse-bind pose choice as the FBX viewport."""
    skeleton = context["skeleton"]
    rig = context.get("rig_context") or {}
    clip = rig.get("capture_clip")
    sample_time = rig.get("capture_sample_time", getattr(clip, "start_time", 0.0))
    points = None
    if clip is None and context.get("source_format") == "fbx":
        try:
            matrices = np.array([joint.inverse_bind_matrix for joint in skeleton.joints]).reshape(-1, 4, 4)
            world = np.linalg.inv(matrices)
            # Imported FBX matrices may use either translation convention.
            candidates = (world[:, :3, 3], world[:, 3, :3])
            candidate = max(candidates, key=lambda values: np.linalg.norm(np.ptp(values, axis=0)))
            if np.isfinite(candidate).all() and np.linalg.norm(np.ptp(candidate, axis=0)) > 1e-6:
                points = candidate
        except (ValueError, np.linalg.LinAlgError):
            pass
    if points is None:
        if clip is None and context.get("source_format") == "bvh":
            clip = context.get("clip")
            sample_time = getattr(clip, "start_time", 0.0)
        matrices = np.array(evaluate_rig_at_time(skeleton, clip, float(sample_time or 0)).global_matrices).reshape(-1, 4, 4)
        points = matrices[:, :3, 3]
    xform = context.get("transform_xform") or {}
    scale = np.array(xform.get("scl", (1, 1, 1)))
    rotation = Rotation.from_euler("xyz", xform.get("rot", (0, 0, 0)), degrees=True)
    return rotation.apply(points * scale) + np.array(xform.get("pos", (0, 0, 0)))


class JointViewport(QtWidgets.QWidget):
    """Orbitable orthographic skeleton viewport with joint picking."""
    picked = QtCore.Signal(str)

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.skeleton = context["skeleton"]
        self.points = preview_joint_positions(context)
        self.center = (self.points.min(axis=0) + self.points.max(axis=0)) / 2
        self.extent = max(float(np.linalg.norm(np.ptp(self.points, axis=0))), 0.01)
        self.yaw, self.pitch, self.zoom = 0.0, 0.0, 1.0
        self.selected = set()
        self.setMinimumSize(420, 420)
        self.setMouseTracking(True)

    def set_context(self, context):
        points = preview_joint_positions(context)
        self.skeleton = context["skeleton"]
        self.points = points
        # Keep the camera stable while editing so surviving joints don't jump.
        self.setToolTip("")
        self.update()

    def projected(self):
        rotation = Rotation.from_euler("yx", [self.yaw, self.pitch], degrees=True).as_matrix()
        points = (self.points - self.center) @ rotation.T
        scale = min(self.width(), self.height()) * 0.82 * self.zoom / self.extent
        return [QtCore.QPointF(self.width() / 2 + p[0] * scale, self.height() / 2 - p[1] * scale) for p in points]

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor("#18212d"))
        points = self.projected()
        painter.setPen(QtGui.QPen(QtGui.QColor("#8394ab"), 2))
        for i, joint in enumerate(self.skeleton.joints):
            if joint.parent_index >= 0:
                painter.drawLine(points[i], points[joint.parent_index])
        for i, joint in enumerate(self.skeleton.joints):
            color = "#ffce56" if joint.name in self.selected else "#6dd5fa"
            painter.setPen(QtGui.QColor(color))
            painter.setBrush(QtGui.QColor(color))
            painter.drawEllipse(points[i], 5, 5)
            if joint.name in self.selected or joint.parent_index < 0:
                painter.drawText(points[i] + QtCore.QPointF(8, -8), joint.name)
        painter.end()

    def mousePressEvent(self, event):
        self.last_pos = event.position()
        if event.button() == QtCore.Qt.LeftButton:
            candidates = [(float((point - event.position()).manhattanLength()), i) for i, point in enumerate(self.projected())]
            distance, index = min(candidates)
            if distance <= 14:
                self.picked.emit(self.skeleton.joints[index].name)

    def mouseMoveEvent(self, event):
        if event.buttons() & (QtCore.Qt.RightButton | QtCore.Qt.MiddleButton):
            delta = event.position() - self.last_pos
            self.yaw += delta.x() * 0.5
            self.pitch += delta.y() * 0.5
            self.last_pos = event.position()
            self.update()
        else:
            candidates = [((point - event.position()).manhattanLength(), i) for i, point in enumerate(self.projected())]
            distance, index = min(candidates)
            self.setToolTip(self.skeleton.joints[index].name if distance <= 14 else "")

    def wheelEvent(self, event):
        self.zoom = min(8, max(0.2, self.zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15)))
        self.update()


class JointDialog(QtWidgets.QDialog):
    def __init__(self, item, context, parent=None):
        super().__init__(parent)
        self.item, self.context = item, context
        self.setWindowTitle("Delete Joint — select joints")
        self.resize(850, 620)
        layout = QtWidgets.QVBoxLayout(self)
        help_text = QtWidgets.QLabel("Click joints to select multiple. Right-drag to orbit; wheel to zoom.\nThe viewport shows the output skeleton. Removed joints remain red in the list for restoration.\nDeleted joints lose their skin influences; remaining weights are normalized.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        row = QtWidgets.QHBoxLayout()
        self.viewport = JointViewport(context)
        row.addWidget(self.viewport, 1)
        self.joints = QtWidgets.QListWidget()
        self.joints.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.joints.addItems(context["skeleton"].joint_names)
        row.addWidget(self.joints)
        layout.addLayout(row, 1)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QtWidgets.QHBoxLayout()
        for label, callback in (("Delete selected", self.delete_selected), ("Restore selected", self.restore_selected), ("Restore all", lambda: self.apply(set())), ("Close", self.accept)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.viewport.picked.connect(self.pick)
        self.joints.itemSelectionChanged.connect(self.selection_changed)
        self.refresh()

    def pick(self, name):
        for i in range(self.joints.count()):
            row = self.joints.item(i)
            if row.text() == name:
                row.setSelected(not row.isSelected())
                self.joints.scrollToItem(row)
                break

    def selection_changed(self):
        self.viewport.selected = {row.text() for row in self.joints.selectedItems()}
        self.viewport.update()

    def refresh(self, output=None):
        removed = removed_names(self.item.model)
        try:
            self.viewport.set_context(output if output is not None else filtered_context(self.context, removed))
        except (ValueError, np.linalg.LinAlgError) as exc:
            self.status.setText(str(exc))
            return
        for i in range(self.joints.count()):
            row = self.joints.item(i)
            row.setForeground(QtGui.QColor("#ec6868") if row.text() in removed else self.palette().text())
            font = row.font()
            font.setStrikeOut(row.text() in removed)
            row.setFont(font)
        self.viewport.update()
        self.status.setText(f"{len(removed)} joint(s) removed. Original files are unchanged.")

    def apply(self, removed):
        try:
            output = filtered_context(self.context, removed)
        except (ValueError, np.linalg.LinAlgError) as exc:
            self.status.setText(str(exc))
            return
        mocap._set_param_value(self.item.model, "removed_joints", json.dumps(sorted(removed)))
        scene = self.item.scene()
        if scene is not None:
            scene.set_node_params(self.item.model.name, list(self.item.model.params), rebuild=False, emit=True)
        self.refresh(output)

    def delete_selected(self):
        self.apply(removed_names(self.item.model) | self.viewport.selected)

    def restore_selected(self):
        self.apply(removed_names(self.item.model) - self.viewport.selected)


def open_selector(item):
    scene = item.scene()
    views = scene.views() if scene is not None else []
    parent = views[0].window() if views else None
    errors, warnings = [], []
    context = input_context(item, errors, warnings, target=True)
    if context is None or errors:
        QtWidgets.QMessageBox.warning(parent, "Delete Joint", "\n".join(errors))
        return
    dialog = JointDialog(item, context, parent)
    dialog.exec()


def view_result(item):
    """Display the filtered rig in the application's main 3D viewport."""
    from nodes.anim_retarget import spec as retarget
    scene = item.scene()
    views = scene.views() if scene is not None else []
    window = views[0].window() if views else None
    errors, warnings = [], []
    context = resolve_context(item, errors, warnings, target=True)
    if context is None or errors:
        QtWidgets.QMessageBox.warning(window, "Delete Joint", "\n".join(errors) or "No rig is available.")
        return False
    asset = retarget._preview_asset_for_context(
        context, owner=item.model.name,
        role="target" if context.get("source_format") == "fbx" else "source",
        x_offset=0.0, curve_thickness=2.0,
    )
    if asset is None:
        QtWidgets.QMessageBox.warning(window, "Delete Joint", "The filtered rig preview could not be built. Check the input file.")
        return False
    asset["fbx_rig_context"].pop("retarget_preview_role", None)
    handler = getattr(window, "open_scene_assets", None)
    if not callable(handler):
        QtWidgets.QMessageBox.warning(window, "Delete Joint", "The main 3D viewport is not available.")
        return False
    handler([asset], frame=True)
    return True


def build_ports(item):
    mocap._ensure_param(item, "rig", "")
    mocap._ensure_param(item, "removed_joints", "[]")
    mocap._ensure_hidden_params(item.model, ["removed_joints"])
    item.ensure_input("rig")


class JointControls(QtWidgets.QWidget):
    def __init__(self, item):
        super().__init__()
        self.item = item
        layout = QtWidgets.QVBoxLayout(self)
        button = QtWidgets.QPushButton("Select joints…")
        button.clicked.connect(self.open)
        layout.addWidget(button)
        self.view_button = QtWidgets.QPushButton("View")
        self.view_button.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: white; border: 1px solid #3b82f6; border-radius: 4px; padding: 6px; }"
            "QPushButton:hover { background-color: #3b82f6; }"
            "QPushButton:pressed { background-color: #1d4ed8; }"
        )
        self.view_button.setToolTip("Show the skeleton with deleted joints removed in the main 3D viewport.")
        self.view_button.clicked.connect(lambda: view_result(self.item))
        layout.addWidget(self.view_button)
        self.label = QtWidgets.QLabel()
        layout.addWidget(self.label)
        self.refresh()
        scene = item.scene() or getattr(item.model, "_graph_scene", None)
        if scene is not None and hasattr(scene, "paramChanged"):
            scene.paramChanged.connect(self.refresh)

    def refresh(self, *args):
        self.label.setText(f"{len(removed_names(self.item.model))} joint(s) removed")

    def open(self):
        open_selector(self.item)
        self.refresh()


def render_node_body(item, y_cursor):
    widget = JointControls(item)
    widget.ensurePolished()
    item.prepareGeometryChange()
    item.width = max(item.width, widget.minimumSizeHint().width() + 16)
    widget.setFixedWidth(int(item.width - 16))
    height = max(widget.sizeHint().height(), widget.minimumSizeHint().height())
    widget.setFixedHeight(height)
    proxy = QtWidgets.QGraphicsProxyWidget(item)
    proxy.setWidget(widget)
    proxy.setPos(8, y_cursor)
    proxy.resize(item.width - 16, height)
    item._plugin_proxies.append(proxy)
    item.height = y_cursor + height + 10
    return item.height


def augment_infocard_footer(card, layout):
    model = getattr(card, "_node_ref", None)
    if model is None or model.kind.lower() != "delete_joint":
        return False
    scene = getattr(card, "_graph_scene", None)
    layout.addWidget(JointControls(SimpleNamespace(model=model, scene=lambda: scene)))
    return True


DELETE_JOINT_SPEC = Spec(stripe_color="#ef6464", build_ports=build_ports, render_node_body=render_node_body,
                         augment_infocard_footer=augment_infocard_footer)
