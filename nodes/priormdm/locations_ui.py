"""Optional folder choices for setup; installation remains a separate user action."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from echograph.motion.backends.priormdm.backend import PriorMDMConfig


class LocationsDialog(QtWidgets.QDialog):
    def __init__(self, setup_dialog):
        super().__init__(setup_dialog)
        self.setup_dialog = setup_dialog
        self.original = setup_dialog.session.config
        self._repository = self.original.repository
        self.setWindowTitle("PriorMDM folders (optional)")
        self.setWindowModality(QtCore.Qt.WindowModal)
        self.resize(760, 310)
        layout = QtWidgets.QVBoxLayout(self)
        explanation = QtWidgets.QLabel(
            "Keep the defaults or choose another drive or folder. Setup uses files already there "
            "and downloads anything missing. Existing files stay in their current locations.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)
        self.edits = {}
        values = {"repository": self.original.repository, "download_cache": self.original.cache_directory,
                  "dataset": self.original.dataset}
        for key, label in (("repository", "PriorMDM installation"), ("download_cache", "Download cache"),
                           ("dataset", "HumanML3D normalization files")):
            row = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            edit = QtWidgets.QLineEdit(str(values[key]))
            self.edits[key] = edit
            row_layout.addWidget(edit, 1)
            browse = QtWidgets.QPushButton("Browse…")
            browse.clicked.connect(lambda checked=False, k=key: self._browse(k))
            row_layout.addWidget(browse)
            form.addRow(label, row)
        self.edits["repository"].editingFinished.connect(self._repository_changed)
        self.remember = QtWidgets.QCheckBox("Use these folders for new nodes and projects")
        self.remember.setChecked(True)
        layout.addWidget(self.remember)
        scope = QtWidgets.QLabel("These choices also save with this node when you save the graph. Other existing nodes keep their own paths.")
        scope.setWordWrap(True)
        layout.addWidget(scope)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QtWidgets.QHBoxLayout()
        self.reset_button = QtWidgets.QPushButton("Restore default folders")
        self.reset_button.clicked.connect(self._reset)
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.apply_button = QtWidgets.QPushButton("Save folders")
        self.apply_button.clicked.connect(self._apply)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    def _browse(self, key):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder", self.edits[key].text())
        if folder:
            self.edits[key].setText(folder)
            if key == "repository":
                self._repository_changed()

    def _repository_changed(self):
        raw = self.edits["repository"].text().strip().strip('"')
        if not raw:
            return
        repository = Path(raw).expanduser().resolve()
        for key, relative in (("download_cache", "downloads"), ("dataset", "dataset/HumanML3D")):
            current = self.edits[key].text().strip().strip('"')
            if not current or Path(current).expanduser().resolve() == (self._repository / relative).resolve():
                self.edits[key].setText(str(repository / relative))
        self._repository = repository

    def _reset(self):
        defaults = PriorMDMConfig()
        self.edits["repository"].setText(str(defaults.repository))
        self.edits["download_cache"].setText(str(defaults.cache_directory))
        self.edits["dataset"].setText(str(defaults.dataset))
        self._repository = defaults.repository

    def _apply(self):
        try:
            self._repository_changed()
            paths = {}
            for key, edit in self.edits.items():
                raw = edit.text().strip().strip('"')
                if not raw:
                    raise ValueError("Choose a folder for each location, or restore the defaults.")
                path = Path(raw).expanduser().resolve()
                if path.exists() and not path.is_dir():
                    raise ValueError(f"This location is a file, not a folder: {path}")
                paths[key] = path
            config = replace(self.original, **paths)
            if config.repository != self.original.repository:
                # A managed environment/model follows its repository; custom files stay custom.
                if self.original.python.is_relative_to(self.original.repository):
                    config = replace(config, python=config.repository / self.original.python.relative_to(self.original.repository))
                if self.original.checkpoint and self.original.checkpoint.is_relative_to(self.original.repository):
                    config = replace(config, checkpoint=None)
            self.setup_dialog.apply_locations(config, remember=self.remember.isChecked())
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
            return
        self.accept()
