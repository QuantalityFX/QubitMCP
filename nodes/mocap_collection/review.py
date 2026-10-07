"""Collection review window using the existing Video Player transport."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6 import QtCore, QtGui, QtWidgets
from shiboken6 import isValid

from nodes.video_player.spec import VideoPlayerWidget
from . import review_data as data


class _ScanThread(QtCore.QThread):
    resultReady = QtCore.Signal(object)

    def __init__(self, rows, base, root, parent):
        super().__init__(parent)
        self.args = rows, base, root

    def run(self):
        try:
            result = data.scan_renders(*self.args, cancelled=self.isInterruptionRequested)
        except Exception as exc:
            result = {"error": str(exc)}
        self.resultReady.emit(result)


class RenderScan(QtCore.QObject):
    def __init__(self, model, scene, root, parent):
        super().__init__(QtWidgets.QApplication.instance())
        from . import spec
        self.model, self.scene = model, scene
        self.progress = QtWidgets.QProgressDialog("Matching renders to BVH paths…", "Cancel", 0, 0, parent)
        self.progress.setWindowTitle("Load renders")
        self.progress.setWindowModality(QtCore.Qt.NonModal)
        self.progress.setMinimumDuration(0)
        base = spec.mocap._workflow_dir_for_node(SimpleNamespace(scene=lambda: scene))
        self.worker = _ScanThread(spec.entries(model), base, root, self)
        self.worker.resultReady.connect(self.finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.finished.connect(self.deleteLater)
        QtWidgets.QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.progress.canceled.connect(self.worker.requestInterruption)
        self.progress.show()
        self.worker.start()

    @QtCore.Slot()
    def shutdown(self):
        if isValid(self.worker):
            self.worker.requestInterruption()
            self.worker.wait()

    @QtCore.Slot(object)
    def finished(self, result):
        cancelled = self.progress.wasCanceled() if isValid(self.progress) else True
        if isValid(self.progress):
            parent = self.progress.parentWidget()
            self.progress.close()
            self.progress.deleteLater()
        else:
            parent = None
        self.model._collection_render_scan = None
        if cancelled or result.get("cancelled") or (self.scene is not None and not isValid(self.scene)):
            return
        try:
            if result.get("error"):
                raise ValueError(result["error"])
            count = data.apply_assignments(self.model, self.scene, result)
            QtWidgets.QMessageBox.information(parent, "Mocap render review",
                f"Matched renders to {count} BVH paths.\nSkipped {result['skipped']} unmatched, ambiguous, or incomplete takes.")
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(parent, "Mocap render review", str(exc))


def load_render_folder(model, scene, parent):
    data.ensure_idle(scene)
    if getattr(model, "_collection_render_scan", None) is not None:
        return
    root = QtWidgets.QFileDialog.getExistingDirectory(parent, "Load render folder (including take subfolders)", "")
    if root:
        model._collection_render_scan = RenderScan(model, scene, root, parent)


class ReviewPlayer(VideoPlayerWidget):
    def __init__(self, parent):
        self.playback_fps = 30.0
        self.adapter = SimpleNamespace(model=SimpleNamespace(params=[]), scene=lambda: None)
        super().__init__(self.adapter, parent)
        self._path_edit.hide()
        self._browse_btn.hide()
        self._sound_btn.hide()
        next_image = QtGui.QPixmap(str(Path(__file__).resolve().parents[2] / "icons" / "PlayNextButton_icon.png"))
        previous_image = next_image.transformed(QtGui.QTransform().scale(-1, 1))
        self.previous_button = QtWidgets.QToolButton(self)
        self.next_button = QtWidgets.QToolButton(self)
        for button, image, name, shortcut in (
            (self.previous_button, previous_image, "Previous animation", "Up arrow"),
            (self.next_button, next_image, "Next animation", "Down arrow"),
        ):
            button.setIcon(QtGui.QIcon(image))
            button.setFixedSize(self._play_btn.size())
            button.setIconSize(self._play_btn.iconSize())
            button.setToolTip(f"{name} ({shortcut})")
            button.setAccessibleName(name)
        self._transport_layout.insertWidget(0, self.previous_button)
        self._transport_layout.insertWidget(2, self.next_button)
        self._transport_layout.removeWidget(self._scrub_slider)
        self.layout().insertWidget(self.layout().count() - 1, self._scrub_slider)
        transport_style = (
            "QToolButton { background:#303640; border:1px solid #434a54;"
            " border-radius:4px; padding:3px; }"
            "QToolButton:hover { background:#5d6877; border-color:#758191; }"
            "QToolButton:pressed { background:#374151; }"
            "QToolButton:disabled { background:#343a43; border-color:#434a54; }"
        )
        for button in (self.previous_button, self._play_btn, self.next_button):
            button.setStyleSheet(transport_style)
        self._preview.setMinimumHeight(180)
        self._preview.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)

    def _frame_interval_ms(self):
        return max(1, round(1000 / self.playback_fps))

    def set_fps(self, fps):
        self.playback_fps = fps
        if self._seq_timer.isActive():
            self._seq_timer.setInterval(self._frame_interval_ms())


class ReviewDialog(QtWidgets.QDialog):
    def __init__(self, model, scene, parent=None):
        super().__init__(parent)
        # Use the normal Windows frame and work-area-aware maximize behavior.
        # A dialog's default frame does not provide all three caption buttons.
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowTitleHint
                            | QtCore.Qt.WindowSystemMenuHint | QtCore.Qt.WindowMinMaxButtonsHint
                            | QtCore.Qt.WindowCloseButtonHint)
        if parent is not None:
            self.setWindowIcon(parent.windowIcon())
        from . import spec
        self.model, self.graph_scene = model, scene
        self.row_number = spec.output_number(model) or (1 if spec.entries(model) else 0)
        self._last_output = spec.output_number(model)
        self._loaded_render = None
        self._restore_maximized = False
        self.setWindowTitle("Mocap Collection — Render review")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.resize(1000, 700)
        layout = QtWidgets.QVBoxLayout(self)
        self.output = QtWidgets.QSpinBox()
        self.output.setPrefix("Output ")
        self.output.setKeyboardTracking(False)
        self.checked_only = QtWidgets.QCheckBox("Checked only")
        self.path_label = QtWidgets.QLabel()
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.path_label)
        self.player = ReviewPlayer(self)
        self.previous = self.player.previous_button
        self.next = self.player.next_button
        self.player._transport_layout.addWidget(self.output)
        self.player._transport_layout.addWidget(self.checked_only)
        self.player._transport_layout.addStretch(1)
        self.player._preview.setToolTip("")
        layout.addWidget(self.player, 1)
        actions = QtWidgets.QHBoxLayout()
        self.approve = QtWidgets.QPushButton("Approve")
        self.approve.setCheckable(True)
        self.approve.setStyleSheet(
            "QPushButton { background:#4b5563; color:white; border:1px solid #6b7280;"
            " border-radius:4px; padding:5px 12px; }"
            "QPushButton:checked { background:#246b3f; border-color:#3a9958; }"
            "QPushButton:!checked:hover { background:#596474; }"
            "QPushButton:checked:hover { background:#2e804d; }"
            "QPushButton:disabled { color:#9ca3af; }"
        )
        self.include = QtWidgets.QCheckBox("Checked for batch")
        self.rename = QtWidgets.QPushButton("Rename folders")
        self.fps = QtWidgets.QDoubleSpinBox()
        self.fps.setRange(1, 240)
        self.fps.setValue(30)
        self.fps.setSuffix(" FPS")
        self.fps.setKeyboardTracking(False)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        for widget in (self.approve, self.include, self.rename, self.fps):
            actions.addWidget(widget)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.help_label = QtWidgets.QLabel(
            "Space: play/pause. Up/Down: previous/next animation. F11: toggle full screen. Esc: exit full screen."
        )
        self.help_label.setWordWrap(True)
        self.help_label.setStyleSheet("color:#94a3b8;padding:0 2px;")
        layout.addWidget(self.help_label)
        layout.addWidget(self.message)
        self.previous.clicked.connect(lambda: self.output.stepBy(-1))
        self.next.clicked.connect(lambda: self.output.stepBy(1))
        self.output.valueChanged.connect(self.select_output)
        self.checked_only.toggled.connect(self.toggle_filter)
        self.approve.toggled.connect(self.set_approved)
        self.include.toggled.connect(self.set_checked)
        self.rename.clicked.connect(self.rename_current)
        self.fps.valueChanged.connect(self.player.set_fps)
        if scene is not None:
            scene.paramChanged.connect(self.params_changed)
            scene.destroyed.connect(self.close)
        for button in self.findChildren(QtWidgets.QPushButton):
            button.setAutoDefault(False)
        # Handle transport keys even when a button, scrubber or spin box has
        # focus, without intercepting keys in other windows or rename dialogs.
        for widget in [self, *self.findChildren(QtWidgets.QWidget)]:
            widget.installEventFilter(self)
        self.refresh()

    @QtCore.Slot(str, list)
    def params_changed(self, name, _params):
        if name != self.model.name:
            return
        from . import spec
        active = spec.output_number(self.model)
        if active != self._last_output:
            self.row_number = active
        self.refresh()

    def refresh(self):
        from . import spec
        rows, eligible = spec.entries(self.model), spec.output_rows(self.model)
        active = spec.output_number(self.model)
        self._last_output = active
        if not 1 <= self.row_number <= len(rows):
            self.row_number = active
        with QtCore.QSignalBlocker(self.output), QtCore.QSignalBlocker(self.checked_only):
            self.output.setRange(1 if eligible else 0, len(eligible))
            self.output.setValue(eligible.index(active) + 1 if active else 0)
            self.checked_only.setChecked(spec.mocap._param_bool(self.model, "checked_only"))
        self.output.setEnabled(bool(eligible))
        self.previous.setEnabled(bool(eligible) and self.output.value() > 1)
        self.next.setEnabled(bool(eligible) and self.output.value() < len(eligible))
        row = rows[self.row_number - 1] if self.row_number else {}
        self.path_label.setText(f"#{self.row_number}  {row.get('path', 'No BVH selected')}")
        with QtCore.QSignalBlocker(self.approve), QtCore.QSignalBlocker(self.include):
            self.approve.setChecked(bool(row.get("approved")))
            self.include.setChecked(bool(row.get("checked")))
        self.approve.setText("Approved" if row.get("approved") else "Approve")
        for control in (self.approve, self.include, self.rename):
            control.setEnabled(bool(row))
        assignment = row.get("render", {})
        if assignment != self._loaded_render:
            self._loaded_render = dict(assignment)
            self.player._load_media(assignment.get("first_frame", ""), force=True)
            self.fps.setValue(assignment.get("frame_rate", 30))
            if self.player._mode == "sequence":
                self.player._on_play_clicked()
        self.message.setText("Preview only: this BVH is excluded by Checked only."
                             if row and self.row_number not in eligible else
                             "" if assignment else "No render assigned. Use Load renders on Mocap Collection.")

    def review_row(self, number):
        from . import spec
        self.row_number = number
        eligible = spec.output_rows(self.model)
        if number in eligible:
            self.select_output(eligible.index(number) + 1)
        self.refresh()

    def save(self, rows, active):
        from . import spec
        try:
            data.ensure_idle(self.graph_scene)
            previous = spec.mocap._param_value(self.model, "path")
            spec.save_state(self.model, rows, active, self.graph_scene)
            if previous != spec.mocap._param_value(self.model, "path"):
                spec.schedule_live_preview(self.model, self.graph_scene)
        except (ValueError, OSError) as exc:
            self.row_number = spec.output_number(self.model)
            self.refresh()
            self.message.setText(str(exc))
            return False
        if self.graph_scene is None:
            self.refresh()
        return True

    def select_output(self, index):
        from . import spec
        eligible = spec.output_rows(self.model)
        if 1 <= index <= len(eligible):
            self.row_number = eligible[index - 1]
            self.save(spec.entries(self.model), self.row_number)

    def toggle_filter(self, enabled):
        from . import spec
        try:
            data.ensure_idle(self.graph_scene)
        except ValueError as exc:
            self.refresh()
            self.message.setText(str(exc))
            return
        spec.mocap._set_param_value(self.model, "checked_only", "1" if enabled else "0")
        self.save(spec.entries(self.model), self.row_number)

    def set_approved(self, enabled):
        self.set_flag("approved", enabled)

    def set_checked(self, enabled):
        self.set_flag("checked", enabled)

    def set_flag(self, key, enabled):
        from . import spec
        rows = spec.entries(self.model)
        if 1 <= self.row_number <= len(rows):
            rows[self.row_number - 1][key] = enabled
            self.save(rows, spec.output_number(self.model))

    def rename_current(self):
        self.player._stop_playback()
        self.player._set_play_button(False)
        rename_with_dialog(self.model, self.graph_scene, self.row_number, self)
        self.refresh()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            if self._restore_maximized:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._restore_maximized = self.isMaximized()
            self.showFullScreen()

    def eventFilter(self, watched, event):
        if (event.type() in (QtCore.QEvent.ShortcutOverride, QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease)
                and watched.window() is self and event.modifiers() == QtCore.Qt.NoModifier
                and event.key() in (QtCore.Qt.Key_Space, QtCore.Qt.Key_Up, QtCore.Qt.Key_Down)):
            event.accept()
            if event.type() == QtCore.QEvent.KeyPress and not event.isAutoRepeat():
                if event.key() == QtCore.Qt.Key_Space:
                    self.player._play_btn.click()
                elif event.key() == QtCore.Qt.Key_Up:
                    self.previous.click()
                else:
                    self.next.click()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_F11 or (event.key() == QtCore.Qt.Key_Escape and self.isFullScreen()):
            self.toggle_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.player._stop_playback()
        if getattr(self.model, "_collection_review_dialog", None) is self:
            self.model._collection_review_dialog = None
        super().closeEvent(event)


def rename_with_dialog(model, scene, number, parent):
    from . import spec
    rows = spec.entries(model)
    if not 1 <= number <= len(rows):
        return
    current = Path(rows[number - 1]["path"]).parent.name
    name, accepted = QtWidgets.QInputDialog.getText(parent, "Rename BVH and render folders",
        "New BVH folder name (also renames assigned take folders).\nAll files in the BVH folder stay together:", text=current)
    if accepted and name != current:
        try:
            data.rename_entry(model, scene, number, name)
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(parent, "Rename folders", str(exc))


def open_review(model, scene, parent=None, number=None):
    dialog = getattr(model, "_collection_review_dialog", None)
    if dialog is None or not isValid(dialog):
        dialog = ReviewDialog(model, scene, parent)
        model._collection_review_dialog = dialog
    if number:
        dialog.review_row(number)
    if dialog.isMinimized():
        dialog.setWindowState(dialog.windowState() & ~QtCore.Qt.WindowMinimized)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
