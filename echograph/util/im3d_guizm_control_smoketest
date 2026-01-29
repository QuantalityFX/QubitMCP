"""
im3d_guizm_control_smoketest.py

Purpose
- Self-contained smoke test for a rotation gizmo inside a QOpenGLWidget.
- Includes XYZ rotation rings that keep a constant on-screen size (pixel radius).
- Includes a camera-facing “view ring” (outer ring) for view-based rotation.
- Includes arcball rotation inside the ring radius, plus axis-constrained ring dragging.
- Includes 2D overlay drawing (QPainter) for crisp lines, halos, view-ring styling, center disc, and center dot.

How to run
  python echograph/util/im3d_guizm_control_smoketest.py

Viewport-only test scaffolding (safe to remove when porting)
- Grid + world axes geometry in initializeGL()
- Orbit camera state and Alt+mouse orbit/zoom controls
- The Clip Rings checkbox UI overlay and its QTimer update loop
- main() and QSurfaceFormat setup

Actual gizmo module logic (what you want to port/reuse)
- Gizmo state + knobs:
    self._obj_rot                 quaternion rotation being edited
    self._gizmo_radius            world radius used by ring math and geometry
    self._gizmo_screen_radius_px  base pixel radius when ui_scale = 1.0
    self._gizmo_ui_scale          overall UI scale knob (XYZ + white together)
    self._view_ring_scale         white ring relative scale vs XYZ rings
    self._view_ring_core_color    tint of the view ring core stroke

- Constant on-screen size math (core feature)
  In paintGL():
    target_ring_px = self._gizmo_screen_radius_px * self._gizmo_ui_scale
    ring_draw_scale = (target_ring_px * 2 * dist) / (viewport_h_px * proj_y * gizmo_radius)
  Rings draw with scaled MVP: mvp_rings = proj * view * (model * scale(ring_draw_scale))
  Cube draws unscaled with mvp_obj = proj * view * model

- Picking and interaction
  _project_to_screen()
  axis ring hover pick (screen-space sampling using mvp_rings)
  _pick_hover_view_ring()
  DragAxis and DragArcball state machines
  mousePressEvent/mouseMoveEvent/mouseReleaseEvent branches:
    * view ring drag
    * axis ring drag
    * arcball drag

- Rendering pieces
  GL:
    shaders VERT/FRAG + _ring_clip_discard() logic for back-half clip
  2D overlay (QPainter):
    _draw_xyz_core_2d()          crisp 2px ring cores (since GL line width clamps)
    _draw_hover_halo_2d()        colored halo for hovered ring
    _draw_view_ring_2d()         outer view ring core + hover glow
    _draw_center_disc_2d()       center disc + center dot
"""

# echograph/util/im3d_guizm_control_smoketest.py
# -----------------------------------------------------------------------------
# im3d_guizm_control_smoketest.py
#
# What this script is:
# A self-contained smoke test for a 3D rotation gizmo rendered in a QOpenGLWidget,
# with constant on-screen size rings (XYZ) and a separate camera-facing "view ring"
# (the white outer ring). It also includes interactive rotation controls:
#   1) Axis ring drag (X/Y/Z ring) for constrained rotation
#   2) View ring drag for camera-facing roll-style rotation
#   3) Arcball drag when clicking inside the gizmo radius for free rotation
#
# What it is NOT:
# This is not the production viewport. The grid, world axes, orbit camera, and UI
# checkbox exist only to test the gizmo in isolation.
#
# How to run:
#   python echograph/util/im3d_guizm_control_smoketest.py
#
# Controls (testing viewport only):
#   Alt + Left Mouse Drag   = orbit camera
#   Alt + Right Mouse Drag  = zoom (drag)
#   Left Mouse Drag         = interact with gizmo:
#       - if mouse is on outer view ring: rotate around camera forward axis
#       - else if mouse is on X/Y/Z ring: axis-constrained rotation
#       - else if mouse is inside gizmo radius: arcball rotation
#   "Clip Rings" checkbox   = toggles back-half ring clipping (fade + discard)
#
# Rendering overview:
#   - 3D GL pass draws:
#       * WORLD geometry (grid + small world axes) using _vao_world
#       * OBJECT geometry (XYZ rings + cube wireframe) using _vao_obj
#   - 2D QPainter overlay draws:
#       * crisp 2px XYZ ring cores (because GL line width can clamp to 1px)
#       * view ring (outer circle), glow only on hover/drag, tinted core stroke
#       * center disc + center point (visual origin / grab area)
#       * hover halo for selected axis ring
#
# Constant on-screen size (important feature to port):
#   - The XYZ rings are built in world units using self._gizmo_radius.
#   - In paintGL(), we compute ring_draw_scale so the rings keep a constant pixel
#     radius, independent of camera zoom/distance:
#       target_ring_px = self._gizmo_screen_radius_px * self._gizmo_ui_scale
#       ring_draw_scale = (target_ring_px * 2 * dist) / (viewport_h_px * proj_y * gizmo_radius)
#     Then we render rings with mvp_rings = proj * view * (model * scale(ring_draw_scale))
#     The cube is rendered UNscaled with mvp_obj (so it stays true to world scale).
#
# Size knobs (keep these when porting):
#   self._gizmo_radius            # world radius used by math and ring geometry
#   self._gizmo_screen_radius_px  # base pixel radius when ui_scale = 1.0
#   self._gizmo_ui_scale          # single overall knob, scales XYZ + white together
#   self._view_ring_scale         # white ring relative scale vs XYZ rings
#
# Styling knobs:
#   self._view_ring_core_color    # tint for crisp core stroke of the view ring
#   _draw_view_ring_2d():         # hover glow thickness/intensity lives here
#   _draw_center_disc_2d():       # center disc alpha/color + center dot lives here
#
# ----------------------------------------------------------------------------- 
# What to REMOVE when turning this into a reusable gizmo module:
#
# A) Testing viewport only (remove or ignore in production):
#   - Grid + world axes generation in initializeGL() (world_verts, w_add_line, grid_n, etc.)
#   - Orbit/zoom camera state and controls:
#       self._yaw, self._pitch, self._dist
#       _camera_world(), _camera_basis(), Alt+mouse orbit/zoom branches
#   - UI panel + checkbox overlay (self._panel, self._clip_check styling)
#   - QTimer driving update() at 60fps (you’ll likely use your app’s render loop)
#   - main() and QSurfaceFormat setup (your app already owns the GL context)
#
# B) Gizmo module logic (keep and integrate):
#   - Rotation state:
#       self._obj_rot (quaternion) and updates during drags
#       DragAxis and DragArcball dataclasses
#   - Ring back-half clipping logic:
#       FRAG shader logic + _ring_clip_discard()
#   - Constant on-screen size math for rings:
#       paintGL() block computing target_ring_px, ring_draw_scale, mvp_rings
#   - Picking logic that matches what is drawn:
#       _project_to_screen()
#       _pick_hover_axis() or the inline scaled-MVP ring_dist code in paintGL()
#       _pick_hover_view_ring()
#   - Interaction handlers:
#       mousePressEvent/mouseMoveEvent/mouseReleaseEvent branches for:
#         * view ring drag
#         * axis drag
#         * arcball drag
#       helper math:
#         quat_from_two_vectors()
#         _arcball_vec_world()
#         _ray_dir_world(), _axis_ring_dir_world()
#   - 2D overlay helpers for consistent visuals:
#       _draw_xyz_core_2d() (2px cosmetic)
#       _draw_hover_halo_2d()
#       _draw_view_ring_2d()
#       _draw_center_disc_2d()
#
# Integration tip for the main app:
#   The clean port is to extract the “Gizmo module logic” into a class that receives:
#     - current proj/view matrices (or camera parameters)
#     - current model matrix / object rotation reference
#     - viewport size + DPR
#     - mouse position + button/modifier events
#   Then the main viewport calls:
#     gizmo.update_hover(mouse_pos, proj, view, model)
#     gizmo.on_mouse_press/move/release(...)
#     gizmo.draw_gl(proj, view, model)      # rings (scaled MVP) + cube (unscaled) if desired
#     gizmo.draw_overlay_qpainter(...)      # view ring, halo, center disc, 2px cores
#
# -----------------------------------------------------------------------------

