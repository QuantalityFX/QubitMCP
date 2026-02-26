from __future__ import annotations

from echograph.qt_compat import QtCore, QtWidgets


def _controller_method(window, method_name: str):
    try:
        controller = getattr(window, "_timeline_controller", None)
        method = getattr(controller, method_name, None) if controller is not None else None
        if callable(method):
            return method
    except Exception:
        pass
    return None


def _window_method(window, method_name: str):
    try:
        method = getattr(window, method_name, None)
        if callable(method):
            return method
    except Exception:
        pass
    return None


def _timeline_panel_enabled(window) -> bool:
    method = _controller_method(window, "timeline_panel_enabled")
    if method is not None:
        try:
            return bool(method())
        except Exception:
            return False
    method = _window_method(window, "_timeline_panel_enabled")
    if method is not None:
        try:
            return bool(method())
        except Exception:
            return False
    return False


def _toggle_timeline_panel(window, checked: bool) -> None:
    method = _controller_method(window, "toggle_timeline_panel_from_menu")
    if method is not None:
        try:
            method(bool(checked))
        except Exception:
            pass
        return
    method = _window_method(window, "_toggle_timeline_panel_from_menu")
    if method is not None:
        try:
            method(bool(checked))
        except Exception:
            pass


def _sync_timeline_menu_state(window) -> None:
    method = _controller_method(window, "sync_timeline_menu_state")
    if method is not None:
        try:
            method()
        except Exception:
            pass
        return
    method = _window_method(window, "_sync_timeline_menu_state")
    if method is not None:
        try:
            method()
        except Exception:
            pass


def _set_timeline_menu_active(window, active: bool) -> None:
    method = _controller_method(window, "set_timeline_menu_active")
    if method is not None:
        try:
            method(bool(active))
        except Exception:
            pass
        return
    method = _window_method(window, "_set_timeline_menu_active")
    if method is not None:
        try:
            method(bool(active))
        except Exception:
            pass


def build_timeline_panels_menu(window, bar, layout) -> tuple[QtWidgets.QToolButton, QtWidgets.QPushButton]:
    timeline_btn = QtWidgets.QToolButton(bar)
    timeline_btn.setObjectName("PanelsButton")
    timeline_btn.setText("Panels")
    timeline_btn.setCursor(QtCore.Qt.PointingHandCursor)
    timeline_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
    timeline_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
    timeline_btn.setFixedHeight(22)
    timeline_btn.setStyleSheet(
        "QToolButton#PanelsButton{border-radius:2px;text-align:center;}"
    )

    timeline_menu = QtWidgets.QMenu(timeline_btn)
    timeline_menu.setObjectName("PanelsMenu")
    timeline_menu.setStyleSheet(
        "#PanelsMenu{background:#1b2026;border:1px solid #333;padding:0px;}"
    )

    timeline_panel = QtWidgets.QFrame(timeline_menu)
    timeline_panel.setObjectName("PanelsPanel")
    timeline_panel.setFixedWidth(96)
    timeline_panel.setStyleSheet(
        "#PanelsPanel{background:#1b2026;border:0px;border-radius:6px;}"
        "#PanelsPanel QPushButton{color:#e5e7eb;background:transparent;border:0px;padding:0px 6px;text-align:left;}"
        "#PanelsPanel QPushButton:hover{color:#e5e7eb;background:#1f7a45;}"
    )
    timeline_layout = QtWidgets.QVBoxLayout(timeline_panel)
    timeline_layout.setContentsMargins(0, 0, 0, 0)
    timeline_layout.setSpacing(0)

    timeline_toggle = QtWidgets.QPushButton("Timeline", timeline_panel)
    timeline_toggle.setToolTip("Show timeline panel in viewport")
    timeline_toggle.setFixedHeight(22)
    timeline_toggle.setCursor(QtCore.Qt.PointingHandCursor)
    timeline_toggle.setFlat(True)
    timeline_toggle.setCheckable(True)
    timeline_toggle.setChecked(_timeline_panel_enabled(window))
    timeline_toggle.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    timeline_toggle.clicked.connect(lambda checked=False: _toggle_timeline_panel(window, bool(checked)))
    timeline_layout.addWidget(timeline_toggle, 0)

    timeline_action = QtWidgets.QWidgetAction(timeline_menu)
    timeline_action.setDefaultWidget(timeline_panel)
    timeline_menu.addAction(timeline_action)

    timeline_menu.aboutToShow.connect(lambda: _sync_timeline_menu_state(window))
    timeline_menu.aboutToShow.connect(lambda: _set_timeline_menu_active(window, True))
    timeline_menu.aboutToHide.connect(lambda: _set_timeline_menu_active(window, False))
    timeline_btn.setMenu(timeline_menu)
    layout.addWidget(timeline_btn, 0)
    return timeline_btn, timeline_toggle
