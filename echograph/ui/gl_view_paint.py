# echograph/ui/gl_view_paint.py
from __future__ import annotations

import math
from typing import Any, Optional

try:
    import numpy as np
except Exception:
    np = None

try:
    from PySide6 import QtCore, QtGui
except Exception:
    from PySide2 import QtCore, QtGui  # type: ignore


# Minimal GL enums so this module does not import gl_view.py (avoids circular imports)
GL_TRIANGLES = 0x0004
GL_TRIANGLE_STRIP = 0x0005
GL_LINES = 0x0001
GL_FLOAT = 0x1406
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_DEPTH_TEST = 0x0B71
GL_CULL_FACE = 0x0B44
GL_LESS = 0x0201


def paint_gl(view: Any) -> None:
    # This is the old GraphGLView.paintGL body, with self -> view.

    if not hasattr(view, "_gl"):
        return

    if getattr(view, "_render_paused", False):
        bg = getattr(view, "_viewport_bg", QtGui.QColor("#1a1f24"))
        view._gl.glClearColor(bg.redF(), bg.greenF(), bg.blueF(), 1.0)
        view._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        return

    # ModernGL path (keep clean)
    if getattr(view, "_use_moderngl", False):
        view._paint_mgl()

        # Make sure we're drawing into the visible Qt FBO
        try:
            view._mgl_bind_default_fbo()
        except Exception:
            pass

        # Axis overlay (debug) in ModernGL path
        if getattr(view, "_debug_show_axis_overlay", False):
            try:
                if getattr(view, "_axis_overlay", None) is None:
                    from echograph.ui.axis_gizmo_overlay import AxisGizmoOverlay
                    view._axis_overlay = AxisGizmoOverlay()

                # Only draw gizmo when something is selected (unless idle gizmo is allowed)
                if getattr(view, "_xform_gizmo_owner", None) is None and not bool(
                    getattr(view, "_xform_gizmo_idle_visible", False)
                ):
                    return

                if view._axis_overlay.ensure_gl(view):
                    renderer = getattr(view, "_mgl_renderer", None) or view

                    P = getattr(renderer, "_mgl_pick_proj", None)
                    V = getattr(renderer, "_mgl_pick_view", None)
                    M = getattr(renderer, "_mgl_pick_model", None)

                    if P is None or V is None or M is None:
                        return  # renderer hasn't produced matrices yet

                    pos = getattr(view, "_xform_gizmo_pos", (0.0, 0.0, 0.0)) or (0.0, 0.0, 0.0)

                    if np is None:
                        return

                    T = np.eye(4, dtype=np.float32)
                    T[0, 3] = float(pos[0])
                    T[1, 3] = float(pos[1])
                    T[2, 3] = float(pos[2])

                    mode = getattr(view, "_xform_gizmo_mode", "translate") or "translate"
                    use_rot = (mode == "rotate") or (
                        mode in ("translate", "scale") and bool(getattr(view, "_xform_use_local", False))
                    )
                    R = None
                    if use_rot:
                        try:
                            owner = getattr(view, "_xform_gizmo_owner", None)
                            if owner:
                                is_splat = False
                                try:
                                    splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
                                    if not isinstance(splat_map, dict) or not splat_map:
                                        splat_map = getattr(renderer, "_mgl_scene_splats", None)
                                    if isinstance(splat_map, dict) and owner in splat_map:
                                        is_splat = True
                                except Exception:
                                    is_splat = False

                                get_xf = (
                                    getattr(renderer, "_mgl_get_scene_splat_xform", None)
                                    if is_splat
                                    else getattr(renderer, "_mgl_get_scene_asset_xform", None)
                                )
                                xf = get_xf(owner) if callable(get_xf) else {}
                                rot = tuple((xf or {}).get("rot", (0.0, 0.0, 0.0)))
                                rx, ry, rz = float(rot[0]), float(rot[1]), float(rot[2])
                                cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
                                cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
                                cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))

                                Rx = np.array(
                                    [[1.0, 0.0, 0.0, 0.0],
                                     [0.0,  cx,  sx, 0.0],
                                     [0.0, -sx,  cx, 0.0],
                                     [0.0, 0.0, 0.0, 1.0]],
                                    dtype=np.float32,
                                )
                                Ry = np.array(
                                    [[ cy, 0.0, -sy, 0.0],
                                     [0.0, 1.0, 0.0, 0.0],
                                     [ sy, 0.0,  cy, 0.0],
                                     [0.0, 0.0, 0.0, 1.0]],
                                    dtype=np.float32,
                                )
                                Rz = np.array(
                                    [[ cz,  sz, 0.0, 0.0],
                                     [-sz,  cz, 0.0, 0.0],
                                     [0.0, 0.0, 1.0, 0.0],
                                     [0.0, 0.0, 0.0, 1.0]],
                                    dtype=np.float32,
                                )
                                # match renderer order: Rz @ Ry @ Rx
                                R = (Rz @ Ry @ Rx).astype(np.float32)
                        except Exception:
                            R = None

                    TR = (T @ R) if (use_rot and R is not None) else T

                    # Scale gizmo so screen size stays constant (match rotate gizmo sizing).
                    TRS = None
                    try:
                        if np is not None:
                            try:
                                dpr = float(view.devicePixelRatioF())
                            except Exception:
                                dpr = 1.0

                            vh = float(max(1, view.height())) * dpr
                            Pn = np.asarray(P, dtype=np.float32)
                            Vn = np.asarray(V, dtype=np.float32)
                            Mn = np.asarray(M, dtype=np.float32)

                            proj_y = abs(float(Pn[1, 1]))
                            if proj_y > 1e-6:
                                vm = (Vn @ Mn @ TR).astype(np.float32)
                                cp = vm @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
                                w = float(cp[3]) if abs(float(cp[3])) > 1e-6 else 1.0
                                dist_raw = abs(float(cp[2]) / w)
                                dist_raw = max(dist_raw, 1e-6)

                                try:
                                    sm = float(getattr(renderer, "_mgl_scale_multiplier", 1.0))
                                except Exception:
                                    sm = 1.0
                                dist = dist_raw * sm

                                # Match rotate gizmo's target size (XYZ ring radius).
                                rot_shared = getattr(view, "_rot_shared", None)
                                if rot_shared is not None:
                                    target_ring_px = float(rot_shared.xyz_ring_radius_px())
                                    ring_r = float(getattr(rot_shared, "gizmo_radius", 0.9))
                                else:
                                    target_ring_px = 110.0 * 1.3
                                    ring_r = 0.9

                                if ring_r > 1e-6:
                                    # Compensate for scene normalization scale baked into Mn.
                                    scene_scale = 1.0
                                    try:
                                        sx = float(np.linalg.norm(Mn[:3, 0]))
                                        sy = float(np.linalg.norm(Mn[:3, 1]))
                                        sz = float(np.linalg.norm(Mn[:3, 2]))
                                        scene_scale = (sx + sy + sz) / 3.0
                                        if scene_scale <= 1e-6:
                                            scene_scale = 1.0
                                    except Exception:
                                        scene_scale = 1.0

                                    s = (target_ring_px * 2.0 * dist) / (vh * proj_y * ring_r * scene_scale)
                                    s = max(1e-6, min(1000.0, float(s)))

                                    S = np.eye(4, dtype=np.float32)
                                    S[0, 0] = s
                                    S[1, 1] = s
                                    S[2, 2] = s
                                    TRS = (TR @ S).astype(np.float32)
                    except Exception:
                        TRS = None

                    mvp_np = (P @ V @ M @ (TRS if TRS is not None else TR)).astype(np.float32)

                    mvp = QtGui.QMatrix4x4(
                        float(mvp_np[0, 0]), float(mvp_np[0, 1]), float(mvp_np[0, 2]), float(mvp_np[0, 3]),
                        float(mvp_np[1, 0]), float(mvp_np[1, 1]), float(mvp_np[1, 2]), float(mvp_np[1, 3]),
                        float(mvp_np[2, 0]), float(mvp_np[2, 1]), float(mvp_np[2, 2]), float(mvp_np[2, 3]),
                        float(mvp_np[3, 0]), float(mvp_np[3, 1]), float(mvp_np[3, 2]), float(mvp_np[3, 3]),
                    )

                    view._axis_overlay.draw(mvp, mode=mode, draw_rotate_rings=(mode != "rotate"))
            except Exception as exc:
                print("[AXIS_OVERLAY] disabled:", exc, flush=True)
                view._debug_show_axis_overlay = False

        return

    if getattr(view, "_use_example_pipeline", False):
        view._paint_example()
        return

    # Classic Qt GL path
    view._upload_scene_texture()
    view._upload_grid()

    bg = getattr(view, "_viewport_bg", QtGui.QColor("#1a1f24"))
    view._gl.glClearColor(bg.redF(), bg.greenF(), bg.blueF(), 1.0)
    view._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

    # These were missing in the original function but are clearly intended.
    proj_m = view._projection_matrix()
    view_m = view._view_matrix()

    if getattr(view, "_vao", None) is not None:
        try:
            view._vao.bind()
        except Exception:
            pass

    if getattr(view, "_show_scene_plane", False) and getattr(view, "_scene_texture", None) and getattr(view, "_quad_program", None) and getattr(view, "_quad_ready", False):
        view._quad_program.bind()
        mvp = proj_m * view_m
        view._quad_program.setUniformValue("u_mvp", mvp)
        view._quad_program.setUniformValue("u_tex", 0)
        view._scene_texture.bind(0)
        if getattr(view, "_quad_vbo", None) and view._quad_vbo.bind():
            stride = 5 * 4
            if getattr(view, "_quad_pos_loc", -1) >= 0:
                view._quad_program.enableAttributeArray(view._quad_pos_loc)
                view._quad_program.setAttributeBuffer(view._quad_pos_loc, GL_FLOAT, 0, 3, stride)
            if getattr(view, "_quad_uv_loc", -1) >= 0:
                view._quad_program.enableAttributeArray(view._quad_uv_loc)
                view._quad_program.setAttributeBuffer(view._quad_uv_loc, GL_FLOAT, 12, 2, stride)
            view._gl.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
            view._quad_vbo.release()
        view._scene_texture.release()
        view._quad_program.release()

    if getattr(view, "_mesh_program", None) and getattr(view, "_grid_count", 0) > 0 and getattr(view, "_grid_vbo", None) is not None:
        view._mesh_program.bind()
        model = QtGui.QMatrix4x4()
        model.translate(view._grid_center.x(), view._grid_center.y(), 0.0)
        mvp = proj_m * view_m * model
        view._mesh_program.setUniformValue("u_mvp", mvp)
        color = view._grid_color
        view._mesh_program.setUniformValue(
            "u_color",
            QtGui.QVector4D(color.redF(), color.greenF(), color.blueF(), 0.55),
        )
        depth_disabled = False
        if getattr(view, "_show_test_cube", False) and not getattr(view, "_render_scene_models", False) and not getattr(view, "_render_scene_plane", False):
            try:
                view._gl.glDisable(GL_DEPTH_TEST)
                depth_disabled = True
            except Exception:
                depth_disabled = False
        if view._grid_vbo.bind():
            if getattr(view, "_mesh_pos_loc", -1) >= 0:
                view._mesh_program.enableAttributeArray(view._mesh_pos_loc)
                view._mesh_program.setAttributeBuffer(view._mesh_pos_loc, GL_FLOAT, 0, 3, 0)
            view._gl.glDrawArrays(GL_LINES, 0, view._grid_count)
            view._grid_vbo.release()
        if depth_disabled:
            try:
                view._gl.glEnable(GL_DEPTH_TEST)
            except Exception:
                pass
        view._mesh_program.release()

    if getattr(view, "_mesh_program", None) and getattr(view, "_render_scene_models", False):
        for name, mesh in getattr(view, "_meshes", {}).items():
            if getattr(mesh, "count", 0) == 0:
                continue
            if getattr(mesh, "vbo", None) is None:
                mesh.upload()
            if getattr(mesh, "vbo", None) is None:
                continue
            model = getattr(view, "_mesh_transforms", {}).get(name, QtGui.QMatrix4x4())
            mvp = proj_m * view_m * model
            color = getattr(view, "_mesh_colors", {}).get(name, QtGui.QColor("#60a5fa"))
            view._mesh_program.bind()
            view._mesh_program.setUniformValue("u_mvp", mvp)
            view._mesh_program.setUniformValue(
                "u_color",
                QtGui.QVector4D(color.redF(), color.greenF(), color.blueF(), 0.85),
            )
            if mesh.vbo.bind():
                if getattr(view, "_mesh_pos_loc", -1) >= 0:
                    view._mesh_program.enableAttributeArray(view._mesh_pos_loc)
                    view._mesh_program.setAttributeBuffer(view._mesh_pos_loc, GL_FLOAT, 0, 3, 0)
                view._gl.glDrawArrays(GL_TRIANGLES, 0, mesh.count)
                mesh.vbo.release()
            view._mesh_program.release()

    if getattr(view, "_mesh_program", None) and getattr(view, "_debug_mesh", None) and getattr(view, "_show_test_cube", False):
        dbg = view._debug_mesh
        if getattr(dbg, "vbo", None) is None:
            dbg.upload()
        if getattr(dbg, "vbo", None) is not None:
            model = QtGui.QMatrix4x4()
            model.translate(view._debug_mesh_center.x(), view._debug_mesh_center.y(), 0.0)
            model.scale(view._debug_mesh_scale)
            mvp = proj_m * view_m * model
            view._mesh_program.bind()
            view._mesh_program.setUniformValue("u_mvp", mvp)
            color = view._debug_mesh_color
            view._mesh_program.setUniformValue(
                "u_color",
                QtGui.QVector4D(color.redF(), color.greenF(), color.blueF(), 0.9),
            )
            if dbg.vbo.bind():
                if getattr(view, "_mesh_pos_loc", -1) >= 0:
                    view._mesh_program.enableAttributeArray(view._mesh_pos_loc)
                    view._mesh_program.setAttributeBuffer(view._mesh_pos_loc, GL_FLOAT, 0, 3, 0)
                view._gl.glDrawArrays(GL_TRIANGLES, 0, dbg.count)
                dbg.vbo.release()
            view._mesh_program.release()

    if getattr(view, "_mesh_program", None) and getattr(view, "_debug_wire", None) and getattr(view, "_show_test_cube", False):
        dbg = view._debug_wire
        if getattr(dbg, "vbo", None) is None:
            dbg.upload()
        if getattr(dbg, "vbo", None) is not None:
            model = QtGui.QMatrix4x4()
            model.translate(view._debug_mesh_center.x(), view._debug_mesh_center.y(), 0.0)
            model.scale(view._debug_mesh_scale)
            mvp = proj_m * view_m * model
            view._mesh_program.bind()
            view._mesh_program.setUniformValue("u_mvp", mvp)
            color = view._debug_wire_color
            view._mesh_program.setUniformValue(
                "u_color",
                QtGui.QVector4D(color.redF(), color.greenF(), color.blueF(), 1.0),
            )
            depth_disabled = False
            try:
                view._gl.glDisable(GL_DEPTH_TEST)
                depth_disabled = True
            except Exception:
                depth_disabled = False
            if dbg.vbo.bind():
                if getattr(view, "_mesh_pos_loc", -1) >= 0:
                    view._mesh_program.enableAttributeArray(view._mesh_pos_loc)
                    view._mesh_program.setAttributeBuffer(view._mesh_pos_loc, GL_FLOAT, 0, 3, 0)
                view._gl.glDrawArrays(GL_LINES, 0, dbg.count)
                dbg.vbo.release()
            if depth_disabled:
                try:
                    view._gl.glEnable(GL_DEPTH_TEST)
                except Exception:
                    pass
            view._mesh_program.release()

    if getattr(view, "_vao", None) is not None:
        try:
            view._vao.release()
        except Exception:
            pass

