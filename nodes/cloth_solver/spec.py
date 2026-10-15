"""Pattern preparation and cloth controls. Simulation belongs to physics/viewport."""
import json
import math
import re
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from nodes.core import Spec
from echograph.physics.cloth import ClothSettings, build_cloth_asset
from echograph.physics.cloth.placement import validate_placements
from echograph.patterns.document import PatternError

CLOTH_NODE_W = 350
CLOTH_BODY_H = 480


def params(model):
    return {p.get("name"): p.get("value", "") for p in model.params}


def input_item(node_item, name):
    scene = node_item.scene()
    if scene is None:
        return None
    ordered = getattr(scene, "_ordered_in_edges", None) or getattr(scene, "_in_edges", None)
    for edge in ordered(node_item) if ordered else []:
        port = str(getattr(edge, "dst_port_name", "") or getattr(edge, "dst_label", "") or getattr(edge, "dst_name", "")).lower()
        if port == name:
            return getattr(edge, "src", None)
    return None


def build_cloth_scene_asset(node_item):
    model = node_item.model
    try:
        source = input_item(node_item, "pattern")
        if source is None or str(source.model.kind).lower() != "pattern_design":
            raise PatternError("Connect a saved Pattern Design to the pattern input.")
        from nodes.pattern_design.spec import resolve_pattern_document, _window
        resolved = resolve_pattern_document(source)
        if not resolved["ready"]:
            raise PatternError("The pattern has unfinished panels. Close and save them first.")
        values = params(model)
        settings = ClothSettings.from_dict(values)
        asset = build_cloth_asset(resolved["document"], settings.mesh_spacing_mm)
        colliders = []
        collider = input_item(node_item, "collider")
        if collider is not None:
            colliders = list(collider._collect_scene_assets())
            colliders = [a for a in colliders if a.get("path") and str(a.get("ext") or Path(a["path"]).suffix).lower()
                         in {".obj", ".fbx", ".gltf", ".glb", ".ply", ".stl"}]
            if not colliders:
                raise PatternError("The collider input needs a mesh or an animated mesh.")
        try:
            pins = json.loads(str(values.get("pins", "[]")))
            if not isinstance(pins, list) or any(type(p) is not int for p in pins):
                raise ValueError()
            if any(p < 0 or p >= len(asset.positions) for p in pins):
                raise ValueError()
        except (ValueError, TypeError):
            raise PatternError("Cloth pins must be a list of vertex indices.")
        pin_positions = json.loads(str(values.get("pin_positions", "{}")))
        if not isinstance(pin_positions, dict):
            raise PatternError("Cloth pin positions must be a map of vertex IDs to 3D points.")
        for key, point in pin_positions.items():
            if (not key.isdigit() or not 0 <= int(key) < len(asset.positions)
                    or not isinstance(point, list) or len(point) != 3
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in point)):
                raise PatternError("Invalid cloth pin position.")
        source_placements = validate_placements(json.loads(str(params(source.model).get("piece_transforms", "{}"))))
        placements = {**source_placements, **validate_placements(json.loads(str(values.get("piece_transforms", "{}"))))}
        cache_path = str(values.get("cache_path", "") or "").strip()
        if cache_path and not Path(cache_path).is_absolute():
            window = _window(node_item)
            workflow = getattr(window, "_current_path", None)
            cache_path = str((Path(workflow).parent if workflow else Path.cwd()) / cache_path)
        model._cloth_build_error = ""
        return {"node": model.name, "path": str(asset.rest_obj()), "ext": ".obj", "kind": "mesh",
                "source_kind": "cloth_solver", "texture": "",
                "cloth": {"asset": asset, "settings": settings.to_dict(), "solver_node": model.name,
                          "pattern_owner": source.model.name, "pattern_path": resolved["path"],
                          "collider_owner": str(values.get("collider_owner", "")), "colliders": colliders,
                          "pins": pins, "pin_positions": pin_positions, "piece_transforms": placements, "cache_path": cache_path,
                          "node_model": model, "node_item": node_item},
                "pattern_pieces": [{"id": p["id"], "name": p["name"]} for p in asset.pieces()]}
    except (OSError, PatternError, ValueError, TypeError, RuntimeError) as exc:
        model._cloth_build_error = str(exc)
        return None


