from __future__ import annotations

from echograph.qt_compat import QtCore, QtWidgets


def _controller(window):
    try:
        return getattr(window, "_timeline_controller", None)
    except Exception:
        pass
    return None


def _timeline_panel_enabled(window) -> bool:
    ctl = _controller(window)
    if ctl is not None:
        try:
            return bool(ctl.timeline_panel_enabled())
        except Exception:
            return False
    return False


def _toggle_timeline_panel(window, checked: bool) -> None:
    ctl = _controller(window)
    if ctl is not None:
        try:
            ctl.toggle_timeline_panel_from_menu(bool(checked))
        except Exception:
            pass


def _sync_timeline_menu_state(window) -> None:
    ctl = _controller(window)
    if ctl is not None:
        try:
            ctl.sync_timeline_menu_state()
        except Exception:
            pass


def _set_timeline_menu_active(window, active: bool) -> None:
    ctl = _controller(window)
    if ctl is not None:
        try:
            ctl.set_timeline_menu_active(bool(active))
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
    ctl = _controller(window)
    if ctl is not None:
        try:
            ctl.bind_menu_widgets(timeline_btn, timeline_toggle)
        except Exception:
            pass
    return timeline_btn, timeline_toggle