from __future__ import annotations

import math
import struct
import sys
from dataclasses import dataclass

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtOpenGL import (
        QOpenGLShader,
        QOpenGLShaderProgram,
        QOpenGLBuffer,
        QOpenGLVertexArrayObject,
    )
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
    from PySide2.QtGui import QSurfaceFormat  # type: ignore
    from PySide2.QtWidgets import QOpenGLWidget  # type: ignore
    from PySide2.QtOpenGL import (  # type: ignore
        QOpenGLShader,
        QOpenGLShaderProgram,
        QOpenGLBuffer,
        QOpenGLVertexArrayObject,
    )

# GL enum constants (no PyOpenGL dependency)
GL_FLOAT = 0x1406
GL_LINES = 0x0001
GL_DEPTH_TEST = 0x0B71
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100


VERT = """
#version 330
layout(location = 0) in vec3 in_pos;
layout(location = 1) in vec3 in_col;

uniform mat4 u_mvp;

out vec3 v_col;
out vec3 v_pos;

void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    v_col = in_col;
    v_pos = in_pos;
}
"""

# Same ring back-half clipping, with smooth fade-in (your improved version)
FRAG = """
#version 330
in vec3 v_col;
in vec3 v_pos;
out vec4 fragColor;

uniform vec3 u_clip;       // x: enabled
uniform vec3 u_view_dir;   // in same space as v_pos
uniform vec3 u_back_clip;  // x: threshold
uniform vec4 u_color; // rgb multiplier + alpha

void main() {
    if (u_clip.x > 0.5) {
        vec3 p = v_pos;

        vec3 axis;
        float eps = 1e-5;
        if (abs(p.x) < eps)      axis = vec3(1.0, 0.0, 0.0);
        else if (abs(p.y) < eps) axis = vec3(0.0, 1.0, 0.0);
        else                     axis = vec3(0.0, 0.0, 1.0);

        vec3 proj = u_view_dir - axis * dot(u_view_dir, axis);
        float proj_len = length(proj);

        float start = 0.35;
        float end   = 0.75;
        float t = clamp((proj_len - start) / (end - start), 0.0, 1.0);

        if (t > 0.0 && proj_len > 1e-6) {
            proj /= proj_len;

            vec3 ring_dir = p - axis * dot(p, axis);
            float ring_len = length(ring_dir);
            if (ring_len > 1e-4) {
                ring_dir /= ring_len;
                float d = dot(ring_dir, proj);

                float thresh = mix(-1.0, u_back_clip.x, t);
                if (d < thresh) discard;
            }
        }
    }

    vec3 col = v_col;
    fragColor = vec4(col * u_color.rgb, u_color.a);
}
"""


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
    # Returns quaternion rotating vector a -> b (both expected normalized)
    a = v3_normalized(a)
    b = v3_normalized(b)

    d = max(-1.0, min(1.0, v3_dot(a, b)))
    if d > 0.999999:
        return QtGui.QQuaternion()  # identity

    if d < -0.999999:
        # 180 deg: pick any orthogonal axis
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
    axis: str | None = None            # "x" | "y" | "z"
    start_rot: QtGui.QQuaternion | None = None
    axis_world: QtGui.QVector3D | None = None  # unit axis in WORLD space at drag start
    start_dir: QtGui.QVector3D | None = None   # unit direction on ring plane (WORLD) at drag start

