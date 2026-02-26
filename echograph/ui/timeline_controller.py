from __future__ import annotations

from echograph.qt_compat import QtCore, QtWidgets


class TimelineController:
    def __init__(self, window):
        self._window = window

    def _win(self):
        return self._window

    def _gl_view(self):
        win = self._win()
        return getattr(win, "gl_view", None)

    def timeline_panel_enabled(self) -> bool:
        gv = self._gl_view()
        if gv is None:
            return False
        try:
            visible_fn = getattr(gv, "timeline_visible", None)
            if callable(visible_fn):
                return bool(visible_fn())
        except Exception:
            pass
        return False

    def sync_timeline_context(self) -> None:
        win = self._win()
        gv = self._gl_view()
        if gv is None:
            return
        setter = getattr(gv, "set_timeline_scene_context", None)
        if not callable(setter):
            return
        scene_name = ""
        try:
            active_scene = getattr(win, "_active_scene_node", None)
            scene_name = str(getattr(active_scene, "name", "") or "").strip() if active_scene is not None else ""
        except Exception:
            scene_name = ""
        project_path = None
        try:
            raw_path = str(getattr(win, "_current_path", "") or "").strip()
            if raw_path:
                project_path = raw_path
        except Exception:
            project_path = None
        try:
            setter(scene_name=scene_name or None, project_path=project_path)
        except Exception:
            pass

    def set_timeline_menu_active(self, active: bool) -> None:
        win = self._win()
        btn = getattr(win, "_timeline_btn", None)
        if btn is None:
            return
        want = bool(active) or self.timeline_panel_enabled()
        try:
            btn.setProperty("active", bool(want))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        except Exception:
            pass

    def sync_timeline_menu_state(self) -> None:
        win = self._win()
        btn = getattr(win, "_timeline_toggle_btn", None)
        if btn is None:
            return
        enabled = self.timeline_panel_enabled()
        try:
            btn.blockSignals(True)
            btn.setChecked(bool(enabled))
        except Exception:
            pass
        finally:
            try:
                btn.blockSignals(False)
            except Exception:
                pass
        self.set_timeline_menu_active(bool(enabled))

    def toggle_timeline_panel_from_menu(self, checked: bool) -> None:
        win = self._win()
        want = bool(checked)
        if want and str(getattr(win, "_view_mode", "2d")).lower() == "2d":
            try:
                win._set_view_mode("split")
            except Exception:
                pass
        self.sync_timeline_context()
        gv = self._gl_view()
        if gv is not None:
            try:
                set_visible = getattr(gv, "set_timeline_visible", None)
                if callable(set_visible):
                    set_visible(want)
            except Exception:
                pass
        self.sync_timeline_menu_state()
        try:
            win._close_menu_for_button("_timeline_btn")
        except Exception:
            pass
        try:
            QtCore.QTimer.singleShot(0, win._reset_ui_cursor_arrow)
        except Exception:
            try:
                win._reset_ui_cursor_arrow()
            except Exception:
                pass

    def timeline_hotkey_target(self):
        win = self._win()
        try:
            if not bool(win._viewport_hotkey_ok()):
                return None
        except Exception:
            return None
        gv = self._gl_view()
        if gv is None:
            return None
        try:
            fw = QtWidgets.QApplication.focusWidget()
            if isinstance(
                fw,
                (
                    QtWidgets.QLineEdit,
                    QtWidgets.QTextEdit,
                    QtWidgets.QPlainTextEdit,
                    QtWidgets.QSpinBox,
                    QtWidgets.QDoubleSpinBox,
                ),
            ):
                return None
        except Exception:
            pass
        if not self.timeline_panel_enabled():
            return None
        return gv

    def shortcut_timeline_play_toggle_action(self) -> None:
        gv = self.timeline_hotkey_target()
        if gv is None:
            return
        self.sync_timeline_context()
        try:
            toggle = getattr(gv, "timeline_toggle_playback", None)
            if callable(toggle):
                toggle()
                return
        except Exception:
            pass

    def shortcut_timeline_set_key_action(self) -> None:
        gv = self.timeline_hotkey_target()
        if gv is None:
            return
        self.sync_timeline_context()
        try:
            add_key = getattr(gv, "timeline_set_key", None)
            if callable(add_key):
                add_key()
                return
        except Exception:
            pass

    def timeline_delete_selected_keys_action(self) -> bool:
        gv = self.timeline_hotkey_target()
        if gv is None:
            return False
        try:
            delete_fn = getattr(gv, "timeline_delete_selected_keys", None)
            if callable(delete_fn):
                return bool(delete_fn())
        except Exception:
            pass
        return False