def build_ports(node_item):
    defaults = {**ClothSettings().to_dict(), "collider_owner": "", "pins": "[]", "cache_path": "",
                "piece_transforms": "{}", "pin_positions": "{}"}
    existing = params(node_item.model)
    for name, value in defaults.items():
        if name not in existing:
            node_item.model.params.append({"name": name, "value": str(int(value)) if isinstance(value, bool) else str(value)})
    hidden = next((p for p in node_item.model.params if p.get("name") == "__ui_hidden_params"), None)
    if hidden is None:
        hidden = {"name": "__ui_hidden_params", "value": ""}
        node_item.model.params.append(hidden)
    hidden["value"] = ",".join(sorted({n for n in str(hidden["value"]).split(",") if n} | set(defaults)))
    node_item.model._named_inputs = ["pattern", "collider"]
    node_item._default_named_input = "pattern"
    node_item._show_default_input_with_named = False
    node_item.ensure_input("pattern")
    node_item.ensure_input("collider")
    node_item.ensure_output("cloth")
    node_item._hide_default_output_with_named = True


def set_value(node_item, name, value):
    # A Scene refresh can delete the originating outliner edit. Always finish
    # its event before emitting graph notifications and rebuilding widgets.
    def apply():
        if isValid(node_item):
            node_item._set_param_value(name, str(value), rebuild=False)
    QtCore.QTimer.singleShot(0, apply)


def collider_owner_slots(values, colliders=()):
    raw = str(values.get("collider_owner", "") or "")
    if raw:
        return [owner.strip() for owner in raw.split(",")]
    return list(dict.fromkeys(str(c.get("node") or "").strip() for c in colliders
                             if str(c.get("node") or "").strip())) or [""]


class ColliderOwnerEdit(QtWidgets.QLineEdit):
    def __init__(self, node_item, parent=None, *, slot_index=None, colliders=()):
        super().__init__(parent)
        self.node_item = node_item
        self.slot_index = slot_index
        self.colliders = colliders
        self.setAcceptDrops(True)
        values = params(node_item.model)
        slots = collider_owner_slots(values, colliders)
        self.setText(str(values.get("collider_owner", "")) if slot_index is None else
                     slots[slot_index] if slot_index < len(slots) else "")
        self.setPlaceholderText("Drop collider mesh (or type owner)")
        self.setToolTip("Drop a scene mesh or a cloth parent here." if slot_index is not None else
                        "Drop scene meshes here. Separate multiple owner names with commas. Leave empty to use the collider input.")
        self.editingFinished.connect(self._commit)

    def _commit(self):
        text = self.text().strip()
        if self.slot_index is not None:
            slots = collider_owner_slots(params(self.node_item.model), self.colliders)
            while len(slots) <= self.slot_index:
                slots.append("")
            slots[self.slot_index] = text
            text = ",".join(slots)
        if text != str(params(self.node_item.model).get("collider_owner", "")):
            set_value(self.node_item, "collider_owner", text)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        text = event.mimeData().text().strip()
        from echograph.ui.scene_outliner import ROW_MIME
        if event.mimeData().hasFormat(ROW_MIME):
            try:
                row = json.loads(bytes(event.mimeData().data(ROW_MIME)))
                if row.get("kind") == "pattern_piece" and row.get("parent"):
                    text = str(row["parent"])
            except (ValueError, TypeError, AttributeError):
                pass
        if text:
            # Scene outliner drags carry owner names; ignore skeleton child suffixes.
            self.setText(text.split("/Skeleton", 1)[0])
            event.acceptProposedAction()
            self._commit()


def request_command(node_item, action, **kwargs):
    queue = getattr(node_item.model, "_cloth_commands", None)
    if queue is None:
        queue = node_item.model._cloth_commands = []
    queue.append({"action": action, **kwargs})
    for widget in QtWidgets.QApplication.allWidgets():
        if getattr(widget, "_use_moderngl", False) and hasattr(widget, "_mgl_update_cloth"):
            widget.update()


