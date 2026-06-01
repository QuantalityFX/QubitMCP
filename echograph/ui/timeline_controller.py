from __future__ import annotations

from echograph.qt_compat import QtCore, QtWidgets


class TimelineController:
    def __init__(self, window):
        self._window = window
        self._timeline_menu_button = None
        self._timeline_toggle_button = None
        self._audio_toggle_button = None

    def _win(self):
        return self._window

    def bind_menu_widgets(self, menu_button, timeline_toggle_button, audio_toggle_button=None) -> None:
        self._timeline_menu_button = menu_button
        self._timeline_toggle_button = timeline_toggle_button
        self._audio_toggle_button = audio_toggle_button

    def _menu_button(self):
        return self._timeline_menu_button

    def _toggle_button(self):
        return self._timeline_toggle_button

    def _audio_button(self):
        return self._audio_toggle_button

    def _menu_visible(self) -> bool:
        btn = self._menu_button()
        if btn is None:
            return False
        try:
            menu = btn.menu()
        except Exception:
            menu = None
        if menu is None:
            return False
        try:
            return bool(menu.isVisible())
        except Exception:
            return False

    def _close_menu(self) -> None:
        btn = self._menu_button()
        if btn is None:
            return
        try:
            menu = btn.menu()
        except Exception:
            menu = None
        if menu is None:
            return
        try:
            menu.close()
        except Exception:
            try:
                menu.hide()
            except Exception:
                pass

    def _gl_view(self):
        win = self._win()
        return getattr(win, "gl_view", None)

    def _notify_panel_layout_changed(self) -> None:
        win = self._win()
        hook = getattr(win, "_on_panel_layout_changed", None)
        if callable(hook):
            try:
                hook()
            except Exception:
                pass

    def save_layout_preset_from_menu(self) -> None:
        win = self._win()
        saver = getattr(win, "_save_current_layout_as_global_preset", None)
        if callable(saver):
            try:
                saver()
            except Exception:
                pass
        self.sync_timeline_menu_state()
        try:
            self._close_menu()
        except Exception:
            pass
        try:
            QtCore.QTimer.singleShot(0, win._reset_ui_cursor_arrow)
        except Exception:
            try:
                win._reset_ui_cursor_arrow()
            except Exception:
                pass

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

    def audio_panel_enabled(self) -> bool:
        gv = self._gl_view()
        if gv is None:
            return False
        try:
            visible_fn = getattr(gv, "timeline_audio_visible", None)
            if callable(visible_fn):
                return bool(visible_fn())
        except Exception:
            pass
        return False

    def sync_timeline_context(self, *, apply_current_frame: bool = True, load_audio: bool = True) -> None:
        win = self._win()
        gv = self._gl_view()
        if gv is None:
            return
        setter = getattr(gv, "set_timeline_scene_context", None)
        if not callable(setter):
            return
        scene_name = ""
        preview_context = None
        try:
            raw_preview = getattr(win, "_active_scene_preview_context", None)
            if isinstance(raw_preview, dict):
                preview_context = raw_preview
                scene_name = str(raw_preview.get("scene_name") or raw_preview.get("name") or "").strip()
        except Exception:
            preview_context = None
            scene_name = ""
        try:
            active_scene = getattr(win, "_active_scene_node", None)
            if preview_context is None:
                scene_name = str(getattr(active_scene, "name", "") or "").strip() if active_scene is not None else ""
        except Exception:
            if preview_context is None:
                scene_name = ""
        project_path = None
        try:
            raw_path = str(getattr(win, "_current_path", "") or "").strip()
            if raw_path:
                project_path = raw_path
        except Exception:
            project_path = None
        owner_name = None
        card = None
        skeleton_row_selected = False
        try:
            active_name = str(scene_name or "").strip()
            cards = getattr(win, "_card_by_node", None)
            if preview_context is None and isinstance(cards, dict):
                if active_name:
                    card = cards.get(active_name)
                if card is None:
                    active_scene = getattr(win, "_active_scene_node", None)
                    for maybe in cards.values():
                        if getattr(maybe, "_node_ref", None) is active_scene:
                            card = maybe
                            break
            if card is not None and bool(getattr(card, "_scene_outliner_user_selected", False)):
                selected_kind = str(getattr(card, "_scene_selected_kind", "") or "").strip().lower()
                skeleton_row_selected = selected_kind == "skeleton"
                raw_owner = str(getattr(card, "_scene_selected_owner", "") or "").strip()
                if raw_owner and not skeleton_row_selected:
                    owner_name = raw_owner
        except Exception:
            owner_name = None
        if preview_context is None and owner_name is None and card is not None:
            # Fallback to current outliner row even when the user-selected flag is stale.
            try:
                outliner = getattr(card, "_scene_outliner_widget", None)
                current_item = outliner.currentItem() if outliner is not None else None
                if current_item is not None:
                    item_kind = str(current_item.data(QtCore.Qt.UserRole + 1) or "").strip().lower()
                    if item_kind == "skeleton":
                        skeleton_row_selected = True
                    else:
                        raw_owner = str(current_item.data(QtCore.Qt.UserRole) or "").strip()
                        if raw_owner:
                            owner_name = raw_owner
            except Exception:
                pass
        if preview_context is None and owner_name is None and not skeleton_row_selected:
            try:
                raw_owner = str(getattr(gv, "_xform_gizmo_owner", "") or "").strip()
                if raw_owner:
                    owner_name = raw_owner
            except Exception:
                owner_name = None
        try:
            if (
                preview_context is None
                and owner_name
                and str(getattr(gv, "_timeline_mode", "composition") or "composition").strip().lower() == "composition"
            ):
                select_fn = getattr(gv, "_timeline_select_composition_owner", None)
                if callable(select_fn):
                    select_fn(owner_name)
                owner_name = None
        except Exception:
            pass
        effective_apply_current_frame = bool(apply_current_frame)
        if effective_apply_current_frame and owner_name:
            try:
                mode = str(getattr(gv, "_camera_select_mode", "default") or "default").strip()
                camera_live = (
                    bool(getattr(gv, "_camera_select_lock_enabled", False))
                    or bool(getattr(gv, "_fly_mode_enabled", False))
                    or bool(getattr(gv, "_fps_camera_active", False))
                )
                if mode and mode.lower() == str(owner_name).strip().lower() and bool(camera_live):
                    effective_apply_current_frame = False
                    log_fn = getattr(gv, "_timeline_scene_view_log", None)
                    if callable(log_fn):
                        log_fn(
                            "timeline_context_preserve_live_camera "
                            f"owner={str(owner_name)!r} mode={mode!r} "
                            f"fly={bool(getattr(gv, '_fly_mode_enabled', False))} "
                            f"lock={bool(getattr(gv, '_camera_select_lock_enabled', False))} "
                            f"fps_active={bool(getattr(gv, '_fps_camera_active', False))}",
                            throttle_key=f"timeline_context_preserve:{str(owner_name).strip()}",
                            interval=0.05,
                        )
                    debug_fn = getattr(gv, "_timeline_camera_key_debug_log", None)
                    if callable(debug_fn):
                        debug_fn(
                            "timeline_context_preserve_live_camera",
                            owner=str(owner_name),
                            frame=gv._timeline_current_frame() if hasattr(gv, "_timeline_current_frame") else None,
                            extra={
                                "mode": mode,
                                "requested_apply_current_frame": bool(apply_current_frame),
                                "effective_apply_current_frame": bool(effective_apply_current_frame),
                                "load_audio": bool(load_audio),
                            },
                        )
            except Exception:
                effective_apply_current_frame = bool(apply_current_frame)
        try:
            setter(
                scene_name=scene_name or None,
                project_path=project_path,
                owner_name=owner_name or None,
                apply_current_frame=bool(effective_apply_current_frame),
                load_audio=bool(load_audio),
            )
        except TypeError:
            try:
                setter(
                    scene_name=scene_name or None,
                    project_path=project_path,
                    owner_name=owner_name or None,
                )
            except Exception:
                pass
        except Exception:
            pass

    def set_timeline_menu_active(self, active: bool) -> None:
        btn = self._menu_button()
        if btn is None:
            return
        want = bool(active)
        try:
            btn.setProperty("active", bool(want))
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.update()
        except Exception:
            pass

    def sync_timeline_menu_state(self) -> None:
        timeline_btn = self._toggle_button()
        audio_btn = self._audio_button()
        if timeline_btn is not None:
            enabled = self.timeline_panel_enabled()
            try:
                timeline_btn.blockSignals(True)
                timeline_btn.setChecked(bool(enabled))
            except Exception:
                pass
            finally:
                try:
                    timeline_btn.blockSignals(False)
                except Exception:
                    pass
        if audio_btn is not None:
            enabled = self.audio_panel_enabled()
            try:
                audio_btn.blockSignals(True)
                audio_btn.setChecked(bool(enabled))
            except Exception:
                pass
            finally:
                try:
                    audio_btn.blockSignals(False)
                except Exception:
                    pass
        self.set_timeline_menu_active(self._menu_visible())

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
            if not want:
                try:
                    set_audio_visible = getattr(gv, "set_timeline_audio_visible", None)
                    if callable(set_audio_visible):
                        set_audio_visible(False)
                except Exception:
                    pass
        self.sync_timeline_menu_state()
        self._notify_panel_layout_changed()
        try:
            self._close_menu()
        except Exception:
            pass
        try:
            QtCore.QTimer.singleShot(0, win._reset_ui_cursor_arrow)
        except Exception:
            try:
                win._reset_ui_cursor_arrow()
            except Exception:
                pass

    def toggle_audio_panel_from_menu(self, checked: bool) -> None:
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
                if want:
                    set_timeline_visible = getattr(gv, "set_timeline_visible", None)
                    if callable(set_timeline_visible):
                        set_timeline_visible(True)
            except Exception:
                pass
            try:
                set_visible = getattr(gv, "set_timeline_audio_visible", None)
                if callable(set_visible):
                    set_visible(want)
            except Exception:
                pass
        self.sync_timeline_menu_state()
        self._notify_panel_layout_changed()
        try:
            self._close_menu()
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
        # Set Key must capture the current live transform. Applying the current
        # timeline frame here can overwrite a moved locked camera before capture.
        self.sync_timeline_context(apply_current_frame=False, load_audio=False)
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