class GizmoControlSmoke(QOpenGLWidget):
    def __init__(self) -> None:
        super().__init__()

        self._gl = None
        self._prog: QOpenGLShaderProgram | None = None

        # World geometry (grid, world axes)
        self._vbo_world: QOpenGLBuffer | None = None
        self._vao_world: QOpenGLVertexArrayObject | None = None
        self._world_count = 0

        # Object geometry (gizmo rings + cube)
        self._vbo_obj: QOpenGLBuffer | None = None
        self._vao_obj: QOpenGLVertexArrayObject | None = None
        self._obj_count = 0

        self._world_count = 0
        self._ring_count = 0
        
        # Camera orbit
        self._yaw = 0.6
        self._pitch = 0.35
        self._dist = 3.2
        self._orbiting = False
        self._zooming = False
        self._last_mouse = QtCore.QPointF(0.0, 0.0)
        self._mouse_pos = QtCore.QPointF(0.0, 0.0)
        self._hover_axis = None # "x" | "y" | "z" | None
        self._hover_view_ring = False
        self._drag_view = False
        self._view_drag_start_mouse = QtCore.QPointF(0.0, 0.0)
        self._view_drag_start_rot = QtGui.QQuaternion()
        self._view_drag_last_dir = None  # QPointF in screen-space (normalized), or None

        # Gizmo state
        self._gizmo_radius = 0.9 # world radius, used by the math
        self._gizmo_screen_radius_px = 110.0 # base pixel radius at ui_scale=1.0
        self._gizmo_ui_scale = 1.3 # ONE overall knob, scales XYZ + white together
        self._view_ring_scale = 1.2 # white relative to XYZ (independent)

        # View ring styling (core stroke tint only; glow stays white)
        self._view_ring_core_color = QtGui.QColor(120, 200, 255, 220)

        self._view_ring_radius_px = None # float | None
        self._ring_draw_scale = 1.0
        self._obj_rot = QtGui.QQuaternion()  # identity
        
        # Arcball drag (click anywhere inside gizmo radius)
        self._drag_arc = DragArcball()
        self._drag_axis = DragAxis()

        # Clip toggle
        self._clip_enabled = True
        self._clip_frac = 0.30  # 0..1

        self.setMinimumSize(1000, 650)
        self.setWindowTitle("Gizmo Control Smoke Test (arcball rotate + orbit/zoom)")

        # UI panel
        self._panel = QtWidgets.QWidget(self)
        lay = QtWidgets.QHBoxLayout(self._panel)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)

        self._clip_check = QtWidgets.QCheckBox("Clip Rings", self._panel)
        self._clip_check.setChecked(True)
        self._clip_check.toggled.connect(self._on_clip_toggle)
        lay.addWidget(self._clip_check)

        self._panel.setStyleSheet("""
        QWidget { background: rgba(0,0,0,140); }
        QCheckBox { color: white; font-size: 13px; }
        QCheckBox::indicator { width: 16px; height: 16px; }
        """)
        self._panel.adjustSize()
        self._panel.move(10, 10)

        self.setMouseTracking(True)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(16)
    def _on_clip_toggle(self, on: bool) -> None:
        self._clip_enabled = bool(on)
        self.update()

    def initializeGL(self) -> None:
        world: list[float] = []
        obj: list[float] = []
        ctx = self.context()
        self._gl = ctx.functions()
        self._gl.initializeOpenGLFunctions()

        self._prog = QOpenGLShaderProgram()
        self._prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT)
        self._prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG)
        self._prog.link()
        self._u_alpha_loc = self._prog.uniformLocation("u_alpha")

        # Build WORLD geometry: grid + tiny axes at origin
        world_verts: list[float] = []

        def w_add_line(p0, p1, col):
            r, g, b = col
            world_verts.extend([p0[0], p0[1], p0[2], r, g, b])
            world_verts.extend([p1[0], p1[1], p1[2], r, g, b])

        grid_n = 10
        grid_step = 0.25
        extent = grid_n * grid_step
        for i in range(-grid_n, grid_n + 1):
            x = i * grid_step
            z = i * grid_step
            col = (0.18, 0.18, 0.22)
            if i == 0:
                col = (0.25, 0.25, 0.32)
            w_add_line((x, 0.0, -extent), (x, 0.0, extent), col)
            w_add_line((-extent, 0.0, z), (extent, 0.0, z), col)

        # Small world axes
        w_add_line((0.0, 0.0, 0.0), (0.35, 0.0, 0.0), (0.9, 0.2, 0.2))
        w_add_line((0.0, 0.0, 0.0), (0.0, 0.35, 0.0), (0.2, 0.9, 0.2))
        w_add_line((0.0, 0.0, 0.0), (0.0, 0.0, 0.35), (0.2, 0.2, 0.9))

        # after you finish building the lists:
        self._world_count = len(world) // 6
        self._obj_count = len(obj) // 6

        # optional: if something later expects _ring_count
        self._ring_count = self._obj_count

        self._world_count = len(world_verts) // 6
        world_data = struct.pack(f"{len(world_verts)}f", *world_verts)

        self._vbo_world = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self._vbo_world.create()
        self._vbo_world.bind()
        self._vbo_world.allocate(world_data, len(world_data))

        self._vao_world = QOpenGLVertexArrayObject()
        self._vao_world.create()
        self._vao_world.bind()

        self._prog.bind()
        stride = 6 * 4
        self._prog.enableAttributeArray(0)
        self._prog.setAttributeBuffer(0, GL_FLOAT, 0, 3, stride)
        self._prog.enableAttributeArray(1)
        self._prog.setAttributeBuffer(1, GL_FLOAT, 3 * 4, 3, stride)

        self._vao_world.release()
        self._vbo_world.release()
        self._prog.release()

        # Build OBJECT geometry: rings + cube wireframe
        obj_verts: list[float] = []

        def o_add_line(p0, p1, col):
            r, g, b = col
            obj_verts.extend([p0[0], p0[1], p0[2], r, g, b])
            obj_verts.extend([p1[0], p1[1], p1[2], r, g, b])

        def add_circle(axis: str, radius: float, segments: int, col):
            r, g, b = col
            for i in range(segments):
                a0 = (i / segments) * (math.pi * 2.0)
                a1 = ((i + 1) / segments) * (math.pi * 2.0)

                if axis == "x":
                    p0 = (0.0, radius * math.cos(a0), radius * math.sin(a0))
                    p1 = (0.0, radius * math.cos(a1), radius * math.sin(a1))
                elif axis == "y":
                    p0 = (radius * math.cos(a0), 0.0, radius * math.sin(a0))
                    p1 = (radius * math.cos(a1), 0.0, radius * math.sin(a1))
                else:  # "z"
                    p0 = (radius * math.cos(a0), radius * math.sin(a0), 0.0)
                    p1 = (radius * math.cos(a1), radius * math.sin(a1), 0.0)

                obj_verts.extend([p0[0], p0[1], p0[2], r, g, b])
                obj_verts.extend([p1[0], p1[1], p1[2], r, g, b])

        segs = 96
        add_circle("x", self._gizmo_radius, segs, (1.0, 0.0, 0.0))
        add_circle("y", self._gizmo_radius, segs, (0.0, 1.0, 0.0))
        add_circle("z", self._gizmo_radius, segs, (0.0, 0.0, 1.0))

        self._ring_count = len(obj_verts) // 6

        # Cube wireframe, centered at origin
        s = 0.35

        p = [
            (-s, -s, -s), ( s, -s, -s), ( s,  s, -s), (-s,  s, -s),
            (-s, -s,  s), ( s, -s,  s), ( s,  s,  s), (-s,  s,  s),
        ]
        edges = [
            (0,1),(1,2),(2,3),(3,0),
            (4,5),(5,6),(6,7),(7,4),
            (0,4),(1,5),(2,6),(3,7),
        ]
        for a, b in edges:
            o_add_line(p[a], p[b], (0.85, 0.85, 0.92))

        self._obj_count = len(obj_verts) // 6
        obj_data = struct.pack(f"{len(obj_verts)}f", *obj_verts)

        self._vbo_obj = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self._vbo_obj.create()
        self._vbo_obj.bind()
        self._vbo_obj.allocate(obj_data, len(obj_data))

        self._vao_obj = QOpenGLVertexArrayObject()
        self._vao_obj.create()
        self._vao_obj.bind()

        self._prog.bind()
        self._prog.enableAttributeArray(0)
        self._prog.setAttributeBuffer(0, GL_FLOAT, 0, 3, stride)
        self._prog.enableAttributeArray(1)
        self._prog.setAttributeBuffer(1, GL_FLOAT, 3 * 4, 3, stride)

        self._vao_obj.release()
        self._vbo_obj.release()
        self._prog.release()

        try:
            self._gl.glDisable(GL_DEPTH_TEST)
        except Exception:
            pass

        try:
            self._gl.glEnable(GL_BLEND)
            self._gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        except Exception:
            pass

        # MSAA (helps jaggies on lines)
        try:
            self._gl.glEnable(0x809D) # GL_MULTISAMPLE
        except Exception:
            pass

        try:
            rng = self._gl.glGetFloatv(0x0B22)  # GL_ALIASED_LINE_WIDTH_RANGE
            print("[smoketest] line width range:", rng)
        except Exception as ex:
            print("[smoketest] line width range query failed:", ex)       


    def _camera_world(self) -> QtGui.QVector3D:
        yaw = self._yaw
        pitch = self._pitch
        dist = self._dist
        return v3(
            dist * math.cos(pitch) * math.sin(yaw),
            dist * math.sin(pitch),
            dist * math.cos(pitch) * math.cos(yaw),
        )

    def _camera_basis(self, cam: QtGui.QVector3D) -> tuple[QtGui.QVector3D, QtGui.QVector3D, QtGui.QVector3D]:
        # forward points from camera to origin
        forward = v3_normalized(v3(0.0, 0.0, 0.0) - cam)
        up_world = v3(0.0, 1.0, 0.0)
        right = v3_cross(forward, up_world)
        if v3_norm(right) < 1e-6:
            right = v3(1.0, 0.0, 0.0)
        else:
            right = v3_normalized(right)
        up = v3_cross(right, forward)
        up = v3_normalized(up)
        return right, up, forward

    def _build_mats(self):
        w = max(1, self.width())
        h = max(1, self.height())
        aspect = float(w) / float(h)

        proj = QtGui.QMatrix4x4()
        proj.perspective(45.0, aspect, 0.01, 100.0)

        cam = self._camera_world()
        view = QtGui.QMatrix4x4()
        view.lookAt(cam, v3(0.0, 0.0, 0.0), v3(0.0, 1.0, 0.0))

        model = QtGui.QMatrix4x4()
        model.rotate(self._obj_rot)

        return proj, view, model, cam

    def _compute_ring_draw_scale(self, proj: QtGui.QMatrix4x4, view: QtGui.QMatrix4x4, model: QtGui.QMatrix4x4) -> float:
        # Target pixel radius for XYZ rings, derived from the cached white ring
        if self._view_ring_radius_px is None:
            return 1.0

        target_px = float(self._view_ring_radius_px) / float(self._view_ring_scale)

        dpr = float(self.devicePixelRatioF())
        vh = float(max(1, self.height())) * dpr

        proj_y = abs(float(proj.row(1).y()))  # P[1,1]
        if proj_y <= 1e-8:
            return 1.0

        vm = QtGui.QMatrix4x4()
        vm *= view
        vm *= model

        # gizmo center is at origin in this smoketest
        p_view = vm.map(v3(0.0, 0.0, 0.0))
        dist = abs(float(p_view.z()))
        if dist <= 1e-6:
            return 1.0

        ring_r = float(self._gizmo_radius)
        if ring_r <= 1e-6:
            return 1.0

        # Same derivation as gl_view: keep a world-space radius constant in pixels
        s = (target_px * 2.0 * dist) / (vh * proj_y * ring_r)

        # safety clamp
        return max(0.01, min(1000.0, float(s)))

    def _project_to_screen(self, mvp: QtGui.QMatrix4x4, p: QtGui.QVector3D) -> QtCore.QPointF | None:
        # Qt-safe projection: avoid (QMatrix4x4 * QVector4D) which PySide6 doesn't overload.
        # We'll compute clip.w manually, and use mvp.map(p) for the perspective-divided XYZ.
        x = float(p.x())
        y = float(p.y())
        z = float(p.z())

        # clip w = row3 dot [x y z 1]
        r3 = mvp.row(3)  # QVector4D
        clip_w = float(r3.x()) * x + float(r3.y()) * y + float(r3.z()) * z + float(r3.w()) * 1.0
        if abs(clip_w) < 1e-9:
            return None

        # mvp.map(QVector3D) returns the perspective-divided position (NDC-ish)
        v = mvp.map(p)
        ndc_x = float(v.x())
        ndc_y = float(v.y())

        sx = (ndc_x * 0.5 + 0.5) * float(max(1, self.width()))
        sy = (1.0 - (ndc_y * 0.5 + 0.5)) * float(max(1, self.height()))
        return QtCore.QPointF(sx, sy)

    def _gizmo_screen_circle(self) -> tuple[QtCore.QPointF, float] | None:
        proj, view, model, _cam = self._build_mats()
        mvp = proj * view * model

        c = self._project_to_screen(mvp, v3(0.0, 0.0, 0.0))
        if c is None:
            return None

        r = float(self._gizmo_radius)

        # Sample multiple axis points and take the largest projected distance.
        # This avoids the radius shrinking depending on object rotation.
        samples = [
            v3( r, 0.0, 0.0), v3(-r, 0.0, 0.0),
            v3(0.0,  r, 0.0), v3(0.0, -r, 0.0),
            v3(0.0, 0.0,  r), v3(0.0, 0.0, -r),
        ]

        best = 1.0
        for p in samples:
            sp = self._project_to_screen(mvp, p)
            if sp is None:
                continue
            d = math.hypot(float(sp.x() - c.x()), float(sp.y() - c.y()))
            if d > best:
                best = d

        return c, max(1.0, float(best))
    
    def _view_ring_screen_circle(self) -> tuple[QtCore.QPointF, float] | None:
        res = self._gizmo_screen_circle()
        if res is None:
            return None

        c, _r_px = res

        ui = float(getattr(self, "_gizmo_ui_scale", 1.0))
        r_view = float(self._gizmo_screen_radius_px) * ui * float(self._view_ring_scale)

        return c, r_view

    def _draw_view_ring_2d(self, hovered: bool) -> None:
        res = self._view_ring_screen_circle()
        if res is None:
            return
        c, r = res

        painter = QtGui.QPainter(self)
        if not painter.isActive():
            return

        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        rect = QtCore.QRectF(float(c.x() - r), float(c.y() - r), float(2.0 * r), float(2.0 * r))

        # hovered/active glow (subtle, same look as the old always-on)
        if hovered:
            for width, alpha in ((12, 18), (8, 28)):
                pen = QtGui.QPen(QtGui.QColor(255, 255, 255, alpha))
                pen.setWidth(int(width))
                pen.setCapStyle(QtCore.Qt.RoundCap)
                pen.setJoinStyle(QtCore.Qt.RoundJoin)
                painter.setPen(pen)
                painter.drawEllipse(rect)

        # crisp core ring
        core_col = getattr(self, "_view_ring_core_color", QtGui.QColor(255, 255, 255, 220))
        pen = QtGui.QPen(core_col)
        pen.setWidth(2)
        #pen.setWidth(1)
        #pen.setCosmetic(True) # keeps it 1px on screen
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawEllipse(rect)

        painter.end()   

    def _draw_center_disc_2d(self, center: QtCore.QPointF, radius_px: float, hovered: bool) -> None:
        # Very light "grab area" disc in the center (2D overlay)
        painter = QtGui.QPainter(self)
        if not painter.isActive():
            return
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        r = float(radius_px)
        rect = QtCore.QRectF(float(center.x() - r), float(center.y() - r), float(2.0 * r), float(2.0 * r))

        # Subtle fill (dark gray). Slightly stronger when hovered/dragging.
        base_a = 55
        hover_a = 70
        a = hover_a if hovered else base_a


        col = QtGui.QColor(35, 35, 35, a) # dark gray
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(col))
        painter.drawEllipse(rect)

        # Center point (shows exact gizmo origin)
        dot_r = 2.0  # pixels
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

    def _ring_clip_discard(
        self,
        p_local: QtGui.QVector3D,
        axis_vec: QtGui.QVector3D,
        view_dir_local: QtGui.QVector3D,
        back_clip_cos: float,
    ) -> bool:
        # Match your FRAG logic: discard back-facing parts of ring
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
        thresh = (-1.0) * (1.0 - t) + float(back_clip_cos) * t  # mix(-1, back_clip_cos, t)
        return d < thresh

    def _draw_hover_halo_2d(
        self,
        axis: str,
        mvp: QtGui.QMatrix4x4,
        view_dir_local: QtGui.QVector3D,
        back_clip_cos: float,
        clip_enabled: bool,
    ) -> None:
        r = float(self._gizmo_radius)
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

            if clip_enabled and self._ring_clip_discard(p, axis_vec, view_dir_local, back_clip_cos):
                if len(seg) >= 2:
                    segments.append(seg)
                seg = []
                continue

            sp = self._project_to_screen(mvp, p)
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

        painter = QtGui.QPainter(self)
        if not painter.isActive():
            return

        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        # Soft halo only (GL already draws the crisp 1px core)
        if axis == "x":
            base = (255, 0, 0)
        elif axis == "y":
            base = (0, 255, 0)
        else:
            #base = (0, 0, 255) #blue
            base = (70, 120, 255) # bluish-white glow, easier to see

        for width, alpha in ((10, 35), (6, 55)):
            c = QtGui.QColor(base[0], base[1], base[2], alpha)
            pen = QtGui.QPen(c)
            pen.setWidth(width)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(pen)

            for pts in segments:
                painter.drawPolyline(QtGui.QPolygonF(pts))

        painter.end()

    def _draw_xyz_core_2d(
        self,
        mvp: QtGui.QMatrix4x4,
        view_dir_local: QtGui.QVector3D,
        back_clip_cos: float,
        clip_enabled: bool,
        width_px: int = 2,
    ) -> None:
        r = float(self._gizmo_radius)
        steps = 128

        painter = QtGui.QPainter(self)
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

                if clip_enabled and self._ring_clip_discard(p, axis_vec, view_dir_local, back_clip_cos):
                    if len(seg) >= 2:
                        segments.append(seg)
                    seg = []
                    continue

                sp = self._project_to_screen(mvp, p)
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

    def _pick_hover_axis(self) -> str | None:
        # Pick closest ring in screen space by sampling points on each ring.
        # IMPORTANT: When ring clipping is enabled, ignore points that would be discarded
        # by the FRAG shader so the "back" (clipped) arc can't trigger hover.
        res = self._gizmo_screen_circle()
        if res is None:
            return None
        _c, r = res

        mx = float(self._mouse_pos.x())
        my = float(self._mouse_pos.y())

        # hover tolerance in pixels (bigger = easier to hover)
        band = max(12.0, r * 0.14)

        proj, view, model, cam = self._build_mats()
        mvp = proj * view * model

        # Match OBJECT pass local-space view dir (paintGL) for ring clipping tests
        inv_rot = self._obj_rot.inverted()
        cam_local = inv_rot.rotatedVector(cam)
        view_dir_local = v3_normalized(cam_local)

        back_clip_cos = -math.cos(math.pi * float(self._clip_frac))
        clip_enabled = bool(self._clip_enabled)

        def ring_dist(axis: str) -> float:
            best = 1e9
            steps = 48

            if axis == "x":
                axis_vec = v3(1.0, 0.0, 0.0)
            elif axis == "y":
                axis_vec = v3(0.0, 1.0, 0.0)
            else:
                axis_vec = v3(0.0, 0.0, 1.0)

            for i in range(steps):
                t = (i / steps) * (2.0 * math.pi)
                ct = math.cos(t) * self._gizmo_radius
                st = math.sin(t) * self._gizmo_radius

                if axis == "x":
                    p = v3(0.0, ct, st)      # ring in YZ plane
                elif axis == "y":
                    p = v3(ct, 0.0, st)      # ring in XZ plane
                else:
                    p = v3(ct, st, 0.0)      # ring in XY plane

                if clip_enabled and self._ring_clip_discard(p, axis_vec, view_dir_local, float(back_clip_cos)):
                    continue

                sp = self._project_to_screen(mvp, p)
                if sp is None:
                    continue

                d = math.hypot(mx - float(sp.x()), my - float(sp.y()))
                if d < best:
                    best = d

            return best

        dx = ring_dist("x")
        dy = ring_dist("y")
        dz = ring_dist("z")

        best = min(dx, dy, dz)
        if best > band:
            return None
        if best == dx:
            return "x"
        if best == dy:
            return "y"
        return "z"

    def _pick_hover_view_ring(self) -> bool:
        res = self._view_ring_screen_circle()
        if res is None:
            return False
        c, r = res

        mx = float(self._mouse_pos.x())
        my = float(self._mouse_pos.y())
        d = math.hypot(mx - float(c.x()), my - float(c.y()))

        # hover band thickness in pixels
        band = max(10.0, r * 0.12)
        return abs(d - float(r)) <= float(band)


    def _arcball_vec_world(self, mouse_px: QtCore.QPointF, center_px: QtCore.QPointF, radius_px: float, cam: QtGui.QVector3D) -> QtGui.QVector3D:
        # Map mouse to arcball unit sphere in camera space, then convert to world using camera basis.
        x = (float(mouse_px.x()) - float(center_px.x())) / float(radius_px)
        y = (float(center_px.y()) - float(mouse_px.y())) / float(radius_px)  # flip so up is positive

        # Clamp to sphere
        r2 = x * x + y * y
        if r2 <= 1.0:
            z = math.sqrt(1.0 - r2)
        else:
            inv = 1.0 / math.sqrt(r2)
            x *= inv
            y *= inv
            z = 0.0

        right, up, forward = self._camera_basis(cam)
        # camera-space z points toward the viewer, which is -forward in world
        v_world = right * float(x) + up * float(y) + (forward * float(-z))
        return v3_normalized(v_world)

    def _ray_dir_world(self, mouse_px: QtCore.QPointF, cam: QtGui.QVector3D) -> QtGui.QVector3D:
        # Build a perspective ray direction in WORLD space from a screen pixel.
        w = float(max(1, self.width()))
        h = float(max(1, self.height()))
        aspect = w / h

        # NDC in [-1..1], with +Y up
        ndc_x = (2.0 * (float(mouse_px.x()) / w)) - 1.0
        ndc_y = 1.0 - (2.0 * (float(mouse_px.y()) / h))

        tan_half = math.tan(math.radians(45.0) * 0.5)
        vx = ndc_x * tan_half * aspect
        vy = ndc_y * tan_half

        right, up, forward = self._camera_basis(cam)
        d = right * float(vx) + up * float(vy) + forward * 1.0
        return v3_normalized(d)

    def _axis_ring_dir_world(
        self,
        mouse_px: QtCore.QPointF,
        axis_world_unit: QtGui.QVector3D,
        cam: QtGui.QVector3D,
    ) -> QtGui.QVector3D | None:
        # Use the closest point on the camera ray to the origin, then project to the ring plane.
        ray_d = self._ray_dir_world(mouse_px, cam)

        # closest point to origin on ray: cam + ray_d * t
        t = QtGui.QVector3D.dotProduct(v3(0.0, 0.0, 0.0) - cam, ray_d)
        p = cam + ray_d * float(t)

        # project onto plane perpendicular to axis
        p_plane = p - axis_world_unit * QtGui.QVector3D.dotProduct(p, axis_world_unit)
        if p_plane.length() <= 1e-6:
            return None
        return p_plane / p_plane.length()


    def _mouse_posf(self, e) -> QtCore.QPointF:
        # Qt6: e.position() -> QPointF
        try:
            p = e.position()
            return QtCore.QPointF(float(p.x()), float(p.y()))
        except Exception:
            # Qt5/PySide2 fallback: e.pos() -> QPoint
            p = e.pos()
            return QtCore.QPointF(float(p.x()), float(p.y()))

    def mousePressEvent(self, e) -> None:
        mp = self._mouse_posf(e)
        self._mouse_pos = mp
        
        self._last_mouse = mp
        
        mods = QtWidgets.QApplication.keyboardModifiers()

        # Alt + Left = orbit
        if e.button() == QtCore.Qt.LeftButton and (mods & QtCore.Qt.AltModifier):
            self._orbiting = True
            e.accept()
            return

        # Alt + Right = zoom drag
        if e.button() == QtCore.Qt.RightButton and (mods & QtCore.Qt.AltModifier):
            self._zooming = True
            e.accept()
            return

        # Left click (no Alt): arcball rotate only if inside gizmo radius on screen
        if e.button() == QtCore.Qt.LeftButton:

            # View ring drag (camera-facing)
            if self._pick_hover_view_ring():
                mp = self._mouse_posf(e)
                self._mouse_pos = mp
                self._last_mouse = mp
                # store last dir from ring center to mouse (normalized)
                res = self._view_ring_screen_circle()
                if res is not None:
                    c, r = res
                    vx = float(mp.x() - c.x())
                    vy = float(mp.y() - c.y())
                    l = math.hypot(vx, vy)
                    if l > 1e-6:
                        self._view_drag_last_dir = QtCore.QPointF(vx / l, vy / l)
                    else:
                        self._view_drag_last_dir = None
                else:
                    self._view_drag_last_dir = None
                                
                # store start state
                self._drag_view = True
                self._view_drag_start_mouse = QtCore.QPointF(mp)
                self._view_drag_start_rot = QtGui.QQuaternion(
                    float(self._obj_rot.scalar()),
                    float(self._obj_rot.x()),
                    float(self._obj_rot.y()),
                    float(self._obj_rot.z()),
                )
                e.accept()
                return

            # Axis drag wins over arcball when clicking a hovered ring
            if self._hover_axis in ("x", "y", "z"):
                mp = self._mouse_posf(e)
                self._mouse_pos = mp

                _proj, _view, _model, cam = self._build_mats()

                axis_local = v3(1, 0, 0) if self._hover_axis == "x" else (v3(0, 1, 0) if self._hover_axis == "y" else v3(0, 0, 1))
                axis_world = self._obj_rot.rotatedVector(axis_local)
                if axis_world.length() > 1e-6:
                    axis_world = axis_world / axis_world.length()

                start_dir = self._axis_ring_dir_world(mp, axis_world, cam)
                if start_dir is not None:
                    self._drag_axis.active = True
                    self._drag_axis.axis = self._hover_axis
                    self._drag_axis.start_rot = QtGui.QQuaternion(
                        float(self._obj_rot.scalar()),
                        float(self._obj_rot.x()),
                        float(self._obj_rot.y()),
                        float(self._obj_rot.z()),
                    )
                    self._drag_axis.axis_world = axis_world
                    self._drag_axis.start_dir = start_dir
                    e.accept()
                    return

            if self._drag_axis.active:
                e.accept()
                return

            circle = self._gizmo_screen_circle()
            if circle is None:
                return
            
            center_px, radius_px = circle

            mp = self._mouse_posf(e)
            dist = math.hypot(float(mp.x() - center_px.x()), float(mp.y() - center_px.y()))
            if dist > radius_px * 1.15:
                return

            proj, view, model, cam = self._build_mats()
            start_vec_world = self._arcball_vec_world(mp, center_px, radius_px, cam)

            self._drag_arc.active = True
            self._drag_arc.start_vec_world = start_vec_world
            self._drag_arc.start_rot = self._obj_rot
            self._drag_arc.center_px = center_px
            self._drag_arc.radius_px = radius_px

            e.accept()
            return
        
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        # End view ring drag
        if e.button() == QtCore.Qt.LeftButton and self._drag_view:
            self._drag_view = False
            self._view_drag_last_dir = None
            self._hover_view_ring = False
            self.update()
            e.accept()
            return
        
        # End axis drag
        if e.button() == QtCore.Qt.LeftButton and self._drag_axis.active:
            self._drag_axis.active = False
            self._drag_axis.axis = None
            self._drag_axis.start_rot = None
            self._drag_axis.axis_world = None
            self._drag_axis.start_dir = None
            self.update()
            e.accept()
            return
        
        if e.button() == QtCore.Qt.LeftButton:
            self._orbiting = False
            self._drag_arc = DragArcball()  # reset arcball drag

        if e.button() == QtCore.Qt.RightButton:
            self._zooming = False

        super().mouseReleaseEvent(e)

    def wheelEvent(self, e) -> None:
        # Disabled on purpose (zoom is Alt + Right drag)
        e.ignore()

    def mouseMoveEvent(self, e) -> None:
        mp = self._mouse_posf(e)
        self._mouse_pos = mp

        # View ring drag: rotate around camera forward axis (screen-facing)
        if self._drag_view:
            _proj, _view, _model, cam = self._build_mats()
            _right, _up, forward = self._camera_basis(cam)

            res = self._view_ring_screen_circle()
            if res is None:
                e.accept()
                return
            c, _r = res

            # current dir from ring center to mouse (normalized)
            vx = float(mp.x() - c.x())
            vy = float(mp.y() - c.y())
            l = math.hypot(vx, vy)
            if l <= 1e-6:
                e.accept()
                return
            cur_dir = QtCore.QPointF(vx / l, vy / l)

            prev = self._view_drag_last_dir
            if prev is None:
                self._view_drag_last_dir = cur_dir
                e.accept()
                return

            # signed angle from prev -> cur (screen space)
            cross = float(prev.x() * cur_dir.y() - prev.y() * cur_dir.x())
            dot = float(prev.x() * cur_dir.x() + prev.y() * cur_dir.y())
            ang = math.degrees(math.atan2(cross, dot))

            self._view_drag_last_dir = cur_dir

            q = QtGui.QQuaternion.fromAxisAndAngle(forward, float(ang))
            self._obj_rot = (q * self._obj_rot).normalized()

            self.update()
            e.accept()
            return

        # Axis-constrained ring drag (must run BEFORE orbit/zoom and BEFORE any early return)
        if (
            self._drag_axis.active
            and self._drag_axis.axis_world is not None
            and self._drag_axis.start_dir is not None
            and self._drag_axis.start_rot is not None
        ):
            _proj, _view, _model, cam = self._build_mats()

            cur_dir = self._axis_ring_dir_world(mp, self._drag_axis.axis_world, cam)
            if cur_dir is not None:
                a = self._drag_axis.start_dir
                b = cur_dir
                axis = self._drag_axis.axis_world

                cross = QtGui.QVector3D.crossProduct(a, b)
                sinang = QtGui.QVector3D.dotProduct(cross, axis)
                cosang = QtGui.QVector3D.dotProduct(a, b)

                ang = math.degrees(math.atan2(float(sinang), float(cosang)))
                q = QtGui.QQuaternion.fromAxisAndAngle(axis, float(ang))
                self._obj_rot = (q * self._drag_axis.start_rot).normalized()

            self.update()
            self._last_mouse = mp
            e.accept()
            return

        dx = float(mp.x() - self._last_mouse.x())
        dy = float(mp.y() - self._last_mouse.y())
        self._last_mouse = mp

        if self._orbiting:
            self._yaw -= float(dx) * 0.01
            self._pitch += float(dy) * 0.01
            self._pitch = max(-1.35, min(1.35, self._pitch))
            self.update()
            e.accept()
            return

        if self._zooming:
            delta = float(dx - dy)  # +right, +up (because dy<0 => -dy>0)
            self._dist *= math.exp(-delta * 0.01)
            self._dist = max(0.6, min(20.0, self._dist))
            self.update()
            e.accept()
            return

        if self._drag_arc.active and self._drag_arc.start_vec_world is not None and self._drag_arc.start_rot is not None:
            proj, view, model, cam = self._build_mats()

            center_px = self._drag_arc.center_px
            radius_px = self._drag_arc.radius_px
            if center_px is None or radius_px <= 1e-6:
                return

            cur_vec_world = self._arcball_vec_world(mp, center_px, radius_px, cam)
            q_delta_world = quat_from_two_vectors(self._drag_arc.start_vec_world, cur_vec_world)

            self._obj_rot = (q_delta_world * self._drag_arc.start_rot).normalized()
            self.update()
            e.accept()
            return

        super().mouseMoveEvent(e)

    def paintGL(self) -> None:
        if not self._gl or not self._prog or not self._vao_world or not self._vao_obj:
            return

        w = max(1, self.width())
        h = max(1, self.height())
        self._gl.glViewport(0, 0, w, h)
        self._gl.glClearColor(0.08, 0.08, 0.10, 1.0)
        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        proj, view, model, cam = self._build_mats()

        back_clip_cos = -math.cos(math.pi * float(self._clip_frac))
        clip_val = 1.0 if self._clip_enabled else 0.0

        self._prog.bind()

        # WORLD pass
        mvp_world = proj * view
        view_dir_world = v3_normalized(cam)

        up_world = v3(0.0, 1.0, 0.0)
        right_world = QtGui.QVector3D.crossProduct(up_world, view_dir_world)
        if right_world.length() <= 1e-6:
            right_world = v3(1.0, 0.0, 0.0)
        else:
            right_world /= right_world.length()

        self._prog.setUniformValue("u_mvp", mvp_world)
        self._prog.setUniformValue("u_clip", v3(0.0, 0.0, 0.0))
        self._prog.setUniformValue("u_view_dir", view_dir_world)
        self._prog.setUniformValue("u_view_right", right_world)
        self._prog.setUniformValue("u_back_clip", v3(float(back_clip_cos), 0.0, 0.0))
        self._prog.setUniformValue("u_color", QtGui.QVector4D(1.0, 1.0, 1.0, 1.0))

        self._vao_world.bind()
        self._gl.glDrawArrays(GL_LINES, 0, self._world_count)
        self._vao_world.release()

        # OBJECT pass
        mvp_obj = proj * view * model

        inv_rot = self._obj_rot.inverted()
        cam_local = inv_rot.rotatedVector(cam)
        view_dir_local = v3_normalized(cam_local)

        up_local = v3(0.0, 1.0, 0.0)
        right_local = QtGui.QVector3D.crossProduct(up_local, view_dir_local)
        if right_local.length() <= 1e-6:
            right_local = v3(1.0, 0.0, 0.0)
        else:
            right_local /= right_local.length()

        # Desired XYZ ring radius in pixels (single overall knob)
        target_ring_px = float(self._gizmo_screen_radius_px) * float(getattr(self, "_gizmo_ui_scale", 1.0))

        # Depth-based screen-size scale (like gl_view._gizmo_screen_scale), no sampling
        try:
            dpr = float(self.devicePixelRatioF())
        except Exception:
            dpr = 1.0

        vh = float(self.height()) * dpr
        proj_y = abs(float(proj.row(1).y()))
        ring_r = float(self._gizmo_radius)

        VM = QtGui.QMatrix4x4()
        VM *= view
        VM *= model
        view_p = VM.map(v3(0.0, 0.0, 0.0))  # gizmo center at origin here
        dist = abs(float(view_p.z()))

        # When you zoom extremely close, dist approaches 0 and the math becomes pathological.
        # Clamp dist so the gizmo doesn't collapse to nothing.
        dist = max(dist, 0.05)

        if vh <= 1.0 or proj_y <= 1e-6 or ring_r <= 1e-6:
            ring_draw_scale = 1.0
        else:
            ring_draw_scale = (target_ring_px * 2.0 * dist) / (vh * proj_y * ring_r)
            ring_draw_scale = max(0.01, min(1000.0, float(ring_draw_scale)))

        self._ring_draw_scale = float(ring_draw_scale)

        # Rings get scaled, cube does NOT
        model_rings = QtGui.QMatrix4x4()
        model_rings *= model
        model_rings.scale(float(ring_draw_scale))
        mvp_rings = proj * view * model_rings

        # Hover picking (use the same scaled MVP as the drawn rings)
        c_obj = self._project_to_screen(mvp_obj, v3(0.0, 0.0, 0.0))
        # Center "grab" hover zone (for arcball / free rotate)
        hover_center = False
        center_grab_r_px = None

        if self._drag_axis.active and self._drag_axis.axis:
            self._hover_axis = self._drag_axis.axis
        else:
            self._hover_axis = None
            if c_obj is not None:
                mx = float(self._mouse_pos.x())
                my = float(self._mouse_pos.y())

                ring_r_px = float(target_ring_px)
                band = max(12.0, ring_r_px * 0.14)

                clip_enabled = bool(self._clip_enabled)

                def ring_dist(axis: str) -> float:
                    best_d = 1e9
                    steps = 48

                    if axis == "x":
                        axis_vec = v3(1.0, 0.0, 0.0)
                    elif axis == "y":
                        axis_vec = v3(0.0, 1.0, 0.0)
                    else:
                        axis_vec = v3(0.0, 0.0, 1.0)

                    for i in range(steps):
                        t = (i / steps) * (2.0 * math.pi)
                        ct = math.cos(t) * float(self._gizmo_radius)
                        st = math.sin(t) * float(self._gizmo_radius)

                        if axis == "x":
                            p = v3(0.0, ct, st)      # ring in YZ plane
                        elif axis == "y":
                            p = v3(ct, 0.0, st)      # ring in XZ plane
                        else:
                            p = v3(ct, st, 0.0)      # ring in XY plane

                        if clip_enabled and self._ring_clip_discard(p, axis_vec, view_dir_local, float(back_clip_cos)):
                            continue

                        sp = self._project_to_screen(mvp_rings, p)
                        if sp is None:
                            continue

                        d = math.hypot(mx - float(sp.x()), my - float(sp.y()))
                        if d < best_d:
                            best_d = d

                    return best_d

                dx = ring_dist("x")
                dy = ring_dist("y")
                dz = ring_dist("z")

                best_pick = min(dx, dy, dz)
                if best_pick <= band:
                    if best_pick == dx:
                        self._hover_axis = "x"
                    elif best_pick == dy:
                        self._hover_axis = "y"
                    else:
                        self._hover_axis = "z"

        self._hover_view_ring = (not self._drag_axis.active and not self._drag_arc.active) and self._pick_hover_view_ring()

        # Show the center disc only when hovering the center area (and not on rings)
        if c_obj is not None and (self._hover_axis is None) and (not self._hover_view_ring):
            mx = float(self._mouse_pos.x())
            my = float(self._mouse_pos.y())
            center_grab_r_px = max(10.0, float(target_ring_px) * 0.28)  # tweak 0.28 if needed
            d = math.hypot(mx - float(c_obj.x()), my - float(c_obj.y()))
            hover_center = (d <= float(center_grab_r_px))

        # Draw rings with scaled MVP (constant screen size)
        self._prog.setUniformValue("u_mvp", mvp_rings)
        self._prog.setUniformValue("u_clip", v3(float(clip_val), 0.0, 0.0))
        self._prog.setUniformValue("u_view_dir", view_dir_local)
        self._prog.setUniformValue("u_view_right", right_local)
        self._prog.setUniformValue("u_back_clip", v3(float(back_clip_cos), 0.0, 0.0))

        self._vao_obj.bind()

        rings_per_axis = self._ring_count // 3
        for i, axis in enumerate(("x", "y", "z")):
            start = i * rings_per_axis
            count = rings_per_axis
            self._prog.setUniformValue("u_color", QtGui.QVector4D(1.0, 1.0, 1.0, 1.0))
            self._gl.glDrawArrays(GL_LINES, start, count)

        # Draw cube UNscaled (rotation-only gizmo center should not be affected by ring scale)
        self._prog.setUniformValue("u_mvp", mvp_obj)
        self._prog.setUniformValue("u_color", QtGui.QVector4D(1.0, 1.0, 1.0, 1.0))
        self._prog.setUniformValue("u_clip", v3(0.0, 0.0, 0.0))
        self._gl.glDrawArrays(GL_LINES, self._ring_count, self._obj_count - self._ring_count)

        self._vao_obj.release()
        self._prog.release()

        # 2D core overlay so XYZ can be 2px even when GL line width is clamped
        self._draw_xyz_core_2d(
            mvp=mvp_rings,
            view_dir_local=view_dir_local,
            back_clip_cos=float(back_clip_cos),
            clip_enabled=(clip_val > 0.5),
            width_px=2,
        )

        if c_obj is not None:
            self._draw_center_disc_2d(
                center=c_obj,
                radius_px=float(target_ring_px),  # SAME as XYZ ring radius on screen
                hovered=bool(hover_center or self._drag_arc.active),
            )
        self._draw_view_ring_2d(hovered=self._hover_view_ring or self._drag_view)

        # Hover thickness overlay (2D halo) uses the ring-scaled MVP
        if self._hover_axis:
            self._draw_hover_halo_2d(
                axis=self._hover_axis,
                mvp=mvp_rings,
                view_dir_local=view_dir_local,
                back_clip_cos=float(back_clip_cos),
                clip_enabled=(clip_val > 0.5),
            )
            
def main() -> None:
    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.OpenGL)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setVersion(3, 3)
    fmt.setSamples(4) # try 4 first (8 if you want it cleaner)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QtWidgets.QApplication(sys.argv)
    w = GizmoControlSmoke()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()