def _open_cache_dialog(node_item, save, action="bake"):
    """Keep file pickers outside the replaceable graphics proxy widget."""
    if not isValid(node_item) or node_item.scene() is None:
        return
    existing = getattr(node_item, "_cloth_cache_dialog", None)
    if existing is not None and isValid(existing):
        existing.raise_()
        existing.activateWindow()
        return
    views = node_item.scene().views()
    parent = views[0].window() if views else QtWidgets.QApplication.activeWindow()
    if parent is not None and parent.graphicsProxyWidget() is not None:
        parent = None
    dialog = QtWidgets.QFileDialog(parent)
    # Embedded graphics controls and native Windows modal dialogs can lose
    # ownership/focus during graph refreshes. Use Qt's asynchronous browser.
    dialog.setOption(QtWidgets.QFileDialog.DontUseNativeDialog)
    dialog.setWindowTitle(("Bake cloth cache" if action == "bake" else "Save cloth state") if save else "Load cloth cache")
    dialog.setNameFilter("Cloth cache (*.npz)")
    dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptSave if save else QtWidgets.QFileDialog.AcceptOpen)
    dialog.setFileMode(QtWidgets.QFileDialog.AnyFile if save else QtWidgets.QFileDialog.ExistingFile)
    if save:
        workflow = getattr(parent, "_current_path", None) or getattr(node_item.scene(), "_filename", None)
        if workflow:
            try:
                directory = Path(workflow).expanduser()
                if not directory.is_dir():
                    directory = directory.parent
                if directory.is_dir():
                    dialog.setDirectory(str(directory.resolve()))
            except (OSError, ValueError):
                pass
        dialog.setDefaultSuffix("npz")
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(node_item.model.name)).strip(" .") or "cloth"
        dialog.selectFile(f"{name}.cloth.npz")
    dialog.setWindowModality(QtCore.Qt.WindowModal)
    node_item._cloth_cache_dialog = dialog

    def selected():
        if not isValid(node_item) or node_item.scene() is None:
            return
        paths = dialog.selectedFiles()
        if paths and paths[0]:
            if save:
                request_command(node_item, action, path=paths[0])
            else:
                set_value(node_item, "cache_path", paths[0])
                request_command(node_item, "load_cache", path=paths[0])

    def finished(_result):
        if getattr(node_item, "_cloth_cache_dialog", None) is dialog:
            node_item._cloth_cache_dialog = None
        dialog.deleteLater()

    dialog.accepted.connect(selected)
    dialog.finished.connect(finished)
    node_item.destroyed.connect(dialog.reject)
    # No nested event loop inside a proxy button signal. The node item keeps
    # the dialog alive even when a graph refresh replaces the originating body.
    dialog.open()


