from __future__ import annotations

import json
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from nodes.core import Spec
from .controller import DEFAULTS, manager_for, notify, output_directory_for, put, resolve_path, session_for, value
from .port_item import PortDot


def build_ports(item):
    existing = {entry.get("name") for entry in item.model.params}
    for key, default in DEFAULTS.items():
        if key not in existing:
            item.model.params.append({"name": key, "value": default})
    hidden = set(value(item.model, "__ui_hidden_params").split(",")) | set(DEFAULTS)
    put(item.model, __ui_hidden_params=",".join(sorted(hidden - {""})))
    for port in ("prompt", "negative_prompt", "image", "reference_image"):
        item.ensure_input(port)
    for port in ("image", "image_path", "seed_used", "status", "job_id", "metadata"):
        item.ensure_output(port)
    from .setup_ui import schedule_setup_offer
    schedule_setup_offer(item)


class ImagePreview(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(230)
        self.pixmap = QtGui.QPixmap()
        self.path = ""
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Double-click to open the generated PNG at full size.")

    def load(self, path):
        if path != self.path:
            self.path = path
            self.pixmap = QtGui.QPixmap(path) if path else QtGui.QPixmap()
            self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.fillRect(rect, QtGui.QColor("#17202f"))
        if self.pixmap.isNull():
            painter.setPen(QtGui.QColor("#a5b4c7"))
            painter.drawText(rect, QtCore.Qt.AlignCenter, "Generated image preview")
            return
        size = self.pixmap.size().scaled(rect.size(), QtCore.Qt.KeepAspectRatio)
        target = QtCore.QRect(QtCore.QPoint(), size)
        target.moveCenter(rect.center())
        painter.save()
        painter.setClipRect(target)
        for y in range(target.top(), target.bottom() + 1, 12):
            for x in range(target.left(), target.right() + 1, 12):
                painter.fillRect(x, y, 12, 12, QtGui.QColor("#9ba0a8" if ((x - target.left()) // 12 + (y - target.top()) // 12) % 2 else "#d9dce1"))
        painter.drawPixmap(target, self.pixmap)
        painter.restore()

    def mouseDoubleClickEvent(self, event):
        if self.path:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.path))


class ImageControls(QtWidgets.QWidget):
    def __init__(self, item):
        super().__init__()
        self.item = item
        self.model = item.model
        self.session = session_for(item)
        self.manager = manager_for(self.model)
        self._preview_path = None
        self._setup_signature = None
        self._setup_complete = False
        self._image_browse_buttons = {}
        self._link_scene = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        # These rows supply anchors even though the raw parameter editors are hidden.
        ports = QtWidgets.QGridLayout()
        ports.setVerticalSpacing(1)
        self.port_labels = {"in": {}, "out": {}}
        names = {"in": (("prompt", "Positive prompt"), ("negative_prompt", "Negative prompt"),
                        ("image", "Input image"), ("reference_image", "Reference")),
                 "out": (("image", "Image"), ("image_path", "Image path"), ("seed_used", "Seed used"),
                         ("status", "Status"), ("job_id", "Job ID"), ("metadata", "Metadata"))}
        for side, entries in names.items():
            for row_index, (key, text) in enumerate(entries):
                label = QtWidgets.QLabel(text)
                label.setFixedHeight(18)
                label.setToolTip(f"{'Input' if side == 'in' else 'Output'} port: {key}")
                if side == "out":
                    label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.port_labels[side][key] = label
                ports.addWidget(label, row_index, 0 if side == "in" else 1)
        layout.addLayout(ports)
        self.backend = QtWidgets.QComboBox()
        self.backend.addItem("ComfyUI", "comfyui")
        self.backend.addItem("Native Diffusers - official BF16", "native_diffusers")
        self.backend.setCurrentIndex(max(0, self.backend.findData(value(self.model, "backend"))))
        self.backend.setToolTip("Choose the local inference runtime. Native uses a separate installation and official model weights.")
        self.backend.currentIndexChanged.connect(self._backend_changed)
        layout.addWidget(self.backend)
        row = QtWidgets.QHBoxLayout()
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem("Text to Image", "text_to_image")
        self.mode.addItem("Image Edit", "image_edit")
        self.mode.setCurrentIndex(max(0, self.mode.findData(value(self.model, "mode"))))
        self.variant = QtWidgets.QComboBox()
        self._populate_variants()
        row.addWidget(self.mode)
        row.addWidget(self.variant)
        layout.addLayout(row)
        edit_options = QtWidgets.QFormLayout()
        self.edit_guidance = QtWidgets.QComboBox()
        guidance_note = ("Input means image 1. All options use your prompt, the Input image and any supplied references. "
                         "These are instruction presets; exact pose, camera or clothing preservation is not guaranteed.")
        for label, data, hint in (
                ("Flexible — follow your prompt", "flexible",
                 "Adds no extra edit-guidance instructions. Qwen interprets how to combine the supplied images."),
                ("Edit input — favor existing composition", "preserve_input",
                 "Asks Qwen to edit the Input image while favoring its existing camera angle, pose, framing and subject placement. "
                 "Your prompt can still request changes to appearance, clothing or the environment."),
                ("Use input as a pose/layout guide", "pose_guide",
                 "Asks Qwen to use the Input image's pose and composition as a visual guide for the finished appearance "
                 "described by your prompt and any references.")):
            self.edit_guidance.addItem(label, data)
            self.edit_guidance.setItemData(self.edit_guidance.count() - 1, hint + "\n\n" + guidance_note,
                                          QtCore.Qt.ToolTipRole)
        self.edit_guidance.setCurrentIndex(max(0, self.edit_guidance.findData(value(self.model, "edit_guidance"))))
        self.edit_guidance.currentIndexChanged.connect(lambda: self._save("edit_guidance", self.edit_guidance.currentData()))
        edit_options.addRow("Guidance", self.edit_guidance)
        self.edit_framing = QtWidgets.QComboBox()
        for label, data in (("Automatic (last supplied image)", "auto"),
                            ("Match Input image aspect ratio", "input"),
                            ("Match first Reference aspect ratio", "reference"),
                            ("Custom width / height", "custom")):
            self.edit_framing.addItem(label, data)
        self.edit_framing.setCurrentIndex(max(0, self.edit_framing.findData(value(self.model, "edit_framing"))))
        self.edit_framing.setToolTip("Choose the output canvas independently of editing guidance. Match modes use Edit size and a 32-pixel grid. Custom uses Width and Height. Reference means image 2.")
        self.edit_framing.currentIndexChanged.connect(lambda: self._save("edit_framing", self.edit_framing.currentData()))
        edit_options.addRow("Framing", self.edit_framing)
        layout.addLayout(edit_options)
        self.prompt = QtWidgets.QPlainTextEdit(value(self.model, "prompt"))
        self.prompt.setPlaceholderText("Describe an image, or give an editing instruction. A prompt connection overrides this text.")
        self.prompt.setFixedHeight(76)
        self.prompt.setTabChangesFocus(True)
        # Persist typing immediately for Save; notify the graph on focus loss or Generate.
        self.prompt.textChanged.connect(lambda: put(self.model, prompt=self.prompt.toPlainText()))
        self.prompt.installEventFilter(self)
        layout.addWidget(self.prompt)
        self.negative_prompt = QtWidgets.QLineEdit(value(self.model, "negative_prompt"))
        self.negative_prompt.setPlaceholderText("Negative prompt (native; requires CFG above 1)")
        self.negative_prompt.editingFinished.connect(lambda: self._save("negative_prompt", self.negative_prompt.text()))
        layout.addWidget(self.negative_prompt)
        self.image = self._file_row(layout, "Input image", "image")
        self.reference = self._file_row(layout, "Reference (optional)", "reference_image")
        self.additional_references = QtWidgets.QPlainTextEdit(value(self.model, "reference_images"))
        self.additional_references.setPlaceholderText("Additional native references: one file path per line (10 images total)")
        self.additional_references.setFixedHeight(48)
        self.additional_references.setTabChangesFocus(True)
        self.additional_references.textChanged.connect(lambda: put(self.model, reference_images=self.additional_references.toPlainText()))
        self.additional_references.installEventFilter(self)
        layout.addWidget(self.additional_references)
        grid = QtWidgets.QGridLayout()
        self.width = self._spin(grid, 0, 0, "Width", "width", 64, 4096, 32)
        self.height = self._spin(grid, 0, 2, "Height", "height", 64, 4096, 32)
        self.steps = self._spin(grid, 1, 0, "Steps", "steps", 1, 100)
        self.steps_label = grid.itemAtPosition(1, 0).widget()
        self.edit_resolution = self._spin(grid, 1, 2, "Edit size", "edit_resolution", 64, 4096, 32)
        self.edit_resolution.setToolTip("Conditioning resolution; the reference image determines the output aspect ratio.")
        self.cfg = QtWidgets.QDoubleSpinBox()
        self.cfg.setRange(0, 20)
        self.cfg.setSingleStep(0.1)
        self.cfg.setValue(float(value(self.model, "cfg")))
        self.cfg.valueChanged.connect(lambda n: self._save("cfg", str(n)))
        self.cfg_label = QtWidgets.QLabel("CFG")
        grid.addWidget(self.cfg_label, 2, 0)
        grid.addWidget(self.cfg, 2, 1)
        self.preset = QtWidgets.QComboBox()
        for label, data in (("Presets", ""), ("Fast preview", "fast"), ("Standard", "standard"),
                            ("Quality 2048", "quality"), ("Quality 16:9", "quality_wide"),
                            ("Quality portrait", "quality_portrait"),
                            ("Low memory 512", "small"), ("Widescreen", "wide")):
            self.preset.addItem(label, data)
        self.preset.activated.connect(self._preset)
        grid.addWidget(self.preset, 2, 2, 1, 2)
        layout.addLayout(grid)
        row = QtWidgets.QHBoxLayout()
        self.seed = QtWidgets.QLineEdit(value(self.model, "seed"))
        self.seed.setPlaceholderText("Seed")
        self.seed.setMaximumWidth(170)
        self.seed.editingFinished.connect(lambda: self._save("seed", self.seed.text().strip()))
        self.random = QtWidgets.QCheckBox("Random seed")
        self.random.setChecked(value(self.model, "random_seed") == "true")
        self.random.toggled.connect(lambda enabled: self._save("random_seed", str(enabled).lower()))
        row.addWidget(QtWidgets.QLabel("Seed"))
        row.addWidget(self.seed)
        row.addWidget(self.random)
        layout.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.transparent = QtWidgets.QCheckBox("Transparent PNG")
        self.transparent.setChecked(value(self.model, "transparent") == "true")
        self.transparent.toggled.connect(lambda enabled: self._save("transparent", str(enabled).lower()))
        self.low_memory = QtWidgets.QCheckBox("Low memory")
        self.low_memory.setToolTip("CPU text encoder + tiled VAE. For full model offload, start the backend with this checked. Restart it after changing the launch profile.")
        self.low_memory.setChecked(value(self.model, "low_memory") == "true")
        self.low_memory.toggled.connect(lambda enabled: self._save("low_memory", str(enabled).lower()))
        row.addWidget(self.transparent)
        row.addWidget(self.low_memory)
        layout.addLayout(row)
        self.preview = ImagePreview()
        layout.addWidget(self.preview)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setFixedHeight(48)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        row = QtWidgets.QHBoxLayout()
        self.generate = QtWidgets.QPushButton("Generate")
        generate_font = self.generate.font()
        generate_font.setBold(True)
        self.generate.setFont(generate_font)
        self.generate.clicked.connect(self._generate)
        self.setup = QtWidgets.QPushButton("Setup")
        self.setup.clicked.connect(self._setup)
        self.open_image = QtWidgets.QPushButton("Open PNG")
        self.open_image.clicked.connect(self._open_image)
        for button in (self.generate, self.setup, self.open_image):
            button.setAutoDefault(False)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            row.addWidget(button, 1)
        layout.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.start = QtWidgets.QPushButton("Start Local Backend")
        self.start.clicked.connect(self._toggle_backend)
        self.check = QtWidgets.QPushButton("Check Backend")
        self.check.clicked.connect(self.session.check_backend)
        self.output_folder = QtWidgets.QPushButton("Output Folder")
        self.output_folder.setToolTip("Open the generated image folder. Change the save destination in Setup.")
        self.output_folder.clicked.connect(self._output_folder)
        self.recover = QtWidgets.QPushButton("Recover Job")
        self.recover.setToolTip("Fetch the saved job's result after a timeout, restart, or detach without queuing another generation.")
        self.recover.clicked.connect(lambda: self.session.generate(recover=True))
        for button in (self.start, self.check, self.recover, self.output_folder):
            button.setAutoDefault(False)
            row.addWidget(button)
        layout.addLayout(row)
        self.mode.currentIndexChanged.connect(lambda: self._save("mode", self.mode.currentData()))
        self.variant.currentIndexChanged.connect(lambda: self._save("model_variant", self.variant.currentData()))
        self.session.changed.connect(self.refresh)
        self.session.image_selected.connect(self._image_selected)
        self.manager.changed.connect(self.refresh)
        self.readiness = QtCore.QTimer(self)
        self.readiness.setInterval(5000)
        self.readiness.timeout.connect(self._check_startup)
        self._startup_checks = 0
        self.refresh()
        # Nodes can be constructed before being added to their graph scene.
        QtCore.QTimer.singleShot(0, self.refresh)

    def _file_row(self, layout, label, key):
        row = QtWidgets.QHBoxLayout()
        edit = QtWidgets.QLineEdit(value(self.model, key))
        edit.setPlaceholderText(label + " (or connect its port)")
        edit.editingFinished.connect(lambda: self._save(key, edit.text()))
        button = QtWidgets.QPushButton("...")
        button.setFixedWidth(32)
        button.setAutoDefault(False)
        button.setObjectName("qwen_browse_" + key)
        button.setToolTip("Choose " + label.lower() + " file.")
        self._image_browse_buttons[key] = button
        session = self.session
        # Return from the graphics proxy's click event before opening a modal
        # native window. The session survives deletion/rebuild of these controls.
        button.clicked.connect(lambda checked=False: QtCore.QTimer.singleShot(
            0, lambda: session.choose_image(key, label)))
        row.addWidget(edit, 1)
        row.addWidget(button)
        layout.addLayout(row)
        return edit

    def _image_selected(self, key, path):
        edit = self.image if key == "image" else self.reference
        edit.setText(path)

    def _spin(self, grid, row, column, label, key, low, high, step=1):
        spin = QtWidgets.QSpinBox()
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setValue(int(value(self.model, key)))
        spin.valueChanged.connect(lambda n: self._save(key, str(n)))
        grid.addWidget(QtWidgets.QLabel(label), row, column)
        grid.addWidget(spin, row, column + 1)
        return spin

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.FocusOut and watched in (
                getattr(self, "prompt", None), getattr(self, "additional_references", None)):
            notify(self.item)
        return super().eventFilter(watched, event)

    def _save(self, key, text):
        put(self.model, **{key: text})
        notify(self.item)
        self.refresh()

    def _populate_variants(self):
        with QtCore.QSignalBlocker(self.variant):
            self.variant.clear()
            if value(self.model, "backend") == "native_diffusers":
                self.variant.addItem("BF16 - official weights", "bf16")
            else:
                self.variant.addItem("INT8 - RTX 3090", "int8")
                self.variant.addItem("GGUF Q4 - lightweight", "gguf_q4")
            self.variant.setCurrentIndex(max(0, self.variant.findData(value(self.model, "model_variant"))))

    def _backend_changed(self):
        selected = self.backend.currentData()
        if selected == value(self.model, "backend"):
            return
        if self.manager.server_running or self.manager.installing or self.manager.generating or self.session.check_task:
            with QtCore.QSignalBlocker(self.backend):
                self.backend.setCurrentIndex(self.backend.findData(value(self.model, "backend")))
            self.session.message = "Stop the current backend and finish active jobs/checks before switching runtimes."
            self.session.changed.emit()
            return
        self.manager.changed.disconnect(self.refresh)
        from shiboken6 import isValid
        dialog = getattr(self.model, "_qwen_setup_dialog", None)
        if dialog is not None and isValid(dialog):
            dialog.close()
            dialog.deleteLater()
            self.model._qwen_setup_dialog = None
        put(self.model, backend=selected, model_variant="bf16" if selected == "native_diffusers" else "int8")
        self.manager = manager_for(self.model)
        self.manager.changed.connect(self.refresh)
        self.session.bind_manager()
        self.session.inventory = None
        self._populate_variants()
        self.session.message = "Open Setup to install or verify the selected runtime, then start Local Backend."
        notify(self.item)
        self.session.changed.emit()

    def _preset(self):
        preset = self.preset.currentData()
        if not preset:
            return
        dimensions = {"quality": (2048, 2048), "quality_wide": (2752, 1536),
                      "quality_portrait": (1792, 2400), "wide": (2048, 1152), "small": (512, 512)}
        width, height = dimensions.get(preset, (1024, 1024))
        self.width.setValue(width)
        self.height.setValue(height)
        self.steps.setValue(25 if preset in ("fast", "small") else 40)
        self.edit_resolution.setValue(512 if preset == "small" else 2048 if preset.startswith("quality") else 1024)
        self.cfg.setValue(1.0)
        self.low_memory.setChecked(preset == "small")
        if preset == "small" and self.variant.count() > 1 and self.variant.model().item(1).isEnabled():
            self.variant.setCurrentIndex(1)
        self.preset.setCurrentIndex(0)

    def _generate(self):
        put(self.model, prompt=self.prompt.toPlainText(), seed=self.seed.text().strip(),
            image=self.image.text().strip(), reference_image=self.reference.text().strip())
        self.session.generate()

    def _setup(self):
        from .setup_ui import show_setup
        show_setup(self.item)

    def _start_backend(self):
        try:
            self.manager.start_server(self.low_memory.isChecked())
            self.session.inventory = None
            self._startup_checks = 0
            self.readiness.start()
            self.session.check_backend()
        except Exception as exc:
            self.session.message = str(exc)
            self.session.changed.emit()

    def _check_startup(self):
        self._startup_checks += 1
        if self.session.inventory is not None or self._startup_checks >= 36 or not self.manager.server_running:
            self.readiness.stop()
            return
        self.session.check_backend()

    def _toggle_backend(self):
        if not self.manager.server_running:
            self._start_backend()
            return
        try:
            self.manager.stop_server()
            self.readiness.stop()
        except Exception as exc:
            self.session.message = str(exc)
            self.session.changed.emit()

    def _open_image(self):
        path = value(self.model, "image_path")
        if path:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))

    def _output_folder(self):
        try:
            image_path = value(self.model, "image_path").strip()
            directory = resolve_path(self.item, image_path).parent if image_path else output_directory_for(self.item)
            directory.mkdir(parents=True, exist_ok=True)
            if not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(directory))):
                raise OSError(f"Could not open the output folder: {directory}")
        except (OSError, ValueError) as exc:
            self.session.message = str(exc)
            self.session.changed.emit()

    def _installation_complete(self):
        if self.manager.installing:
            return False
        config = self.manager.config
        native = value(self.model, "backend") == "native_diffusers"
        variant = value(self.model, "model_variant")
        receipt = config.runtime / (".qubit-native-installed.json" if native else f".qubit-qwen-{variant}.json")
        try:
            stamp = receipt.stat()
            if not config.python.is_file():
                return False
            signature = (config, native, variant, stamp.st_mtime_ns, stamp.st_size)
            if signature != self._setup_signature:
                self._setup_signature = signature
                self._setup_complete = False
                # Validate completed setup once per receipt/configuration change,
                # without hashing model weights or importing inference libraries.
                if native:
                    from echograph.image_generation.native_config import installed_manifest
                    self._setup_complete = variant == "bf16" and bool(installed_manifest(config))
                else:
                    from echograph.image_generation.schema import RESOURCES
                    data = json.loads(receipt.read_text(encoding="utf-8"))
                    versions = json.loads((RESOURCES / f"{variant}-versions.json").read_text(encoding="utf-8"))
                    manifest = json.loads((RESOURCES / "manifest.json").read_text(encoding="utf-8"))
                    self._setup_complete = (data["versions"] == versions
                        and data["source_revision"] == manifest["source_revision"]
                        and (config.runtime / "main.py").is_file() and bool(data["models"])
                        and all((config.runtime / "models" / entry["path"]).is_file()
                                and (config.runtime / "models" / entry["path"]).stat().st_size == entry["bytes"]
                                for entry in data["models"]))
            return self._setup_complete
        except (OSError, ValueError, KeyError, TypeError):
            return False

    @staticmethod
    def _button_color(button, color):
        # Only change colors; retain the app's normal button padding and sizing.
        style = (f"QPushButton:enabled {{ background-color: {color}; color: white; }}" if color else "")
        if button.styleSheet() != style:
            button.setStyleSheet(style)

    def refresh(self):
        try:
            scene = self.item.scene()
        except RuntimeError:
            return  # A queued update can arrive after the node has been deleted.
        if scene is not self._link_scene:
            if self._link_scene is not None and hasattr(self._link_scene, "linksChanged"):
                try:
                    self._link_scene.linksChanged.disconnect(self.refresh)
                except (RuntimeError, TypeError):
                    pass
            self._link_scene = scene
            if scene is not None and hasattr(scene, "linksChanged"):
                scene.linksChanged.connect(self.refresh)
        mode = value(self.model, "mode")
        native = value(self.model, "backend") == "native_diffusers"
        variant = value(self.model, "model_variant")
        for combo, data in ((self.mode, mode), (self.variant, variant),
                            (self.edit_guidance, value(self.model, "edit_guidance")),
                            (self.edit_framing, value(self.model, "edit_framing"))):
            with QtCore.QSignalBlocker(combo):
                combo.setCurrentIndex(max(0, combo.findData(data)))
        running = self.session.running
        edges = (scene._ordered_in_edges(self.item) if hasattr(scene, "_ordered_in_edges") else
                 scene._in_edges(self.item) if hasattr(scene, "_in_edges") else [])
        connected = {str(getattr(edge, "dst_port_name", None) or
                         getattr(edge, "dst_label", "")).lower() for edge in edges}
        self.prompt.setEnabled(not running and "prompt" not in connected)
        self.prompt.setToolTip("Using the connected Positive prompt input. Disconnect it to edit this text."
                               if "prompt" in connected else "Describe the image or edit to generate.")
        self.image.setEnabled(mode == "image_edit" and not running)
        self.reference.setEnabled(mode == "image_edit" and not running)
        for button in self._image_browse_buttons.values():
            button.setEnabled(mode == "image_edit" and not running)
        self.additional_references.setEnabled(native and mode == "image_edit" and not running)
        self.edit_guidance.setEnabled(native and mode == "image_edit" and not running)
        self.edit_guidance.setToolTip(self.edit_guidance.currentData(QtCore.Qt.ToolTipRole) or "")
        self.edit_framing.setEnabled(native and mode == "image_edit" and not running)
        self.negative_prompt.setEnabled(native and not running and "negative_prompt" not in connected)
        self.negative_prompt.setToolTip("Using the connected Negative prompt input. Disconnect it to edit this text."
                                        if "negative_prompt" in connected else
                                        "Native negative prompt: describe what to avoid. Used only when CFG is above 1.0.")
        self.port_labels["in"]["prompt"].setToolTip("Positive prompt text. A single-field Note supplies its value automatically; multi-field Notes supply only checked values. A connection overrides the local prompt.")
        self.port_labels["in"]["negative_prompt"].setToolTip("Optional native negative prompt text. A single-field Note supplies its value automatically; multi-field Notes supply only checked values. A connection overrides the local negative prompt. Used only when CFG is above 1.0.")
        self.port_labels["in"]["negative_prompt"].setEnabled(native)
        steps_hint = ("Steps: how many refinement passes generate the image.\n"
                      "Start with 40 for Qwen-Image 2.1; try 20-25 for faster previews.\n"
                      "More steps usually take longer and may refine details, but do not always improve the result.")
        cfg_hint = ("CFG: prompt guidance strength. Start with 1.0 for Qwen-Image 2.1.\n"
                    "Native CFG above 1.0 requires a negative prompt and adds processing work.\n"
                    "Very high values can introduce artifacts. CFG does not control reference-image strength."
                    if native else
                    "CFG: prompt guidance strength. Start with 1.0 for Qwen-Image 2.1.\n"
                    "Higher values can push prompt adherence but may introduce artifacts; behavior depends on the workflow.\n"
                    "CFG does not control reference-image strength.")
        for widget in (self.steps_label, self.steps, self.steps.lineEdit()):
            widget.setToolTip(steps_hint)
        for widget in (self.cfg_label, self.cfg, self.cfg.lineEdit()):
            widget.setToolTip(cfg_hint)
        self.backend.setEnabled(not running and not self.manager.generating and not self.manager.installing)
        self.low_memory.setToolTip("Sequential CPU offloading. Preserves BF16 weights, requested size and steps; can be much slower."
                                   if native else "CPU text encoder + tiled VAE. Restart the backend after changing the launch profile.")
        self.edit_resolution.setToolTip("Sets the native conditioning size and automatic/matched output pixel budget. Framing chooses the aspect ratio. Custom framing uses Width and Height."
                                       if native else "Conditioning resolution; reference image determines aspect ratio.")
        self.image.setToolTip("Conditioning image 1. Guidance can favor its pose/camera, and Framing can match its aspect ratio."
                              if native else "Main image to edit.")
        self.reference.setToolTip("Conditioning image 2; it can strongly influence the result. Describe what to use from each image, or select optional Guidance."
                                  if native else "Optional second image for the selected edit workflow.")
        custom_edit = native and value(self.model, "edit_framing") == "custom"
        self.width.setEnabled((mode != "image_edit" or custom_edit) and not running)
        self.height.setEnabled((mode != "image_edit" or custom_edit) and not running)
        self.edit_resolution.setEnabled(mode == "image_edit" and not running)
        self.seed.setEnabled(not self.random.isChecked() and not running)
        self.generate.setText("Cancel" if running else "Generate")
        self.generate.setEnabled(running or (not self.manager.installing and not self.manager.stopping))
        self.setup.setEnabled(not running)
        installed = self._installation_complete()
        self._button_color(self.generate, "#b83b3b" if running else "#14532d" if installed else "")
        self._button_color(self.setup, "" if installed else "#2563c9")
        self.start.setText("Stopping Backend..." if self.manager.stopping else (
            "Stop Local Backend" if self.manager.server_running else "Start Local Backend"))
        self.start.setEnabled(not self.manager.installing and not self.manager.stopping
                              and not self.manager.generating)
        self.start.setToolTip("Finish or cancel active image jobs before stopping the shared backend."
                             if self.manager.generating else
                             "Stop this app's shared local backend to release RAM and GPU memory."
                             if self.manager.server_running else "Start the local Qwen Image backend.")
        self.check.setEnabled(self.session.check_task is None)
        self.recover.setEnabled(not running and bool(value(self.model, "job_id")) and bool(value(self.model, "pending_request")))
        for widget in (self.mode, self.variant, self.steps, self.cfg, self.random, self.transparent, self.low_memory, self.preset):
            widget.setEnabled(not running)
        if self.session.inventory is not None and not native:
            available = "UnetLoaderGGUF" in self.session.inventory
            option = self.variant.model().item(1)
            option.setEnabled(available)
            option.setToolTip("" if available else "Open Setup and install GGUF Q4, then restart the local backend.")
        path = value(self.model, "image_path")
        self.preview.load(path)
        self.open_image.setEnabled(bool(path) and Path(path).is_file())
        message = "Installing / verifying Qwen resources..." if self.manager.installing else self.session.message
        self.status.setText(message)
        self.status.setToolTip(message + "\nJob: " + value(self.model, "job_id"))


