# echograph/gizmo/rotate_gizmo_shared.py
from __future__ import annotations

import math
from dataclasses import dataclass

try:
    from PySide6 import QtCore, QtGui
except Exception:
    from PySide2 import QtCore, QtGui  # type: ignore


def v3(x: float, y: float, z: float) -> QtGui.QVector3D:
    return QtGui.QVector3D(float(x), float(y), float(z))


def v3_dot(a: QtGui.QVector3D, b: QtGui.QVector3D) -> float:
    return float(QtGui.QVector3D.dotProduct(a, b))


def v3_cross(a: QtGui.QVector3D, b: QtGui.QVector3D) -> QtGui.QVector3D:
    return QtGui.QVector3D.crossProduct(a, b)


def v3_norm(a: QtGui.QVector3D) -> float:
    return float(a.length())


def v3_normalized(a: QtGui.QVector3D) -> QtGui.QVector3D:
    n = v3_norm(a)
    if n <= 1e-12:
        return QtGui.QVector3D(0.0, 0.0, 0.0)
    return a / n


def quat_from_two_vectors(a: QtGui.QVector3D, b: QtGui.QVector3D) -> QtGui.QQuaternion:
    a = v3_normalized(a)
    b = v3_normalized(b)

    d = max(-1.0, min(1.0, v3_dot(a, b)))
    if d > 0.999999:
        return QtGui.QQuaternion()  # identity

    if d < -0.999999:
        ortho = v3_cross(a, v3(1.0, 0.0, 0.0))
        if v3_norm(ortho) < 1e-6:
            ortho = v3_cross(a, v3(0.0, 1.0, 0.0))
        ortho = v3_normalized(ortho)
        return QtGui.QQuaternion.fromAxisAndAngle(ortho, 180.0)

    axis = v3_cross(a, b)
    w = 1.0 + d
    q = QtGui.QQuaternion(float(w), float(axis.x()), float(axis.y()), float(axis.z()))
    return q.normalized()


@dataclass
class DragArcball:
    active: bool = False
    start_vec_world: QtGui.QVector3D | None = None
    start_rot: QtGui.QQuaternion | None = None
    center_px: QtCore.QPointF | None = None
    radius_px: float = 1.0


@dataclass
class DragAxis:
    active: bool = False
    axis: str | None = None
    start_rot: QtGui.QQuaternion | None = None
    axis_world: QtGui.QVector3D | None = None
    start_dir: QtGui.QVector3D | None = None


