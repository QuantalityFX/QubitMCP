"""Pattern preparation and cloth controls. Simulation belongs to physics/viewport."""
import json
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from nodes.core import Spec
from echograph.physics.cloth import ClothSettings, build_cloth_asset
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
        except (ValueError, TypeError):
            raise PatternError("Cloth pins must be a list of vertex indices.")
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
                          "pins": pins, "cache_path": cache_path, "node_model": model, "node_item": node_item}}
    except (OSError, PatternError, ValueError, RuntimeError) as exc:
        model._cloth_build_error = str(exc)
        return None


def build_ports(node_item):
    defaults = {**ClothSettings().to_dict(), "collider_owner": "", "pins": "[]", "cache_path": ""}
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
    QtCore.QTimer.singleShot(0, lambda: node_item._set_param_value(name, str(value), rebuild=False))


class ColliderOwnerEdit(QtWidgets.QLineEdit):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self.node_item = node_item
        self.setAcceptDrops(True)
        self.setText(str(params(node_item.model).get("collider_owner", "")))
        self.setPlaceholderText("Drop collider mesh (or type owner)")
        self.setToolTip("Drop a scene mesh here. Separate multiple mesh owner names with commas. Leave empty to use the collider input.")
        self.editingFinished.connect(self._commit)

    def _commit(self):
        text = self.text().strip()
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


class ClothWidget(QtWidgets.QWidget):
    def __init__(self, node_item):
        super().__init__()
        self.node_item = node_item
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
            ("Start frame", "start_frame", 0, 100000, None, ""),
            ("End frame / bake", "end_frame", 0, 100000, None, ""),
        ]
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
        for title, name in [("Enabled", "enabled"), ("Self collision", "self_collision"), ("Ground plane Y = 0", "ground"), ("Grab cloth with mouse", "interactive")]:
            check = QtWidgets.QCheckBox(title)
            check.setChecked(getattr(settings, name))
            check.toggled.connect(lambda checked, key=name: set_value(node_item, key, int(checked)))
            if name in {"self_collision", "ground"}:
                forms["Collision"].addRow(check)
            else:
                layout.addWidget(check)
        buttons = QtWidgets.QHBoxLayout()
        layout.addLayout(buttons)
        for title, action in [("Play / Pause", "toggle"), ("Step", "step"), ("Reset", "reset")]:
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(lambda checked=False, key=action: request_command(node_item, key))
            buttons.addWidget(button)
        pin_buttons = QtWidgets.QHBoxLayout()
        layout.addLayout(pin_buttons)
        for title, action in [("Pin selected points", "pin"), ("Unpin selected", "unpin"), ("Clear pins", "clear_pins")]:
            button = QtWidgets.QPushButton(title)
            button.setToolTip("Select cloth vertices in the viewport's point mode. Pin changes reset the simulation.")
            button.clicked.connect(lambda checked=False, key=action: request_command(node_item, key))
            pin_buttons.addWidget(button)
        cache_buttons = QtWidgets.QHBoxLayout()
        layout.addLayout(cache_buttons)
        for title, slot in [("Bake…", self._bake), ("Load cache…", self._load_cache), ("Clear cache", self._clear_cache)]:
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(slot)
            cache_buttons.addWidget(button)
        self.status = QtWidgets.QLabel("Connect a saved pattern, then connect cloth to Scene.")
        self.status.setWordWrap(True)
        self.status.setFixedHeight(64)
        self.status.setStyleSheet("color:#94a3b8")
        layout.addWidget(self.status)
        tip = QtWidgets.QLabel("Position the cloth, then Reset. Enable mouse grab to pull it while running. Alt still navigates the camera.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#94a3b8; font-size:10px")
        layout.addWidget(tip)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._status)
        self.timer.start()

    def _status(self):
        text = getattr(self.node_item.model, "_cloth_build_error", "") or getattr(self.node_item.model, "_cloth_status", "")
        if text:
            self.status.setText(str(text))
            self.status.setToolTip(str(text))

    def _bake(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Bake cloth cache", "cloth.cloth.npz", "Cloth cache (*.npz)")
        if path:
            request_command(self.node_item, "bake", path=path)

    def _load_cache(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load cloth cache", "", "Cloth cache (*.npz)")
        if path:
            set_value(self.node_item, "cache_path", path)

    def _clear_cache(self):
        set_value(self.node_item, "cache_path", "")
        request_command(self.node_item, "reset")


def render_node_body(node_item, y_cursor):
    body = ClothWidget(node_item)
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