class ClothWidget(QtWidgets.QWidget):
    def __init__(self, node_item):
        super().__init__()
        self.node_item = node_item
        icon_dir = Path(__file__).resolve().parents[2] / "icons"
        self._play_icon = QtGui.QIcon(str(icon_dir / "PlayButton_icon.png"))
        self._stop_icon = QtGui.QIcon(str(icon_dir / "StopButton_icon.png"))
        self.setObjectName("ClothSolverBody")
        self.setStyleSheet(
            "QWidget#ClothSolverBody{background:#141c29;}"
            "QWidget#ClothSettingsPage{background:#111827;}"
            "QLabel,QCheckBox{color:#cbd5e1;}"
            "QTabWidget::pane{border:1px solid #334155;background:#111827;border-radius:4px;}"
            "QTabBar::tab{background:#1e293b;color:#94a3b8;padding:5px 10px;}"
            "QTabBar::tab:selected{background:#334155;color:#e2e8f0;}"
            "QAbstractSpinBox,QComboBox,QLineEdit{background:#0f172a;color:#e2e8f0;"
            "border:1px solid #334155;border-radius:3px;padding:2px;}"
            "QPushButton{background:#1e293b;color:#e2e8f0;border:1px solid #334155;"
            "border-radius:4px;padding:4px;}"
            "QPushButton:hover{background:#334155;}"
            "QPushButton#ClothBakeStopButton:enabled{background:#b91c1c;color:#fee2e2;border-color:#ef4444;}"
            "QPushButton#ClothBakeStopButton:enabled:hover{background:#dc2626;}"
            "QPushButton#ClothBakeStopButton:enabled:pressed{background:#991b1b;}"
        )
        values = params(node_item.model)
        settings = ClothSettings.from_dict(values)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 8)
        layout.setSpacing(6)
        label = QtWidgets.QLabel("Pattern → Cloth Solver → Scene")
        label.setStyleSheet("color:#70d5cd; font-weight:600")
        layout.addWidget(label)
        tabs = QtWidgets.QTabWidget()
        forms, pages = {}, {}
        for title in ("Fabric", "Simulation", "Collision"):
            page = QtWidgets.QWidget()
            page.setObjectName("ClothSettingsPage")
            form = QtWidgets.QFormLayout(page)
            form.setSpacing(4)
            form.setContentsMargins(8, 8, 8, 8)
            form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
            forms[title], pages[title] = form, page
            tabs.addTab(page, title)
        layout.addWidget(tabs)
        gravity = QtWidgets.QCheckBox("Gravity")
        gravity.setObjectName("Cloth_gravity_enabled")
        gravity.setChecked(settings.gravity_enabled)
        gravity.setToolTip("Enable gravity without changing its stored strength.")
        gravity.toggled.connect(lambda checked: set_value(node_item, "gravity_enabled", int(checked)))
        forms["Simulation"].addRow(gravity)
        controls = [
            ("Stretch stiffness", "stretch", 0, 1000000, 2, " N/m"),
            ("Bend stiffness", "bend", 0, 10000, 4, ""),
            ("Sew stiffness", "seam_stiffness", 0, 1000000, 2, " N/m"),
            ("Sew closure", "seam_seconds", 0, 60, 2, " s"),
            ("Seam gap", "seam_gap", 0, 0.1, 4, " m"),
            ("Gravity Y", "gravity_y", -1000, 1000, 2, " m/s²"),
            ("Damping", "damping", 0, 100, 2, " /s"),
            ("Surface density", "density", 0.001, 100, 3, " kg/m²"),
            ("Collision thickness", "thickness", 0.0001, 0.1, 4, " m"),
            ("Friction", "friction", 0, 1, 2, ""),
            ("Mesh spacing", "mesh_spacing_mm", 2, 100, 1, " mm"),
            ("Scene units / meter", "scene_units_per_meter", 0.001, 10000, 3, ""),
            ("Substeps", "substeps", 1, 64, None, ""),
            ("Constraint iterations", "iterations", 1, 64, None, ""),
            ("Collision quality", "collision_quality", 1, 8, None, ""),
            ("FPS", "fps", 1, 240, 2, ""),
            ("Start frame / bake", "start_frame", 0, 100000, None, ""),
            ("End frame / bake", "end_frame", 0, 100000, None, ""),
        ]
        hints = {
            "substeps": "More substeps reduce motion per solve step. Try 8 for dressing, or 16 for fast body motion.",
            "collision_quality": "More passes resolve point, edge and face contacts around folds and tight body features. Try 4; higher values take longer.",
            "thickness": "Surface clearance in meters, also used for self collision. 0.003 m is 3 mm; try 0.003–0.005 m for body clearance.",
            "friction": "Contact friction: 0 is slippery. Resists sliding where the fabric presses against a collider.",
            "density": "Fabric mass per square meter. 0.2 kg/m² is 200 g/m²; gravity acceleration does not depend on density.",
            "gravity_y": "Earth gravity is −9.81 m/s². Use Scene units / meter to set scale instead of reducing gravity.",
            "scene_units_per_meter": "Default: 1 scene unit = 1 meter (the primitive cube is 1 m wide). Use 100 for centimeter scenes.",
            "seam_seconds": "Increase the closure time to pull sewn edges together more gently. Try 1–2 seconds when sewing around a body.",
            "fps": "Frame rate for real-time simulation and baking. Timeline simulation uses the viewport timeline frame rate.",
        }
        for title, name, low, high, decimals, suffix in controls:
            group = ("Simulation" if name in {"gravity_y", "substeps", "iterations", "fps", "start_frame", "end_frame"}
                     else "Collision" if name in {"thickness", "friction", "mesh_spacing_mm", "scene_units_per_meter", "collision_quality"}
                     else "Fabric")
            spin = QtWidgets.QSpinBox() if decimals is None else QtWidgets.QDoubleSpinBox()
            spin.setRange(low, high)
            if decimals is not None:
                spin.setDecimals(decimals)
                spin.setSingleStep(0.01 if high <= 1 else 1)
            spin.setSuffix(suffix)
            help_text = hints.get(name)
            if help_text:
                spin.setToolTip(help_text)
            spin.setKeyboardTracking(False)
            spin.setValue(getattr(settings, name))
            spin.valueChanged.connect(lambda value, key=name: set_value(node_item, key, value))
            forms[group].addRow(title, spin)
        device = QtWidgets.QComboBox()
        device.addItems(["auto", "cpu", "cuda"])
        device.setCurrentText(settings.device)
        device.currentTextChanged.connect(lambda value: set_value(node_item, "device", value))
        forms["Simulation"].addRow("Backend", device)
        forms["Collision"].addRow("Collider owner", ColliderOwnerEdit(node_item))
        for title, name in [("Enabled", "enabled"), ("Self collision", "self_collision"), ("Ground plane Y = 0", "ground"),
                            ("Simulate with timeline", "follow_timeline"), ("Grab cloth with mouse", "interactive")]:
            check = QtWidgets.QCheckBox(title)
            check.setObjectName(f"Cloth_{name}")
            check.setChecked(getattr(settings, name))
            if name == "follow_timeline":
                check.setToolTip("Play or scrub to simulate from the current or loaded pose. Calculated frames stay in memory until the collider animation changes.")
            check.toggled.connect(lambda checked, key=name: set_value(node_item, key, int(checked)))
            if name in {"self_collision", "ground"}:
                forms["Collision"].addRow(check)
            else:
                layout.addWidget(check)
        buttons = QtWidgets.QHBoxLayout()
        layout.addLayout(buttons)
        for title, action in [("Play", "toggle"), ("Step", "step"), ("Reset", "reset")]:
            button = QtWidgets.QPushButton(title)
            if action == "toggle":
                self.play_button = button
                button.setObjectName("ClothRealtimePlayButton")
                button.setIconSize(QtCore.QSize(18, 18))
                button.setMinimumWidth(80)
            elif action == "step":
                button.setToolTip("Advance one simulation step, or one animation frame when timeline simulation is enabled.")
            elif action == "reset":
                button.setToolTip("Clear temporary playback and timeline history. Keep the saved cache path and restore its state, or the arranged pattern if none is loaded.")
            button.clicked.connect(lambda checked=False, key=action: request_command(node_item, key))
            buttons.addWidget(button)
        pin_buttons = QtWidgets.QHBoxLayout()
        layout.addLayout(pin_buttons)
        for title, action in [("Pin selected points", "pin"), ("Unpin selected", "unpin"), ("Clear pins", "clear_pins")]:
            button = QtWidgets.QPushButton(title)
            button.setToolTip("Select cloth vertices in the viewport's point mode. Pin changes retain the current pose and recalculate subsequent frames.")
            button.clicked.connect(lambda checked=False, key=action: request_command(node_item, key))
            pin_buttons.addWidget(button)
        cache_buttons = QtWidgets.QHBoxLayout()
        layout.addLayout(cache_buttons)
        for title, slot in [("Bake…", self._bake), ("Load cache…", self._load_cache), ("Clear cache", self._clear_cache)]:
            button = QtWidgets.QPushButton(title)
            button.setToolTip("Bake the Start–End range from the arranged pattern. Stop & save keeps completed frames."
                              if title == "Bake…" else "Load a saved pose or animation cache." if title == "Load cache…"
                              else "Unload the cache and return to the arranged pattern.")
            button.clicked.connect(slot)
            cache_buttons.addWidget(button)
        save_buttons = QtWidgets.QHBoxLayout()
        layout.addLayout(save_buttons)
        save_state = QtWidgets.QPushButton("Save state…")
        save_state.setToolTip("Save the cloth pose, velocity and sewing progress. Load it later to continue at the current timeline frame.")
        save_state.clicked.connect(self._save_state)
        save_buttons.addWidget(save_state)
        self.stop_bake = QtWidgets.QPushButton("Stop & save")
        self.stop_bake.setObjectName("ClothBakeStopButton")
        self.stop_bake.setToolTip("Stop baking after the current solve and save every completed frame.")
        self.stop_bake.clicked.connect(lambda: request_command(node_item, "stop"))
        self.stop_bake.setEnabled(bool(getattr(node_item.model, "_cloth_baking", False)))
        save_buttons.addWidget(self.stop_bake)
        self.cache_file = QtWidgets.QLineEdit(str(params(node_item.model).get("cache_path", "")))
        self.cache_file.setReadOnly(True)
        self.cache_file.setPlaceholderText("No cache loaded")
        self.cache_file.setToolTip("Current cloth cache. Use Load cache to browse for a saved pose or animation.")
        layout.addWidget(self.cache_file)
        self.status = QtWidgets.QLabel("Connect a saved pattern, then connect cloth to Scene.")
        self.status.setWordWrap(True)
        self.status.setFixedHeight(64)
        self.status.setStyleSheet("color:#94a3b8")
        layout.addWidget(self.status)
        self.seek_progress = QtWidgets.QProgressBar()
        self.seek_progress.setObjectName("ClothTimelineProgress")
        self.seek_progress.setFixedHeight(18)
        self.seek_progress.setRange(0, 1)
        self.seek_progress.setValue(0)
        self.seek_progress.setFormat("Timeline cache idle")
        self.seek_progress.setToolTip("Temporary memory cache. Changing simulation settings or collider animation clears calculated frames and recalculates from the starting pose.")
        layout.addWidget(self.seek_progress)
        tip = QtWidgets.QLabel("Arrange pieces, then Reset. Play settles cloth. Simulate with timeline lets you play or scrub the animation. Alt navigates the camera.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#94a3b8; font-size:10px")
        layout.addWidget(tip)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._status)
        self.timer.start()
        self._status()

    def _status(self):
        model = self.node_item.model
        progress = getattr(model, "_cloth_seek_progress", None)
        if progress is not None:
            start, frame, target = progress
            self.seek_progress.setRange(0, max(1, target - start))
            self.seek_progress.setValue(max(0, min(frame - start, target - start)))
            self.seek_progress.setFormat(f"Calculating frame {min(frame + 1, target)} / {target} · %p%")
        else:
            self.seek_progress.setRange(0, 1)
            self.seek_progress.setValue(1 if "Preview cache" in str(getattr(model, "_cloth_status", "")) else 0)
            self.seek_progress.setFormat("Frame cached" if self.seek_progress.value() else "Timeline cache idle")
        baking = bool(getattr(model, "_cloth_baking", False))
        values = params(model)
        settings = ClothSettings.from_dict(values)
        running = bool(getattr(model, "_cloth_playing", False)) or baking
        timeline_check = self.findChild(QtWidgets.QCheckBox, "Cloth_follow_timeline")
        if timeline_check is not None and timeline_check.isChecked() != settings.follow_timeline:
            blocker = QtCore.QSignalBlocker(timeline_check)
            timeline_check.setChecked(settings.follow_timeline)
            del blocker
        self.play_button.setText("Stop" if running else "Play")
        self.play_button.setIcon(self._stop_icon if running else self._play_icon)
        self.play_button.setEnabled(baking or (settings.enabled and not settings.follow_timeline
                                              and not getattr(model, "_cloth_saving", False)))
        self.play_button.setToolTip("Use the viewport timeline controls to play or pause the simulation."
                                    if settings.follow_timeline and not baking else
                                    "Stop and save completed bake frames." if baking else
                                    "Stop real-time simulation and keep the current pose." if running else
                                    "Play real-time simulation to settle the cloth.")
        self.stop_bake.setEnabled(baking)
        path = str(values.get("cache_path", ""))
        if self.cache_file.text() != path:
            self.cache_file.setText(path)
            self.cache_file.setToolTip(path or "No cache loaded")
        text = getattr(self.node_item.model, "_cloth_build_error", "") or getattr(self.node_item.model, "_cloth_status", "")
        if text:
            self.status.setText(str(text))
            self.status.setToolTip(str(text))

    def _bake(self):
        node_item = self.node_item
        QtCore.QTimer.singleShot(0, lambda: _open_cache_dialog(node_item, save=True))

    def _load_cache(self):
        node_item = self.node_item
        request_command(node_item, "pause")
        QtCore.QTimer.singleShot(0, lambda: _open_cache_dialog(node_item, save=False))

    def _save_state(self):
        node_item = self.node_item
        request_command(node_item, "pause")
        QtCore.QTimer.singleShot(0, lambda: _open_cache_dialog(node_item, save=True, action="save"))

    def _clear_cache(self):
        set_value(self.node_item, "cache_path", "")
        request_command(self.node_item, "reset")


def render_node_body(node_item, y_cursor):
    body = ClothWidget(node_item)
    body.ensurePolished()
    node_item.prepareGeometryChange()
    node_item.width = max(node_item.width, body.minimumSizeHint().width())
    body.setFixedWidth(int(node_item.width))
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setPos(0, y_cursor)
    proxy.setZValue(node_item.zValue() + 0.1)
    height = max(body.sizeHint().height(), body.minimumSizeHint().height())
    proxy.resize(node_item.width, height)
    node_item._plugin_proxies.append(proxy)
    node_item._cloth_min_h = height
    node_item.height = max(node_item.height, int(y_cursor + height + 12))
    return int(y_cursor + height)


CLOTH_SOLVER_SPEC = Spec(stripe_color="#32b9b0", build_ports=build_ports, render_node_body=render_node_body)