def paint_example(view: Any) -> None:
    if getattr(view, "_example_program", None) is None or getattr(view, "_gl", None) is None:
        return

    if getattr(view, "_shader_error", False):
        view._gl.glClearColor(0.12, 0.12, 0.12, 1.0)
        view._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        return

    try:
        view._gl.glEnable(GL_DEPTH_TEST)
        view._gl.glDepthFunc(GL_LESS)
        view._gl.glDisable(GL_CULL_FACE)
    except Exception:
        pass

    if getattr(view, "_example_draw_count", 0) == 0 and getattr(view, "_example_pending_vertices", None) is None:
        vertices, _ = view._example_cube_data()
        view._upload_example_vertices(vertices)

    view._apply_example_pending()

    fov = max(10.0, min(120.0, float(getattr(view, "_example_fov", 60.0))))
    if abs(fov - float(getattr(view, "_example_fov", 60.0))) > 0.01:
        view._example_fov = fov
        view._update_example_projection()

    clear = getattr(view, "_example_clear_color", QtGui.QColor("#1a1f24"))
    view._gl.glClearColor(clear.redF(), clear.greenF(), clear.blueF(), 1.0)
    view._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

    view_m = view._example_view_matrix()

    if getattr(view, "_example_vao", None) is not None:
        try:
            view._example_vao.bind()
        except Exception:
            pass

    view._example_program.bind()
    view._example_program.setUniformValue("u_proj", view._example_proj)
    view._example_program.setUniformValue("u_view", view_m)

    stride = 7 * 4

    def bind_attributes(vbo) -> bool:
        if vbo is None or not vbo.bind():
            return False
        view._example_program.enableAttributeArray(0)
        view._example_program.setAttributeBuffer(0, GL_FLOAT, 0, 3, stride)
        view._example_program.enableAttributeArray(1)
        view._example_program.setAttributeBuffer(1, GL_FLOAT, 12, 4, stride)
        return True

    if getattr(view, "_example_grid_vbo", None) and getattr(view, "_example_grid_count", 0) > 0:
        grid_transform = QtGui.QMatrix4x4()
        view._example_program.setUniformValue("u_trans", grid_transform)
        if bind_attributes(view._example_grid_vbo):
            view._gl.glDrawArrays(GL_LINES, 0, view._example_grid_count)
            view._example_grid_vbo.release()

    if getattr(view, "_example_draw_count", 0) > 0 and getattr(view, "_example_vbo", None) is not None:
        view._example_program.setUniformValue("u_trans", view._example_transform)
        if bind_attributes(view._example_vbo):
            view._gl.glDrawArrays(GL_TRIANGLES, 0, view._example_draw_count)
            view._example_vbo.release()

    view._example_program.release()

    if getattr(view, "_example_vao", None) is not None:
        try:
            view._example_vao.release()
        except Exception:
            pass
