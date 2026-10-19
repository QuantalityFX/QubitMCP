from __future__ import annotations

from copy import deepcopy

from echograph.qt_compat import QtCore, QtWidgets, QKeySequence
from echograph.ui import actions, hotkeys
from echograph.ui.infocard import InfoCard
from echograph.ui.view import GraphView


class ShelfSnippetWindow(QtWidgets.QMainWindow):
    """Edit a shelf snippet in its own scene and save back only on request."""

    closed = QtCore.Signal(str)
    saved = QtCore.Signal()

    def __init__(self, parent, *, scene_factory, store, tool):
        # Use a normal dialog caption: Windows omits buttons on tool captions.
        # The parent still owns this window, keeping it above the main editor.
        super().__init__(parent, QtCore.Qt.Dialog | QtCore.Qt.CustomizeWindowHint | QtCore.Qt.WindowTitleHint
                         | QtCore.Qt.WindowSystemMenuHint | QtCore.Qt.WindowMinMaxButtonsHint
                         | QtCore.Qt.WindowCloseButtonHint)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.resize(1100, 720)
        self._store = store
        self._tool = deepcopy(tool)
        self._tool_id = str(tool["id"])
        self._label = str(tool.get("label") or "Node Snippet")
        self.setWindowTitle(f"{self._label}[*] - Shelf Node Snippet")
        self._workflow_path = store.snippet_workflow_path(tool)
        self._observed_file_version = self._file_version()
        self._workflow = store.load_snippet_workflow(tool)
        self._external_update = False
        self._loading_workflow = False
        self._current_path = str(self._workflow_path)
        self._current_project_dir = str(self._workflow_path.parent)
        self._info_card = None

        self.scene = scene_factory(on_info=self._show_node_info)
        self.scene.setParent(self)
        self.view = GraphView(self.scene)

        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(central)
        source = QtWidgets.QLabel(
            f'Save updates shelf snippet "{self._label}" in:\n{self._workflow_path}', central
        )
        source.setWordWrap(True)
        source.setTextFormat(QtCore.Qt.PlainText)
        source.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(source)
        controls = QtWidgets.QHBoxLayout()
        self.save_button = QtWidgets.QPushButton("Save Snippet", central)
        self.save_button.clicked.connect(self.save_snippet)
        controls.addWidget(self.save_button)
        frame_button = QtWidgets.QPushButton("Frame All", central)
        frame_button.clicked.connect(self.frame_all)
        controls.addWidget(frame_button)
        controls.addStretch(1)
        close_button = QtWidgets.QPushButton("Close", central)
        close_button.clicked.connect(self.close)
        controls.addWidget(close_button)
        layout.addLayout(controls)
        self.update_banner = QtWidgets.QFrame(central)
        self.update_banner.setStyleSheet("QFrame{background:#493a1e;border-radius:4px;}")
        update_layout = QtWidgets.QHBoxLayout(self.update_banner)
        self.update_label = QtWidgets.QLabel(self.update_banner)
        self.update_label.setWordWrap(True)
        self.update_label.setTextFormat(QtCore.Qt.PlainText)
        update_layout.addWidget(self.update_label, 1)
        self.update_button = QtWidgets.QPushButton("Update", self.update_banner)
        self.update_button.setToolTip("Load the latest changes saved in the other instance.")
        self.update_button.clicked.connect(self.reload_snippet)
        update_layout.addWidget(self.update_button)
        self.update_banner.hide()
        layout.addWidget(self.update_banner)
        layout.addWidget(self.view, 1)
        self.setCentralWidget(central)

        self._info_dock = QtWidgets.QDockWidget("Node Info", self)
        self._info_scroll = QtWidgets.QScrollArea(self._info_dock)
        self._info_scroll.setWidgetResizable(True)
        self._info_dock.setWidget(self._info_scroll)
        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self._info_dock)
        self._info_dock.hide()
        self.scene.nodeDeleted.connect(self._on_node_deleted)

        # Loading at the original positions avoids paste offsets or renaming nodes.
        self.scene.from_dict(deepcopy(self._workflow))
        self._saved_state = self._network_state()
        self._initializing = True
        self._initial_load_pass = 0
        self._initial_load_timer = QtCore.QTimer(self)
        self._initial_load_timer.setSingleShot(True)
        self._initial_load_timer.timeout.connect(self._settle_initial_load)
        self._dirty_timer = QtCore.QTimer(self)
        self._dirty_timer.setSingleShot(True)
        self._dirty_timer.setInterval(150)
        self._dirty_timer.timeout.connect(self._refresh_dirty)
        self.scene.changed.connect(self._schedule_dirty_check)
        self.scene.paramChanged.connect(self._on_network_edited)
        self.scene.linksChanged.connect(self._on_network_edited)
        self._refresh_dirty()

        self._shortcuts = []
        self._hotkey_shortcuts = {}
        for owner, action_id, sequence, callback in (
            (self, "app_save", "Ctrl+S", self.save_snippet),
            (self, "shelf_snippet_maximize", "Shift+F", self._toggle_maximized),
            (self.view, "node_copy", "Ctrl+C", lambda: actions.copy_selected_nodes_from_window(self)),
            (self.view, "node_paste", "Ctrl+V", lambda: actions.paste_nodes_from_window(self)),
            (self.view, "node_delete", "Del", self._delete_selection),
            (self.view, "gl_frame", "F", self._frame_from_hotkey),
            (self.view, "comment_group", "C", lambda: actions.create_comment_group_from_window(self)),
        ):
            shortcut = hotkeys.add_shortcut(
                owner, action_id, sequence, callback, context=QtCore.Qt.WidgetWithChildrenShortcut
            )
            if action_id == "shelf_snippet_maximize":
                shortcut.setAutoRepeat(False)
            self._shortcuts.append(shortcut)
            self._hotkey_shortcuts[action_id] = shortcut
        QtCore.QTimer.singleShot(0, self, self.frame_all)
        QtWidgets.QApplication.instance().installEventFilter(self)
        self._initial_load_timer.start(0)
        # Poll the path, rather than watching a file handle: saves replace the
        # workflow atomically, and may come from a separate app process.
        self._external_update_timer = QtCore.QTimer(self)
        self._external_update_timer.setInterval(1000)
        self._external_update_timer.timeout.connect(self._check_external_update)
        self._external_update_timer.start()

    def apply_hotkey_map(self, mapping):
        for action_id, shortcut in self._hotkey_shortcuts.items():
            shortcut.setKey(QKeySequence(str(mapping.get(action_id, shortcut.key().toString()) or "")))

    def _toggle_maximized(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _settle_initial_load(self):
        # Node widgets can queue another update when first attached to the scene.
        self._initial_load_pass += 1
        if self._initial_load_pass < 3:
            self._initial_load_timer.start(0)
        else:
            self._finish_initial_load()

    def _finish_initial_load(self, *, preserve_baseline=False):
        if not self._initializing:
            return
        self._initial_load_timer.stop()
        if not preserve_baseline:
            self._saved_state = self._network_state()
        self._initializing = False
        QtWidgets.QApplication.instance().removeEventFilter(self)
        self._refresh_dirty()

    def _on_network_edited(self, *_args):
        if self._loading_workflow:
            return
        # Explicit model edits must remain dirty even before the first event-loop tick.
        self._finish_initial_load(preserve_baseline=True)
        self._schedule_dirty_check()

    def eventFilter(self, obj, event):
        if self._initializing and event.type() in (
            QtCore.QEvent.MouseButtonPress, QtCore.QEvent.KeyPress,
            QtCore.QEvent.ShortcutOverride, QtCore.QEvent.Wheel,
            QtCore.QEvent.GraphicsSceneMousePress,
        ):
            belongs_here = obj is self.scene
            if isinstance(obj, QtWidgets.QWidget):
                root = obj.window()
                proxy = root.graphicsProxyWidget()
                belongs_here = root is self or (proxy is not None and proxy.scene() is self.scene)
            if belongs_here:
                # Freeze the baseline before the first user action can edit a node.
                self._finish_initial_load()
        return super().eventFilter(obj, event)

    def _show_node_info(self, node):
        if self._info_card is not None and self._info_card._node_ref is node:
            self._info_dock.show()
            return
        old = self._info_scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        node._graph_scene = self.scene
        self._info_card = InfoCard(node)
        self._info_card.attach_scene(self.scene)
        self._info_card.requestJump.connect(self.scene.center_on_name)
        self._info_card.closedForNode.connect(self._info_dock.hide)
        self._info_scroll.setWidget(self._info_card)
        self._info_dock.show()

    def _on_node_deleted(self, name):
        if self._info_card is not None and self._info_card._node_ref.name == name:
            self._info_scroll.takeWidget().deleteLater()
            self._info_card = None
            self._info_dock.hide()

    def _delete_selection(self):
        if not actions._focus_is_text_input():
            actions.delete_selected_nodes_from_window(self)

    def frame_all(self):
        items = list(self.scene._node_items.values()) + list(self.scene._comment_groups)
        if not items:
            return
        rect = QtCore.QRectF()
        for item in items:
            rect = rect.united(item.sceneBoundingRect())
        self.view.fitInView(rect.adjusted(-50, -50, 50, 50), QtCore.Qt.KeepAspectRatio)

    def _frame_from_hotkey(self):
        if not actions._focus_is_text_input():
            self.frame_all()

    def _network_state(self):
        data = self.scene.to_dict()
        return deepcopy({key: data.get(key, []) for key in ("nodes", "edges", "comments")})

    def _schedule_dirty_check(self, *_args):
        if not self._loading_workflow and not self._dirty_timer.isActive():
            self._dirty_timer.start()

    def _refresh_dirty(self):
        dirty = not self._initializing and self._network_state() != self._saved_state
        if self.isWindowModified() != dirty:
            self.setWindowModified(dirty)
        can_save = dirty and not self._external_update
        if self.save_button.isEnabled() != can_save:
            self.save_button.setEnabled(can_save)
        return dirty

    def _file_version(self):
        info = self._workflow_path.stat()
        return info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino

    def _set_external_update(self, message=None):
        self._external_update = message is not None
        self.update_label.setText(message or "")
        self.update_banner.setVisible(self._external_update)
        self._refresh_dirty()

    def _check_external_update(self, *, force=False):
        try:
            version = self._file_version()
            if not force and version == self._observed_file_version:
                return self._external_update
            workflow = self._store.load_snippet_workflow(self._tool)
        except Exception as exc:
            # A missing or partially written file must never allow a stale save.
            # Retry on the next tick, including when its timestamp is unchanged.
            self._observed_file_version = None
            self._set_external_update(
                f"The saved snippet cannot be read. Your open changes are kept; saving is paused.\n{exc}"
            )
            return True
        self._observed_file_version = version
        self._set_external_update(
            "This snippet has changed in another instance. Click Update to load the saved changes."
            if workflow != self._workflow else None
        )
        return self._external_update

    def reload_snippet(self):
        self._finish_initial_load()
        self._commit_focused_editor()
        if self._refresh_dirty():
            answer = QtWidgets.QMessageBox.question(
                self, "Update Node Snippet",
                "Updating will replace your unsaved edits in this window with the latest saved changes.\n\n"
                "Discard your unsaved edits and update?",
                QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel,
            )
            if answer != QtWidgets.QMessageBox.Discard:
                return False
        try:
            # Read after confirmation so another save during the prompt is included.
            version = self._file_version()
            workflow = self._store.load_snippet_workflow(self._tool)
            previous = deepcopy(self.scene.to_dict())
        except Exception as exc:
            self._check_external_update(force=True)
            QtWidgets.QMessageBox.warning(self, "Update Node Snippet", f"Could not update the node snippet:\n{exc}")
            return False

        self._dirty_timer.stop()
        old_info = self._info_scroll.takeWidget()
        if old_info is not None:
            old_info.deleteLater()
        self._info_card = None
        self._info_dock.hide()
        self._loading_workflow = True
        try:
            try:
                self.scene.from_dict(deepcopy(workflow))
            except Exception:
                # Deserialization clears the scene before constructing nodes.
                # Restore the open copy if a node cannot be loaded.
                self.scene.from_dict(previous)
                raise
        except Exception as exc:
            self._refresh_dirty()
            QtWidgets.QMessageBox.warning(self, "Update Node Snippet", f"Could not update the node snippet:\n{exc}")
            return False
        finally:
            self._loading_workflow = False

        self._workflow = workflow
        self._observed_file_version = version
        self._saved_state = self._network_state()
        # Reuse startup settling for nodes that queue widget updates on load.
        self._initializing = True
        self._initial_load_pass = 0
        QtWidgets.QApplication.instance().installEventFilter(self)
        self._initial_load_timer.start(0)
        self._set_external_update()
        QtCore.QTimer.singleShot(0, self, self.frame_all)
        return True

    def save_snippet(self):
        self._finish_initial_load()
        self._commit_focused_editor()
        # Check synchronously as well: Ctrl+S can arrive before the polling tick.
        if self._check_external_update(force=True):
            QtWidgets.QMessageBox.warning(
                self, "Save Node Snippet",
                "The saved snippet has changed or cannot be read. Click Update before saving.\n\n"
                "Your edits in this window have been kept.",
            )
            return False
        try:
            current = self._store.tool_by_id(self._tool_id)
            if current is None or current.get("type") != "graph_snippet":
                raise ValueError("The original shelf node snippet no longer exists.")
            state = self._network_state()
            workflow = deepcopy(self._workflow)
            data = self.scene.to_dict()
            settings = dict(workflow.get("settings") or {})
            settings.update(data.get("settings") or {})
            workflow.update(data)
            workflow["settings"] = settings
            self._store.save_snippet_workflow(self._tool_id, workflow)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Save Node Snippet", f"Could not save the node snippet:\n{exc}")
            return False
        self._workflow = workflow
        # Re-read on the next tick; a different instance may save immediately
        # after this one, before we could capture a post-save timestamp.
        self._observed_file_version = None
        self._saved_state = deepcopy(state)
        self._refresh_dirty()
        self.saved.emit()
        return True

    def _commit_focused_editor(self):
        focus = QtWidgets.QApplication.focusWidget()
        if focus is not None and focus.window() is self:
            focus.clearFocus()

    def closeEvent(self, event):
        # Commit any focused editor before comparing against the saved network.
        self._finish_initial_load()
        self._commit_focused_editor()
        try:
            dirty = self._refresh_dirty()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Node Snippet", f"Could not check for unsaved changes:\n{exc}")
            event.ignore()
            return
        if dirty:
            answer = QtWidgets.QMessageBox.question(
                self, "Save Node Snippet",
                f'Save changes to shelf snippet "{self._label}" before closing?\n\n{self._workflow_path}',
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Save,
            )
            if answer not in (QtWidgets.QMessageBox.Save, QtWidgets.QMessageBox.Discard):
                event.ignore()
                return
            if answer == QtWidgets.QMessageBox.Save and not self.save_snippet():
                event.ignore()
                return
        self._dirty_timer.stop()
        self._external_update_timer.stop()
        self.closed.emit(self._tool_id)
        event.accept()