class RotateGizmoShared:
    """
    Shared 2D overlay rendering helpers copied from im3d_guizm_control_smoketest.
    This keeps the look identical (crisp 2px rings, halo, view ring, center disc).
    Interaction/state machine comes next step.
    """

    def __init__(self) -> None:
        self.gizmo_radius: float = 0.9
        self.gizmo_screen_radius_px: float = 110.0
        self.gizmo_ui_scale: float = 1.3
        self.view_ring_scale: float = 1.2
        self.view_ring_core_color: QtGui.QColor = QtGui.QColor(120, 200, 255, 220)
        self.hover_view_ring = False
        self.drag_view = False
        self._view_drag_last_dir: QtCore.QPointF | None = None
        self.hover_axis: str | None = None
        self.drag_axis = DragAxis()

    def xyz_ring_radius_px(self) -> float:
        return float(self.gizmo_screen_radius_px) * float(self.gizmo_ui_scale)

    def view_ring_radius_px(self) -> float:
        return float(self.gizmo_screen_radius_px) * float(self.gizmo_ui_scale) * float(self.view_ring_scale)

    def project_to_screen(self, viewport_w: int, viewport_h: int, mvp: QtGui.QMatrix4x4, p: QtGui.QVector3D) -> QtCore.QPointF | None:
        x = float(p.x())
        y = float(p.y())
        z = float(p.z())

        r3 = mvp.row(3)  # QVector4D
        clip_w = float(r3.x()) * x + float(r3.y()) * y + float(r3.z()) * z + float(r3.w()) * 1.0
        if abs(clip_w) < 1e-9:
            return None

        v = mvp.map(p)  # perspective-divided
        ndc_x = float(v.x())
        ndc_y = float(v.y())

        w = float(max(1, int(viewport_w)))
        h = float(max(1, int(viewport_h)))

        sx = (ndc_x * 0.5 + 0.5) * w
        sy = (1.0 - (ndc_y * 0.5 + 0.5)) * h
        return QtCore.QPointF(sx, sy)

    def ring_clip_discard(
        self,
        p_local: QtGui.QVector3D,
        axis_vec: QtGui.QVector3D,
        view_dir_local: QtGui.QVector3D,
        back_clip_cos: float,
    ) -> bool:
        proj = view_dir_local - axis_vec * QtGui.QVector3D.dotProduct(view_dir_local, axis_vec)
        proj_len = proj.length()

        start = 0.35
        end = 0.75
        if proj_len <= 1e-6:
            return False

        t = (proj_len - start) / (end - start)
        if t <= 0.0:
            return False
        if t >= 1.0:
            t = 1.0

        proj /= proj_len

        ring_dir = p_local - axis_vec * QtGui.QVector3D.dotProduct(p_local, axis_vec)
        ring_len = ring_dir.length()
        if ring_len <= 1e-6:
            return False
        ring_dir /= ring_len

        d = QtGui.QVector3D.dotProduct(ring_dir, proj)
        thresh = (-1.0) * (1.0 - t) + float(back_clip_cos) * t
        return d < thresh

    def draw_view_ring_2d(self, widget, center: QtCore.QPointF, hovered: bool) -> None:
        r = float(self.view_ring_radius_px())

        painter = QtGui.QPainter(widget)
        if not painter.isActive():
            return
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        rect = QtCore.QRectF(float(center.x() - r), float(center.y() - r), float(2.0 * r), float(2.0 * r))

        if hovered:
            for width, alpha in ((12, 18), (8, 28)):
                pen = QtGui.QPen(QtGui.QColor(255, 255, 255, alpha))
                pen.setWidth(int(width))
                pen.setCapStyle(QtCore.Qt.RoundCap)
                pen.setJoinStyle(QtCore.Qt.RoundJoin)
                painter.setPen(pen)
                painter.drawEllipse(rect)

        core_col = self.view_ring_core_color
        pen = QtGui.QPen(core_col)
        pen.setWidth(2)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawEllipse(rect)

        painter.end()

    def draw_center_disc_2d(self, widget, center: QtCore.QPointF, radius_px: float, hovered: bool) -> None:
        painter = QtGui.QPainter(widget)
        if not painter.isActive():
            return
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        r = float(radius_px)
        rect = QtCore.QRectF(float(center.x() - r), float(center.y() - r), float(2.0 * r), float(2.0 * r))

        base_a = 55
        hover_a = 70
        a = hover_a if hovered else base_a

        col = QtGui.QColor(35, 35, 35, a)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(col))
        painter.drawEllipse(rect)

        dot_r = 2.0
        dot_rect = QtCore.QRectF(
            float(center.x() - dot_r),
            float(center.y() - dot_r),
            float(dot_r * 2.0),
            float(dot_r * 2.0),
        )
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 180)))
        painter.drawEllipse(dot_rect)

        painter.end()

    def draw_hover_halo_2d(
        self,
        widget,
        axis: str,
        viewport_w: int,
        viewport_h: int,
        mvp: QtGui.QMatrix4x4,
        view_dir_local: QtGui.QVector3D,
        back_clip_cos: float,
        clip_enabled: bool,
    ) -> None:
        r = float(self.gizmo_radius)
        steps = 128

        if axis == "x":
            axis_vec = v3(1.0, 0.0, 0.0)

            def make_p(a: float) -> QtGui.QVector3D:
                return v3(0.0, r * math.cos(a), r * math.sin(a))

        elif axis == "y":
            axis_vec = v3(0.0, 1.0, 0.0)

            def make_p(a: float) -> QtGui.QVector3D:
                return v3(r * math.cos(a), 0.0, r * math.sin(a))

        else:
            axis_vec = v3(0.0, 0.0, 1.0)

            def make_p(a: float) -> QtGui.QVector3D:
                return v3(r * math.cos(a), r * math.sin(a), 0.0)

        segments: list[list[QtCore.QPointF]] = []
        seg: list[QtCore.QPointF] = []

        for k in range(steps + 1):
            a = (2.0 * math.pi) * (k / steps)
            p = make_p(a)

            if clip_enabled and self.ring_clip_discard(p, axis_vec, view_dir_local, back_clip_cos):
                if len(seg) >= 2:
                    segments.append(seg)
                seg = []
                continue

            sp = self.project_to_screen(viewport_w, viewport_h, mvp, p)
            if sp is None:
                if len(seg) >= 2:
                    segments.append(seg)
                seg = []
                continue

            seg.append(sp)

        if len(seg) >= 2:
            segments.append(seg)

        if not segments:
            return

        painter = QtGui.QPainter(widget)
        if not painter.isActive():
            return
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        if axis == "x":
            base = (255, 0, 0)
        elif axis == "y":
            base = (0, 255, 0)
        else:
            base = (70, 120, 255)

        for width, alpha in ((10, 35), (6, 55)):
            c = QtGui.QColor(base[0], base[1], base[2], alpha)
            pen = QtGui.QPen(c)
            pen.setWidth(int(width))
            pen.setCapStyle(QtCore.Qt.RoundCap)
            pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(pen)

            for pts in segments:
                painter.drawPolyline(QtGui.QPolygonF(pts))

        painter.end()

    def draw_xyz_core_2d(
        self,
        widget,
        viewport_w: int,
        viewport_h: int,
        mvp: QtGui.QMatrix4x4,
        view_dir_local: QtGui.QVector3D,
        back_clip_cos: float,
        clip_enabled: bool,
        width_px: int = 2,
    ) -> None:
        r = float(self.gizmo_radius)
        steps = 128

        painter = QtGui.QPainter(widget)
        if not painter.isActive():
            return
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        pen = QtGui.QPen()
        pen.setWidth(int(width_px))
        pen.setCosmetic(True)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)

        def draw_axis(axis: str, color: QtGui.QColor) -> None:
            if axis == "x":
                axis_vec = v3(1.0, 0.0, 0.0)

                def make_p(a: float) -> QtGui.QVector3D:
                    return v3(0.0, r * math.cos(a), r * math.sin(a))

            elif axis == "y":
                axis_vec = v3(0.0, 1.0, 0.0)

                def make_p(a: float) -> QtGui.QVector3D:
                    return v3(r * math.cos(a), 0.0, r * math.sin(a))

            else:
                axis_vec = v3(0.0, 0.0, 1.0)

                def make_p(a: float) -> QtGui.QVector3D:
                    return v3(r * math.cos(a), r * math.sin(a), 0.0)

            segments: list[list[QtCore.QPointF]] = []
            seg: list[QtCore.QPointF] = []

            for k in range(steps + 1):
                a = (2.0 * math.pi) * (k / steps)
                p = make_p(a)

                if clip_enabled and self.ring_clip_discard(p, axis_vec, view_dir_local, back_clip_cos):
                    if len(seg) >= 2:
                        segments.append(seg)
                    seg = []
                    continue

                sp = self.project_to_screen(viewport_w, viewport_h, mvp, p)
                if sp is None:
                    if len(seg) >= 2:
                        segments.append(seg)
                    seg = []
                    continue

                seg.append(sp)

            if len(seg) >= 2:
                segments.append(seg)

            if not segments:
                return

            pen.setColor(color)
            painter.setPen(pen)
            for pts in segments:
                painter.drawPolyline(QtGui.QPolygonF(pts))

        draw_axis("x", QtGui.QColor(255, 0, 0, 255))
        draw_axis("y", QtGui.QColor(0, 255, 0, 255))
        draw_axis("z", QtGui.QColor(0, 0, 255, 255))

        painter.end()

    def pick_hover_view_ring(self, mouse_px: QtCore.QPointF, center_px: QtCore.QPointF) -> bool:
        mx = float(mouse_px.x())
        my = float(mouse_px.y())
        cx = float(center_px.x())
        cy = float(center_px.y())

        r = float(self.view_ring_radius_px())
        d = math.hypot(mx - cx, my - cy)

        band = max(10.0, r * 0.12)
        return abs(d - r) <= float(band)

    def angle_on_ring(self, center_px: QtCore.QPointF, mouse_px: QtCore.QPointF) -> float:
        return float(math.atan2(float(mouse_px.y() - center_px.y()), float(mouse_px.x() - center_px.x())))
    
    def pick_axis_2d(
        self,
        widget,
        center: QtCore.QPointF,
        mouse_px: QtCore.QPointF,
        *,
        viewport_w: int | None = None,
        viewport_h: int | None = None,
        mvp: QtGui.QMatrix4x4 | None = None,
        view_dir_local: QtGui.QVector3D | None = None,
        back_clip_cos: float = -0.25,
        clip_enabled: bool = False,
        threshold_px: float = 14.0,
    ) -> str | None:


        # 2) pull viewport and MVP
        if viewport_w is None:
            viewport_w = int(widget.width())
        if viewport_h is None:
            viewport_h = int(widget.height())
        if mvp is None:
            mvp = getattr(widget, "_rot_shared_mvp", None)
        if mvp is None:
            return None

        mx = float(mouse_px.x())
        my = float(mouse_px.y())

        def dist_pt_seg(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
            vx = bx - ax
            vy = by - ay
            wx = px - ax
            wy = py - ay
            denom = vx * vx + vy * vy
            if denom <= 1e-12:
                return math.hypot(px - ax, py - ay)
            t = (wx * vx + wy * vy) / denom
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            cx = ax + t * vx
            cy = ay + t * vy
            return math.hypot(px - cx, py - cy)

        r = float(self.gizmo_radius)
        steps = 128

        best_axis: str | None = None
        best_d = 1e30

        for axis in ("x", "y", "z"):
            if axis == "x":
                axis_vec = v3(1.0, 0.0, 0.0)
                def make_p(a: float) -> QtGui.QVector3D:
                    return v3(0.0, r * math.cos(a), r * math.sin(a))
            elif axis == "y":
                axis_vec = v3(0.0, 1.0, 0.0)
                def make_p(a: float) -> QtGui.QVector3D:
                    return v3(r * math.cos(a), 0.0, r * math.sin(a))
            else:
                axis_vec = v3(0.0, 0.0, 1.0)
                def make_p(a: float) -> QtGui.QVector3D:
                    return v3(r * math.cos(a), r * math.sin(a), 0.0)

            prev: QtCore.QPointF | None = None
            for k in range(steps + 1):
                a = (2.0 * math.pi) * (k / steps)
                p = make_p(a)

                if clip_enabled and (view_dir_local is not None) and self.ring_clip_discard(p, axis_vec, view_dir_local, float(back_clip_cos)):
                    prev = None
                    continue

                sp = self.project_to_screen(int(viewport_w), int(viewport_h), mvp, p)
                if sp is None:
                    prev = None
                    continue

                if prev is not None:
                    d = dist_pt_seg(mx, my, float(prev.x()), float(prev.y()), float(sp.x()), float(sp.y()))
                    if d < best_d:
                        best_d = d
                        best_axis = axis

                prev = sp

        if best_axis is not None and best_d <= float(threshold_px):
            return best_axis

        # view ring fallback (only if we didn't hit xyz)
        if self.pick_hover_view_ring(mouse_px, center):
            return "view"

        return None

    def begin_view_ring_drag(self) -> None:
        self.drag_view = True
        self._view_drag_last_dir = None


    def end_view_ring_drag(self) -> None:
        self.drag_view = False
        self._view_drag_last_dir = None
        self.hover_view_ring = False


    def update_view_ring_drag(
        self,
        mouse_px: QtCore.QPointF,
        center_px: QtCore.QPointF,
        forward_world: QtGui.QVector3D,
        obj_rot: QtGui.QQuaternion,
    ) -> QtGui.QQuaternion:
        vx = float(mouse_px.x() - center_px.x())
        vy = float(mouse_px.y() - center_px.y())
        l = math.hypot(vx, vy)
        if l <= 1e-6:
            return obj_rot

        cur_dir = QtCore.QPointF(vx / l, vy / l)

        prev = self._view_drag_last_dir
        if prev is None:
            self._view_drag_last_dir = cur_dir
            return obj_rot

        cross = float(prev.x() * cur_dir.y() - prev.y() * cur_dir.x())
        dot = float(prev.x() * cur_dir.x() + prev.y() * cur_dir.y())
        ang_deg = math.degrees(math.atan2(cross, dot))

        self._view_drag_last_dir = cur_dir

        q = QtGui.QQuaternion.fromAxisAndAngle(forward_world, float(ang_deg))
        return (q * obj_rot).normalized()        
    
    def begin_axis_drag(self, axis: str, start_rot: QtGui.QQuaternion, axis_world: QtGui.QVector3D, start_dir: QtGui.QVector3D) -> None:
        self.drag_axis.active = True
        self.drag_axis.axis = axis
        self.drag_axis.start_rot = QtGui.QQuaternion(float(start_rot.scalar()), float(start_rot.x()), float(start_rot.y()), float(start_rot.z()))
        self.drag_axis.axis_world = axis_world
        self.drag_axis.start_dir = start_dir

    def end_axis_drag(self) -> None:
        self.drag_axis.active = False
        self.drag_axis.axis = None
        self.drag_axis.start_rot = None
        self.drag_axis.axis_world = None
        self.drag_axis.start_dir = None
        self.hover_axis = None

    def update_axis_drag(self, cur_dir: QtGui.QVector3D) -> QtGui.QQuaternion | None:
        if not self.drag_axis.active:
            return None
        if self.drag_axis.start_rot is None or self.drag_axis.axis_world is None or self.drag_axis.start_dir is None:
            return None

        a = self.drag_axis.start_dir
        b = cur_dir
        if a.length() <= 1e-6 or b.length() <= 1e-6:
            return self.drag_axis.start_rot

        a = a / a.length()
        b = b / b.length()

        axis = self.drag_axis.axis_world
        if axis.length() > 1e-6:
            axis = axis / axis.length()

        # signed angle around axis
        cross = QtGui.QVector3D.crossProduct(a, b)
        s = float(QtGui.QVector3D.dotProduct(cross, axis))
        c = float(QtGui.QVector3D.dotProduct(a, b))
        ang = math.degrees(math.atan2(s, c))

        q = QtGui.QQuaternion.fromAxisAndAngle(axis, float(ang))
        return (q * self.drag_axis.start_rot).normalized()