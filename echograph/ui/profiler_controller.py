from __future__ import annotations

from echograph.qt_compat import QtCore
from echograph.services.profiler import get_profiler
from echograph.ui.profiler_panel import ProfilerPanel


class ProfilerController:
    def __init__(self, window):
        self._window = window
        self._menu_button = None
        self._toggle_button = None
        self._panel = None
        self._session = get_profiler()

    def bind_menu_widgets(self, menu_button, toggle_button) -> None:
        self._menu_button = menu_button
        self._toggle_button = toggle_button

    def attach_panel_to_view(self) -> None:
        view = getattr(self._window, "view", None)
        if view is None:
            return
        panel = self._panel
        if panel is None:
            panel = ProfilerPanel(view, session=self._session)
            self._panel = panel
        setter = getattr(view, "set_profiler_panel", None)
        if callable(setter):
            setter(panel)

    def profiler_panel_enabled(self) -> bool:
        view = getattr(self._window, "view", None)
        if view is None:
            return False
        visible_fn = getattr(view, "profiler_visible", None)
        if callable(visible_fn):
            try:
                return bool(visible_fn())
            except Exception:
                return False
        return False

    def sync_profiler_menu_state(self) -> None:
        btn = self._toggle_button
        if btn is None:
            return
        try:
            btn.blockSignals(True)
            btn.setChecked(bool(self.profiler_panel_enabled()))
        except Exception:
            pass
        finally:
            try:
                btn.blockSignals(False)
            except Exception:
                pass

    def toggle_profiler_panel_from_menu(self, checked: bool) -> None:
        self.attach_panel_to_view()
        view = getattr(self._window, "view", None)
        if view is not None:
            setter = getattr(view, "set_profiler_visible", None)
            if callable(setter):
                try:
                    setter(bool(checked))
                except Exception:
                    pass
        self.sync_profiler_menu_state()
        hook = getattr(self._window, "_on_panel_layout_changed", None)
        if callable(hook):
            try:
                hook()
            except Exception:
                pass
        self._close_menu()
        try:
            QtCore.QTimer.singleShot(0, self._window._reset_ui_cursor_arrow)
        except Exception:
            try:
                self._window._reset_ui_cursor_arrow()
            except Exception:
                pass

    def _close_menu(self) -> None:
        btn = self._menu_button
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
