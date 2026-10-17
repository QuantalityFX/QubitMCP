from __future__ import annotations

from pathlib import Path
import weakref

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from echograph.image_generation.config import RuntimeConfig, save_config
from echograph.image_generation.schema import LICENSE_URL
from .controller import manager_for, notify, output_directory_for, put, session_for, value


class SetupDialog(QtWidgets.QDialog):
    def __init__(self, item):
        parent = item.scene().views()[0].window() if item.scene() and item.scene().views() else None
        super().__init__(parent)
        self.item_ref = weakref.ref(item)
        self.native = value(item.model, "backend") == "native_diffusers"
        self.manager = manager_for(item.model)
        self.setWindowTitle("Qwen Image 2.1 - Local setup")
        self.resize(880, 960)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.configuration_scroll = QtWidgets.QScrollArea()
        self.configuration_scroll.setWidgetResizable(True)
        self.configuration_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        configuration = QtWidgets.QWidget()
        content = QtWidgets.QVBoxLayout(configuration)
        content.setContentsMargins(0, 0, 8, 0)
        content.setSpacing(14)
        title = QtWidgets.QLabel("Local Qwen-Image Installation")
        title_font = title.font()
        title_font.setPointSizeF(max(9, title_font.pointSizeF()) + 3)
        title_font.setBold(True)
        title.setFont(title_font)
        content.addWidget(title)
        content.addWidget(self._description("Installs official Qwen BF16 weights and a separate local Diffusers runtime."
                                            if self.native else "Installs the selected AI model and its local inference runtime."))

        details = QtWidgets.QHBoxLayout()
        details.setSpacing(16)
        requirements = QtWidgets.QGroupBox("Requirements")
        requirements_layout = QtWidgets.QVBoxLayout(requirements)
        requirements_layout.setContentsMargins(12, 16, 12, 12)
        requirements_layout.setSpacing(6)
        requirement_texts = (("NVIDIA GPU with BF16 support; RTX 3090 target", "Python 3.12", "64 GB system RAM recommended",
                              "Disk: ~18 GB runtime and ~38 GB models on separate drives") if self.native else
                             ("NVIDIA GPU with a current driver", "Python 3.12", "Git", "At least 45 GB free disk space"))
        for text in requirement_texts:
            requirements_layout.addWidget(self._description(text))
        details.addWidget(requirements, 1)
        downloads = QtWidgets.QGroupBox("Model download sizes")
        downloads_layout = QtWidgets.QFormLayout(downloads)
        downloads_layout.setContentsMargins(12, 16, 12, 12)
        downloads_layout.setVerticalSpacing(10)
        downloads_layout.setHorizontalSpacing(16)
        model_sizes = (("Official BF16", "~33.1 GB\nAdditional Python/CUDA packages required"),
                       ("INT8 / GGUF", "Not yet validated for native inference")) if self.native else (("INT8", "~17.3 GB\nRecommended for RTX 3090"),
                           ("GGUF Q4", "~14.6 GB\nLower memory option"),
                           ("Both variants", "~21.9 GB\nShared components"))
        for name, text in model_sizes:
            downloads_layout.addRow(name, self._description(text))
        details.addWidget(downloads, 1)
        content.addLayout(details)
        content.addWidget(self._description(
            "Native BF16 uses CPU model offloading. Low memory uses slower sequential offloading. "
            "Quality presets keep the requested size and steps. GPU quality validation is still required."
            if self.native else "Low-memory mode supports CPU offloading and tiled VAE decoding."))

        fields = QtWidgets.QGroupBox("Installation fields")
        form = QtWidgets.QFormLayout(fields)
        form.setContentsMargins(12, 16, 12, 12)
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(16)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.variant = QtWidgets.QComboBox()
        if self.native:
            self.variant.addItem("BF16 - official Qwen checkpoint", "bf16")
        else:
            self.variant.addItem("INT8 - RTX 3090 / 24 GB", "int8")
            self.variant.addItem("GGUF Q4_K_M - lightweight", "gguf_q4")
        self.variant.setCurrentIndex(max(0, self.variant.findData(value(item.model, "model_variant"))))
        form.addRow("Install model", self.variant)
        self.repository = QtWidgets.QLineEdit(str(getattr(self.manager.config, "repository", "")))
        self.runtime = QtWidgets.QLineEdit(str(self.manager.config.runtime))
        self.runtime.setToolTip("Python and CUDA packages. Native model files can be installed in a separate folder.")
        self.repository.setToolTip("The existing clone is reused when it matches the pinned source revision.")
        if not self.native:
            form.addRow("Source folder", self.repository)
        else:
            self.repository.hide()
        form.addRow("Runtime folder", self.runtime)
        if self.native:
            model_row = QtWidgets.QWidget()
            model_layout = QtWidgets.QHBoxLayout(model_row)
            model_layout.setContentsMargins(0, 0, 0, 0)
            self.model_directory = QtWidgets.QLineEdit(str(self.manager.config.models_root))
            self.model_directory.setPlaceholderText(str(self.manager.config.runtime / "models"))
            self.model_directory.setToolTip("Choose a parent folder such as T:\\MLModels. Setup creates Qwen-Image-2.1 inside it. Existing Qwen checkpoints are reused. Leave blank for the default under the runtime.")
            self.model_directory.editingFinished.connect(self._save_native_paths)
            self.model_browse = QtWidgets.QPushButton("...")
            self.model_browse.setFixedWidth(32)
            self.model_browse.setAutoDefault(False)
            self.model_browse.setToolTip("Choose the parent folder for model installations.")
            self.model_browse.clicked.connect(self._choose_model_directory)
            model_layout.addWidget(self.model_directory, 1)
            model_layout.addWidget(self.model_browse)
            form.addRow("Models folder", model_row)
            self.model_destination = self._description("")
            form.addRow("Install location", self.model_destination)
            self.model_directory.textChanged.connect(self._update_model_destination)
            self.runtime.textChanged.connect(self._update_model_destination)
            self._update_model_destination()
        output_row = QtWidgets.QWidget()
        output_layout = QtWidgets.QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        self.output_directory = QtWidgets.QLineEdit(value(item.model, "output_directory"))
        self.output_directory.setPlaceholderText(str(output_directory_for(item)))
        self.output_directory.setToolTip("Folder for future generated images. Leave blank to use the workflow's default folder.")
        self.output_directory.editingFinished.connect(self._save_output_directory)
        self.output_browse = QtWidgets.QPushButton("...")
        self.output_browse.setFixedWidth(32)
        self.output_browse.setAutoDefault(False)
        self.output_browse.setToolTip("Choose the folder for generated images.")
        self.output_browse.clicked.connect(self._choose_output_directory)
        output_layout.addWidget(self.output_directory, 1)
        output_layout.addWidget(self.output_browse)
        form.addRow("Output folder", output_row)
        self.license_use = QtWidgets.QComboBox()
        self.license_use.addItem("Noncommercial research / evaluation", "research")
        self.license_use.addItem("Commercial use under a separate Qwen license", "separately_licensed")
        self.license_use.setCurrentIndex(max(0, self.license_use.findData(value(item.model, "license_use"))))
        form.addRow("Model use", self.license_use)
        content.addWidget(fields)
        content.addStretch()
        self.configuration_scroll.setWidget(configuration)
        layout.addWidget(self.configuration_scroll, 1)

        license_group = QtWidgets.QGroupBox("Model license")
        license_layout = QtWidgets.QVBoxLayout(license_group)
        license_layout.setContentsMargins(12, 16, 12, 12)
        license_layout.setSpacing(10)
        # A real button has a clear hit target and keyboard focus before any setup.
        self.license_link = QtWidgets.QPushButton("Read the Qwen Research License")
        self.license_link.setAutoDefault(False)
        license_font = self.license_link.font()
        license_font.setUnderline(True)
        self.license_link.setFont(license_font)
        self.license_link.setStyleSheet(
            "QPushButton { color: #3b82f6; }"
            "QPushButton:hover { color: #60a5fa; }"
            "QPushButton:pressed { color: #2563eb; }"
        )
        self.license_link.setCursor(QtCore.Qt.PointingHandCursor)
        self.license_link.setToolTip(LICENSE_URL)
        self.license_link.clicked.connect(self._open_license)
        license_layout.addWidget(self.license_link, 0, QtCore.Qt.AlignLeft)
        license_layout.addWidget(self._description(
            "Local model use is subject to the Qwen Research License.\n"
            "Research/evaluation is permitted; commercial use of the model requires a separate Qwen license.\n"
            "Contact: model-business@notice.qwencloud.com"))
        if not self.native:
            license_layout.addWidget(self._description(
                "GGUF weights are supplied by a third party and retain the Qwen license restrictions."))
        self.accept = QtWidgets.QCheckBox("I accept the model license and hold a separate license if required.")
        self.accept.setChecked(value(item.model, "license_accepted") == "true")
        self.accept.toggled.connect(self._save_license)
        self.license_use.currentIndexChanged.connect(self._usage_changed)
        license_layout.addWidget(self.accept)
        # Keep the license action and acknowledgement visible even on smaller screens.
        layout.addWidget(license_group)
        row = QtWidgets.QHBoxLayout()
        self.install = QtWidgets.QPushButton("Install / Verify Selected Model")
        self.start = QtWidgets.QPushButton("Start Local Backend")
        self.stop = QtWidgets.QPushButton("Stop This App's Backend")
        self.check = QtWidgets.QPushButton("Check Backend")
        self.install.clicked.connect(self._install)
        self.start.clicked.connect(self._start)
        self.stop.clicked.connect(self._stop)
        self.check.clicked.connect(lambda: self._session().check_backend() if self._session() else None)
        for button in (self.install, self.start, self.stop, self.check):
            button.setAutoDefault(False)
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(100)
        self.log.setMaximumHeight(110)
        self.log.setMaximumBlockCount(1500)
        self.log.setPlainText(self.manager.log_text)
        layout.addWidget(self.log)
        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        close.rejected.connect(self.close)
        layout.addWidget(close)
        self.manager.output.connect(self._append)
        self.manager.changed.connect(self.refresh)
        self.manager.setup_finished.connect(self._installed)
        session_for(item).changed.connect(self.refresh)
        self.refresh()

    @staticmethod
    def _description(text):
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(QtCore.Qt.PlainText)
        label.setMinimumHeight(label.fontMetrics().lineSpacing() + 4)
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        return label

    def _open_license(self):
        if not QtGui.QDesktopServices.openUrl(QtCore.QUrl(LICENSE_URL)):
            self.status.setText("Could not open your browser. Read the model license at:\n" + LICENSE_URL)

    def _item(self):
        item = self.item_ref()
        return item if item is not None and isValid(item) and item.scene() is not None else None

    def _session(self):
        item = self._item()
        return session_for(item) if item else None

    def _usage_changed(self):
        # A previous acknowledgement does not assert a newly selected commercial license.
        self.accept.setChecked(False)
        self._save_license()

    def _save_license(self):
        item = self._item()
        if item:
            put(item.model, license_use=self.license_use.currentData(),
                license_accepted=str(self.accept.isChecked()).lower())
            notify(item)
            session_for(item).changed.emit()
        self.refresh()

    def _save_output_directory(self):
        item = self._item()
        if item:
            put(item.model, output_directory=self.output_directory.text().strip())
            notify(item)

    def _choose_output_directory(self):
        item = self._item()
        if item is None:
            return
        # This top-level dialog remains alive when the embedded node controls rebuild.
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Generated image folder",
                                                         str(output_directory_for(item)))
        if path and self._item() is not None:
            self.output_directory.setText(path)
            self._save_output_directory()

    def _save_native_paths(self):
        if not self.native or self.manager.installing or self.manager.server_running or self.manager.generating:
            return
        try:
            self._configure()
            self.status.setText("Models folder saved. Install / Verify will use: " + str(self.manager.config.model))
        except Exception as exc:
            self.status.setText(str(exc))

    def _choose_model_directory(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Models installation folder",
            self.model_directory.text().strip() or self.model_directory.placeholderText())
        if path and self._item() is not None:
            self.model_directory.setText(path)
            self._save_native_paths()

    def _selected_model_directory(self):
        from echograph.image_generation.native_config import model_directory_for_selection
        folder = self.model_directory.text().strip()
        return model_directory_for_selection(Path(folder) if folder else
            Path(self.runtime.text()).expanduser() / "models")

    def _update_model_destination(self, *_):
        try:
            self.model_destination.setText(str(self._selected_model_directory()))
        except (OSError, ValueError):
            self.model_destination.setText("Choose a valid models folder.")

    def _configure(self):
        if self.manager.installing or self.manager.server_running:
            return
        if self.native:
            from echograph.image_generation.native_config import NativeConfig, save_native_config
            from echograph.image_generation.native_installer import check_paths
            config = check_paths(NativeConfig(Path(self.runtime.text()).expanduser().resolve(),
                self._selected_model_directory()))
            self.manager.config = config
            save_native_config(config)
            return
        config = RuntimeConfig(Path(self.repository.text()).expanduser().resolve(),
                               Path(self.runtime.text()).expanduser().resolve())
        if config.runtime == config.repository or config.runtime.is_relative_to(config.repository):
            raise ValueError("Choose a runtime folder outside the Qwen source clone.")
        self.manager.config = config
        save_config(config)

    def _install(self):
        try:
            if not self.accept.isChecked():
                raise ValueError("Accept the model license before installing.")
            self._configure()
            self._save_license()
            self.manager.install(self.variant.currentData(), self.license_use.currentData())
        except Exception as exc:
            self.status.setText(str(exc))

    def _start(self):
        try:
            self._configure()
            item = self._item()
            self.manager.start_server(low_memory=item is not None and value(item.model, "low_memory") == "true")
            if self._session():
                self._session().check_backend()
        except Exception as exc:
            self.status.setText(str(exc))

    def _installed(self, success):
        if success:
            item = self._item()
            if item:
                put(item.model, model_variant=self.variant.currentData())
                notify(item)
                session_for(item).changed.emit()
        session = self._session()
        if session:
            session.message = ("Setup complete. Start Local Backend and check readiness." if success else
                               "Setup failed. Read the log below, fix the reported issue, and retry.")
            session.changed.emit()
        self.refresh()

    def _stop(self):
        try:
            self.manager.stop_server()
        except RuntimeError as exc:
            self.status.setText(str(exc))

    def _append(self, text):
        cursor = self.log.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText(text)
        self.log.setTextCursor(cursor)

    def refresh(self):
        busy = self.manager.installing or self.manager.server_running
        session = self._session()
        running = session is not None and session.running
        self.install.setEnabled(self.accept.isChecked() and not busy and not running)
        self.start.setEnabled(not busy and not self.manager.generating)
        self.stop.setText("Stopping Backend..." if self.manager.stopping else "Stop This App's Backend")
        self.stop.setEnabled(self.manager.server_running and not self.manager.stopping and not self.manager.generating)
        self.stop.setToolTip("Finish or cancel active image jobs before stopping the shared backend."
                             if self.manager.generating else
                             "Stop the local backend to release RAM and GPU memory.")
        self.repository.setEnabled(not busy)
        self.runtime.setEnabled(not busy)
        if self.native:
            self.model_directory.setEnabled(not busy and not self.manager.generating)
            self.model_browse.setEnabled(not busy and not self.manager.generating)
        self.variant.setEnabled(not busy)
        self.output_directory.setEnabled(not self.manager.generating)
        self.output_browse.setEnabled(not self.manager.generating)
        if self.manager.installing:
            self.status.setText("Installing / verifying Qwen resources...")
        elif session:
            self.status.setText(session.message)


def show_setup(item):
    dialog = getattr(item.model, "_qwen_setup_dialog", None)
    if dialog is not None and isValid(dialog) and dialog.native != (value(item.model, "backend") == "native_diffusers"):
        dialog.close()
        dialog.deleteLater()
        dialog = None
    if dialog is None or not isValid(dialog):
        dialog = SetupDialog(item)
        item.model._qwen_setup_dialog = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


def schedule_setup_offer(item):
    if getattr(item.model, "_qwen_setup_offered", False):
        return
    item.model._qwen_setup_offered = True
    reference = weakref.ref(item)
    def offer():
        current = reference()
        if current is not None and isValid(current) and current.scene() is not None:
            config = manager_for(current.model).config
            if not config.python.is_file() and value(current.model, "license_accepted") != "true":
                show_setup(current)
    QtCore.QTimer.singleShot(0, offer)
