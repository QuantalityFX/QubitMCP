from __future__ import annotations

import time

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore


class TurntableController(QtCore.QObject):
    def __init__(self, view):
        super().__init__(view)
        self._view = view
        self._axis = None
        self._axis_checks = {}
        self._panel = None
        self._timer = None
        self._last_t = None
        self._speed_deg = 45.0

    def build_panel(self, parent: QtWidgets.QWidget) -> QtWidgets.QFrame:
        if self._panel is not None:
            return self._panel
        panel = QtWidgets.QFrame(parent)
        panel.setObjectName("TurntablePanel")
        panel.setStyleSheet(
            "#TurntablePanel{background:transparent;}"
            "#TurntablePanel QCheckBox{font-size:10px;}"
            "#TurntablePanel QToolButton{padding:2px 6px;font-size:10px;}"
        )
        layout = QtWidgets.QHBoxLayout(panel)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(6)

        axis_colors = {
            "x": "#ff0000",
            "y": "#00ff00",
            "z": "#0000ff",
        }
        for label in ("X", "Y", "Z"):
            cb = QtWidgets.QCheckBox(label, panel)
            axis_color = axis_colors[label.lower()]
            cb.setStyleSheet(
                "QCheckBox { color: " + axis_color + "; }"
                "QCheckBox::indicator {"
                " width: 12px; height: 12px;"
                " border: 1px solid " + axis_color + ";"
                " background: transparent;"
                "}"
                "QCheckBox::indicator:checked {"
                " background-color: " + axis_color + ";"
                "}"
            )
            cb.stateChanged.connect(lambda state, axis=label.lower(): self._on_axis_toggle(axis, state))
            layout.addWidget(cb)
            self._axis_checks[label.lower()] = cb

        reset_btn = QtWidgets.QToolButton(panel)
        reset_btn.setText("Reset")
        reset_btn.setCursor(QtCore.Qt.PointingHandCursor)
        reset_btn.clicked.connect(self._reset_rotation)
        layout.addWidget(reset_btn)

        copy_btn = QtWidgets.QToolButton(panel)
        copy_btn.setText("Copy")
        copy_btn.setCursor(QtCore.Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_rotation)
        layout.addWidget(copy_btn)

        self._panel = panel
        self._start_timer()
        return panel

    def _start_timer(self) -> None:
        if self._timer is not None:
            return
        timer = QtCore.QTimer(self)
        timer.setInterval(16)
        timer.timeout.connect(self._tick)
        timer.start()
        self._timer = timer
        self._last_t = time.perf_counter()

    def _target(self):
        owner = getattr(self._view, "_xform_gizmo_owner", None)
        if not owner:
            return None, False, {}
        try:
            rot, is_splat = self._view._get_owner_rot_deg(owner)
        except Exception:
            rot, is_splat = (0.0, 0.0, 0.0), False
        return owner, bool(is_splat), {"rot": rot}

    def _on_axis_toggle(self, axis: str, state: int) -> None:
        if state:
            for other, cb in self._axis_checks.items():
                if other != axis and cb.isChecked():
                    cb.blockSignals(True)
                    cb.setChecked(False)
                    cb.blockSignals(False)
            self._axis = axis
            self._last_t = time.perf_counter()
            try:
                self._view._xform_gizmo_mode = "rotate"
            except Exception:
                pass
        else:
            if self._axis == axis:
                self._axis = None
                self._last_t = time.perf_counter()
        try:
            self._view.update()
        except Exception:
            pass

    def _reset_rotation(self) -> None:
        owner, is_splat, _xf = self._target()
        if not owner:
            return
        fn = getattr(self._view, "_mgl_set_scene_asset_xform", None)
        if not callable(fn):
            return
        fn(
            owner,
            rot=(0.0, 0.0, 0.0),
            apply_to_scene_models=not is_splat,
            use_splat_xform=bool(is_splat),
        )
        try:
            w = self._view.window()
            if hasattr(w, "update_scene_asset_xform"):
                w.update_scene_asset_xform(owner)
        except Exception:
            pass
        self._view.update()

    def _copy_rotation(self) -> None:
        owner, _is_splat, xf = self._target()
        if not owner:
            return
        rot = tuple((xf or {}).get("rot", (0.0, 0.0, 0.0)))
        rx, ry, rz = float(rot[0]), float(rot[1]), float(rot[2])
        text = f"RX={rx:+.1f} RY={ry:+.1f} RZ={rz:+.1f}"
        try:
            QtWidgets.QApplication.clipboard().setText(text)
        except Exception:
            pass

    def _tick(self) -> None:
        axis = self._axis
        if not axis:
            return
        if getattr(self._view, "_xform_rotate_dragging", False) or getattr(self._view, "_xform_dragging", False):
            return
        if getattr(self._view, "_mgl_orbit_dragging", False) or getattr(self._view, "_orbit_dragging", False):
            return
        if getattr(self._view, "_pan_dragging", False) or getattr(self._view, "_dolly_dragging", False):
            return

        owner, is_splat, xf = self._target()
        if not owner:
            return

        now = time.perf_counter()
        last = self._last_t or now
        dt = max(0.0, min(0.05, now - last))
        self._last_t = now
        if dt <= 0.0:
            return

        rot = tuple((xf or {}).get("rot", (0.0, 0.0, 0.0)))
        rx0, ry0, rz0 = float(rot[0]), float(rot[1]), float(rot[2])

        q0 = self._view._rot_shared_q_from_euler_deg((rx0, ry0, rz0))
        if axis == "x":
            axis_vec = QtGui.QVector3D(1.0, 0.0, 0.0)
        elif axis == "y":
            axis_vec = QtGui.QVector3D(0.0, 1.0, 0.0)
        else:
            axis_vec = QtGui.QVector3D(0.0, 0.0, 1.0)

        delta_deg = float(self._speed_deg) * dt
        q_inc = QtGui.QQuaternion.fromAxisAndAngle(axis_vec, delta_deg)
        try:
            if hasattr(q_inc, "normalized"):
                q_inc = q_inc.normalized()
        except Exception:
            pass

        q_new = q_inc * q0
        rx, ry, rz = self._view._rot_shared_euler_deg_from_q_continuous(q_new, (rx0, ry0, rz0))

        fn = getattr(self._view, "_mgl_set_scene_asset_xform", None)
        if not callable(fn):
            return
        fn(
            owner,
            rot=(rx, ry, rz),
            apply_to_scene_models=not is_splat,
            use_splat_xform=bool(is_splat),
        )
        try:
            w = self._view.window()
            if hasattr(w, "update_scene_asset_xform"):
                w.update_scene_asset_xform(owner)
        except Exception:
            pass
        self._view.update()
