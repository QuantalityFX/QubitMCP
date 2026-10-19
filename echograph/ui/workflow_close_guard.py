from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from echograph.qt_compat import QtCore, QtWidgets


class WorkflowCloseGuard(QtCore.QObject):
    """Compare workflow content with its saved state before closing the editor."""

    def __init__(self, window, scene, save_workflow):
        super().__init__(window)
        self._window = window
        self._scene = scene
        self._save_workflow = save_workflow
        self._initializing = False
        self._settle_timer = QtCore.QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._settle_load)
        scene.paramChanged.connect(self._on_edit)
        scene.linksChanged.connect(self._on_edit)
        self.reset_after_load()

    @staticmethod
    def _content(data):
        return deepcopy({key: data.get(key, [] if key != "settings" else {})
                         for key in ("nodes", "edges", "comments", "settings")})

    def _state(self):
        return self._content(self._window._workflow_data_for_save())

    def reset_after_load(self):
        self._saved_state = self._state()
        self._initializing = True
        self._settle_pass = 0
        QtWidgets.QApplication.instance().installEventFilter(self)
        self._settle_timer.start(0)

    def _settle_load(self):
        self._settle_pass += 1
        if self._settle_pass < 3:
            self._settle_timer.start(0)
        else:
            self._finish_load()

    def _finish_load(self, *, preserve_baseline=False):
        if not self._initializing:
            return
        if not preserve_baseline:
            self._saved_state = self._state()
        self._initializing = False
        self._settle_timer.stop()
        QtWidgets.QApplication.instance().removeEventFilter(self)

    def _on_edit(self, *_args):
        if not getattr(self._window, "_workflow_load_in_progress", False):
            self._finish_load(preserve_baseline=True)

    def eventFilter(self, obj, event):
        if self._initializing and event.type() in (
            QtCore.QEvent.MouseButtonPress, QtCore.QEvent.KeyPress,
            QtCore.QEvent.ShortcutOverride, QtCore.QEvent.Wheel,
            QtCore.QEvent.GraphicsSceneMousePress,
        ):
            belongs_here = obj is self._scene
            if isinstance(obj, QtWidgets.QWidget):
                root = obj.window()
                proxy = root.graphicsProxyWidget()
                belongs_here = root is self._window or (proxy is not None and proxy.scene() is self._scene)
            if belongs_here:
                self._finish_load()
        return super().eventFilter(obj, event)

    def commit_focused_editor(self):
        focus = QtWidgets.QApplication.focusWidget()
        if focus is not None:
            root = focus.window()
            proxy = root.graphicsProxyWidget()
            if root is self._window or (proxy is not None and proxy.scene() is self._scene):
                focus.clearFocus()

    def mark_saved(self, data):
        self._finish_load(preserve_baseline=True)
        self._saved_state = self._content(data)

    def is_dirty(self):
        self._finish_load()
        return self._state() != self._saved_state

    def confirm_close(self):
        try:
            self._finish_load()
            self.commit_focused_editor()
            if not self.is_dirty():
                return True
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self._window, "Save Workflow", f"Could not check for unsaved changes:\n{exc}"
            )
            return False
        path = str(getattr(self._window, "_current_path", "") or "")
        label = Path(path).name if path else "Untitled Workflow"
        message = f'Save changes to workflow "{label}" before closing?'
        answer = QtWidgets.QMessageBox.question(
            self._window, "Save Workflow", message,
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Save,
        )
        if answer == QtWidgets.QMessageBox.Discard:
            return True
        if answer == QtWidgets.QMessageBox.Save:
            try:
                return bool(self._save_workflow())
            except Exception as exc:
                QtWidgets.QMessageBox.warning(
                    self._window, "Save Workflow", f"Could not save the workflow:\n{exc}"
                )
        return False