def render_node_body(item, y_cursor):
    body = ImageControls(item)
    body.setFixedWidth(int(item.width))
    body.ensurePolished()
    height = max(body.sizeHint().height(), body.minimumSizeHint().height())
    proxy = QtWidgets.QGraphicsProxyWidget(item)
    proxy.setWidget(body)
    proxy.setPos(0, y_cursor)
    proxy.resize(item.width, height)
    body.layout().activate()
    item._hide_default_output_with_named = True
    item._show_default_input_with_named = False
    item._default_named_input = "prompt"
    for side, labels in body.port_labels.items():
        positions = item._input_port_pos if side == "in" else item._output_port_pos
        for name, label in labels.items():
            local_y = label.mapTo(body, QtCore.QPoint(0, label.height() // 2)).y()
            positions[name] = (QtCore.QPointF(0 if side == "in" else item.width, y_cursor + local_y), name)
            PortDot(item, proxy, name, side, positions[name][0])
    item._qwen_body_height = height + 12
    item._plugin_proxies.append(proxy)
    bottom = y_cursor + height + 12
    item.prepareGeometryChange()
    item.height = max(item.height, bottom)
    return bottom


def augment_infocard_footer(card, layout):
    model = getattr(card, "_node_ref", None)
    scene = getattr(card, "_graph_scene", None)
    item = getattr(scene, "_node_items", {}).get(model.name) if model is not None else None
    if item is None:
        return False
    button = QtWidgets.QPushButton("Qwen Image Setup")
    from .setup_ui import show_setup
    button.clicked.connect(lambda: show_setup(item))
    layout.addWidget(button)
    return True


QWEN_IMAGE_SPEC = Spec(stripe_color="#d28ff7", build_ports=build_ports,
                       render_node_body=render_node_body, augment_infocard_footer=augment_infocard_footer)
