from __future__ import annotations

# Mouse interaction methods extracted from gl_view.py to reduce GraphGLView size.
_GV_MODULE = None


def _ensure_gl_view_globals():
    global _GV_MODULE
    if _GV_MODULE is None:
        from . import gl_view as _gv

        _GV_MODULE = _gv
        for _name, _value in vars(_gv).items():
            if _name not in globals():
                globals()[_name] = _value
    return _GV_MODULE


def _graph_gl_view_super(self):
    gv = _ensure_gl_view_globals()
    return super(gv.GraphGLView, self)

_MOUSE_METHOD_NAMES = [
    '_ray_from_mouse',
    'mousePressEvent',
    '_handle_mouse_press_moderngl',
    '_handle_mouse_retarget_viewport',
    '_handle_mouse_press_moderngl_retarget_joint',
    '_handle_mouse_press_moderngl_left_gizmo',
    '_handle_mouse_press_moderngl_left_gizmo_build_context',
    '_handle_mouse_press_moderngl_left_gizmo_build_context_dict',
    '_handle_mouse_press_moderngl_left_gizmo_owner_pos',
    '_handle_mouse_press_moderngl_left_gizmo_pick_viewport',
    '_handle_mouse_press_moderngl_left_gizmo_pick_matrices',
    '_handle_mouse_press_moderngl_left_gizmo_pick_basis',
    '_handle_mouse_press_moderngl_left_gizmo_dispatch',
    '_handle_mouse_press_moderngl_left_gizmo_dispatch_rotate',
    '_handle_mouse_press_moderngl_left_gizmo_dispatch_xform',
    '_handle_mouse_press_moderngl_left_gizmo_dispatch_xform_scale',
    '_handle_mouse_press_moderngl_left_gizmo_scale_pick_context',
    '_handle_mouse_press_moderngl_left_gizmo_dispatch_xform_translate',
    '_handle_mouse_press_moderngl_left_gizmo_translate_pick_context',
    '_handle_mouse_press_moderngl_left_gizmo_project_world',
    '_handle_mouse_press_moderngl_left_gizmo_mouse_dev_pos',
    '_handle_mouse_press_moderngl_left_gizmo_dist_pt_seg',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_state',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_dispatch',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_ready',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_hit',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_pick',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_pick_vectors',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_pick_begin_drag',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_begin_owner',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_vectors',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir_cache_invpv',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir_ray',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir_qvectors',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_begin_drag',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_pick',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_begin_owner',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_sync_q0',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_set_center',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_set_forward',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_try',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_prepare',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_basis',
    '_handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_begin_state',
    '_handle_mouse_press_moderngl_left_gizmo_axis_local',
    '_handle_mouse_press_moderngl_left_gizmo_owner_rotation_matrix',
    '_handle_mouse_press_moderngl_left_gizmo_scale_pick',
    '_handle_mouse_press_moderngl_left_gizmo_scale_prepare',
    '_handle_mouse_press_moderngl_left_gizmo_scale_basis',
    '_handle_mouse_press_moderngl_left_gizmo_scale_axis_proj',
    '_handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_translate',
    '_handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_scale',
    '_handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_pvtrs',
    '_handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_cube_axis_pos',
    '_handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_map',
    '_handle_mouse_press_moderngl_left_gizmo_scale_pick_axis',
    '_handle_mouse_press_moderngl_left_gizmo_scale_start',
    '_handle_mouse_press_moderngl_left_gizmo_scale_start_begin',
    '_handle_mouse_press_moderngl_left_gizmo_scale_start_mode',
    '_handle_mouse_press_moderngl_left_gizmo_scale_start_finalize',
    '_handle_mouse_press_moderngl_left_gizmo_scale_start_scl',
    '_handle_mouse_press_moderngl_left_gizmo_scale_begin_drag',
    '_handle_mouse_press_moderngl_left_gizmo_scale_start_uniform',
    '_handle_mouse_press_moderngl_left_gizmo_scale_start_axis',
    '_handle_mouse_press_moderngl_left_gizmo_translate_pick',
    '_handle_mouse_press_moderngl_left_gizmo_translate_pick_start',
    '_handle_mouse_press_moderngl_left_gizmo_translate_prepare',
    '_handle_mouse_press_moderngl_left_gizmo_translate_axis_dirs',
    '_handle_mouse_press_moderngl_left_gizmo_translate_axis_proj',
    '_handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled',
    '_handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled_pvtrs',
    '_handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled_map',
    '_handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale',
    '_handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale_multiplier',
    '_handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale_target',
    '_handle_mouse_press_moderngl_left_gizmo_translate_project_local',
    '_handle_mouse_press_moderngl_left_gizmo_axis_proj_max_len',
    '_handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_fallback',
    '_handle_mouse_press_moderngl_left_gizmo_translate_is_far_from_center',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_offsets',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_try_view',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_try_axis',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_view_drag',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_view_init_drag',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_view_set_plane_hit',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_view_finalize',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_view_is_center_hit',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_view_plane_normal',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_view_ray',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_view_plane_hit',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_axis_drag',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_axis_best',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_axis_begin',
    '_handle_mouse_press_moderngl_left_gizmo_translate_start_axis_finalize',
    '_handle_mouse_press_moderngl_left_gizmo_translate_begin_drag',
    '_handle_mouse_press_moderngl_left_gizmo_owner_is_splat',
    '_handle_mouse_press_moderngl_left_orbit_start',
    '_handle_mouse_press_moderngl_left_pick_start',
    '_handle_mouse_press_moderngl_middle_start',
    '_handle_mouse_press_moderngl_right_start',
    '_handle_mouse_press_moderngl_right_start_fly',
    '_handle_mouse_press_moderngl_right_start_fly_nav_state',
    '_handle_mouse_press_moderngl_right_start_fly_anchor_from_event',
    '_handle_mouse_press_moderngl_right_start_fly_camera_state',
    '_handle_mouse_press_moderngl_right_start_fly_ui_state',
    '_handle_mouse_press_moderngl_right_start_zoom',
    '_handle_mouse_press_moderngl_right_start_zoom_center',
    '_handle_mouse_press_moderngl_right_start_zoom_ray',
    '_handle_mouse_press_moderngl_right_start_zoom_cam_start',
    '_handle_mouse_press_moderngl_right_start_zoom_cam_dir',
    '_handle_mouse_press_example_pipeline',
    '_handle_mouse_press_legacy_left',
    '_handle_mouse_press_legacy_middle',
    '_handle_mouse_press_legacy_right',
    'mouseMoveEvent',
    '_handle_mouse_move_moderngl',
    '_handle_mouse_move_moderngl_retarget_drag',
    '_handle_mouse_move_moderngl_fps_nav',
    '_handle_mouse_move_moderngl_fps_nav_active',
    '_handle_mouse_move_moderngl_fps_nav_fly_look',
    '_handle_mouse_move_moderngl_fps_nav_anchor',
    '_handle_mouse_move_moderngl_fps_nav_global_pos',
    '_handle_mouse_move_moderngl_fps_nav_drag_look',
    '_handle_mouse_move_moderngl_fps_nav_finish',
    '_log_mouse_move_rot_shared_state',
    '_update_mouse_move_xform_hover',
    '_handle_mouse_move_moderngl_rot_shared_view_drag',
    '_handle_mouse_move_moderngl_rot_shared_view_drag_prepare',
    '_handle_mouse_move_moderngl_rot_shared_view_drag_qcur',
    '_handle_mouse_move_moderngl_rot_shared_view_drag_apply',
    '_handle_mouse_move_moderngl_rot_shared_arc_drag',
    '_handle_mouse_move_moderngl_rot_shared_arc_drag_prepare',
    '_handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_owner',
    '_handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_mouse',
    '_handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_basis',
    '_handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_qnew',
    '_handle_mouse_move_moderngl_rot_shared_arc_drag_apply',
    '_handle_mouse_move_moderngl_rot_shared_axis_drag',
    '_handle_mouse_move_moderngl_rot_shared_axis_prepare',
    '_handle_mouse_move_moderngl_rot_shared_axis_prepare_from_ray',
    '_handle_mouse_move_moderngl_rot_shared_axis_prepare_owner',
    '_handle_mouse_move_moderngl_rot_shared_axis_prepare_mouse',
    '_handle_mouse_move_moderngl_rot_shared_axis_prepare_log',
    '_handle_mouse_move_moderngl_rot_shared_axis_prepare_qnew',
    '_handle_mouse_move_moderngl_rot_shared_axis_prepare_ray',
    '_handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_viewport',
    '_handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_invpv',
    '_handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_unproject',
    '_handle_mouse_move_moderngl_rot_shared_axis_prepare_ring_dir',
    '_handle_mouse_move_moderngl_rot_shared_axis_apply',
    '_handle_mouse_move_moderngl_rot_shared_axis_apply_qapply',
    '_handle_mouse_move_moderngl_rot_shared_axis_apply_cache_quat',
    '_handle_mouse_move_moderngl_rot_shared_axis_apply_candidates',
    '_handle_mouse_move_moderngl_rot_shared_axis_apply_best',
    '_handle_mouse_move_moderngl_rot_shared_axis_apply_set_owner',
    '_handle_mouse_move_moderngl_xform_drag',
    '_handle_mouse_move_moderngl_xform_drag_context',
    '_handle_mouse_move_moderngl_xform_ray',
    '_handle_mouse_move_moderngl_xform_translate_drag',
    '_handle_mouse_move_moderngl_xform_translate_drag_new_pos',
    '_handle_mouse_move_moderngl_xform_translate_drag_finish',
    '_handle_mouse_move_moderngl_xform_translate_compute_pos',
    '_handle_mouse_move_moderngl_xform_translate_compute_pos_view',
    '_handle_mouse_move_moderngl_xform_translate_compute_pos_axis',
    '_handle_mouse_move_moderngl_xform_translate_axis_world',
    '_handle_mouse_move_moderngl_xform_translate_axis_param',
    '_handle_mouse_move_moderngl_xform_translate_apply_owner',
    '_handle_mouse_move_moderngl_xform_translate_apply_owner_pos',
    '_handle_mouse_move_moderngl_xform_translate_apply_owner_pivot',
    '_handle_mouse_move_moderngl_xform_translate_apply_owner_sync',
    '_handle_mouse_move_moderngl_xform_scale_drag',
    '_handle_mouse_move_moderngl_xform_scale_drag_new_scale',
    '_handle_mouse_move_moderngl_xform_scale_get_start_scl',
    '_handle_mouse_move_moderngl_xform_scale_uniform_drag',
    '_handle_mouse_move_moderngl_xform_scale_axis_drag',
    '_handle_mouse_move_moderngl_xform_scale_axis_drag_apply_factor',
    '_handle_mouse_move_moderngl_xform_scale_axis_world',
    '_handle_mouse_move_moderngl_xform_scale_axis_factor',
    '_handle_mouse_move_moderngl_xform_scale_apply_owner',
    '_handle_mouse_move_moderngl_arcball_drag',
    '_handle_mouse_move_moderngl_pan_drag',
    '_handle_mouse_move_moderngl_pan_drag_scale',
    '_handle_mouse_move_moderngl_pan_drag_scale_zoom_ref',
    '_handle_mouse_move_moderngl_pan_drag_scale_ref_zoom',
    '_handle_mouse_move_moderngl_pan_drag_scale_boost',
    '_handle_mouse_move_moderngl_pan_drag_basis',
    '_handle_mouse_move_moderngl_zoom_drag',
    '_handle_mouse_move_moderngl_zoom_drag_infinite',
    '_handle_mouse_move_moderngl_zoom_drag_finite',
    '_handle_mouse_move_example_pipeline',
    '_handle_mouse_move_example_pipeline_orbit',
    '_handle_mouse_move_example_pipeline_pan',
    '_handle_mouse_move_example_pipeline_dolly',
    '_handle_mouse_move_legacy_orbit',
    '_handle_mouse_move_legacy_pan',
    '_handle_mouse_move_legacy_dolly',
    '_handle_mouse_release_rot_shared_arc',
    '_handle_mouse_release_rot_shared_view',
    '_handle_mouse_release_rot_shared_axis',
    '_handle_mouse_release_moderngl',
    '_handle_mouse_release_moderngl_retarget_drag',
    '_handle_mouse_release_moderngl_right_button_nav',
    '_handle_mouse_release_moderngl_end_xform_drag',
    '_handle_mouse_release_moderngl_commit_retarget_pose_if_needed',
    '_handle_mouse_release_moderngl_log_splat_drag_end',
    '_handle_mouse_release_moderngl_reset_xform_drag_state',
    '_handle_mouse_release_moderngl_release_mouse_grab',
    '_handle_mouse_release_moderngl_click_pick',
    '_handle_mouse_release_moderngl_click_pick_is_click',
    '_handle_mouse_release_moderngl_click_pick_owner',
    '_handle_mouse_release_moderngl_click_pick_viewport',
    '_handle_mouse_release_moderngl_click_pick_dpr',
    '_handle_mouse_release_moderngl_click_pick_apply',
    '_handle_mouse_release_moderngl_pick_owner',
    '_handle_mouse_release_moderngl_pick_owner_select',
    '_handle_mouse_release_moderngl_pick_owner_place_gizmo',
    '_handle_mouse_release_moderngl_pick_owner_place_gizmo_splat',
    '_handle_mouse_release_moderngl_pick_owner_place_gizmo_mesh',
    '_handle_mouse_release_moderngl_pick_owner_is_splat',
    '_handle_mouse_release_moderngl_pick_owner_xf_pos',
    '_handle_mouse_release_moderngl_pick_owner_splat_pivot',
    '_handle_mouse_release_moderngl_pick_owner_splat_bounds_center',
    '_handle_mouse_release_moderngl_pick_owner_set_splat_pos',
    '_handle_mouse_release_moderngl_pick_empty',
    '_handle_mouse_release_moderngl_end_camera_interactions',
    '_handle_mouse_release_example_pipeline',
    '_handle_mouse_release_legacy',
    'mouseReleaseEvent',
    'wheelEvent',
]

def _ray_from_mouse(self, pos: QtCore.QPoint):
    if not self._use_moderngl or np is None:
        return None
    try:
        renderer = getattr(self, "_mgl_renderer", None) or self
        Pn = getattr(renderer, "_mgl_pick_proj", None)
        Vn = getattr(renderer, "_mgl_pick_view", None)
        Mn = getattr(renderer, "_mgl_pick_model", None)
        if Pn is None or Vn is None or Mn is None:
            return None

        dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())
        vw = float(self.width()) * dpr
        vh = float(self.height()) * dpr
        px = float(pos.x()) * dpr
        py = float(pos.y()) * dpr

        PV = (
            np.asarray(Pn, dtype=np.float32)
            @ np.asarray(Vn, dtype=np.float32)
            @ np.asarray(Mn, dtype=np.float32)
        ).astype(np.float32)
        invPV = np.linalg.inv(PV)

        ndc_fn = getattr(renderer, "_mgl_screen_to_active_ndc", None)
        ndc = ndc_fn(px, py, vw, vh) if callable(ndc_fn) else None
        if ndc is None:
            return None
        x, y = ndc
        near = np.array([x, y, -1.0, 1.0], dtype=np.float32)
        far = np.array([x, y, 1.0, 1.0], dtype=np.float32)
        pN = invPV @ near
        pF = invPV @ far
        pN = pN[:3] / pN[3]
        pF = pF[:3] / pF[3]
        ray_o = pN
        ray_d = pF - pN
        rn = float(np.linalg.norm(ray_d))
        if rn <= 1e-8:
            return None
        ray_d /= rn
        return ray_o.astype("f4"), ray_d.astype("f4")
    except Exception:
        return None

def mousePressEvent(self, e):
    # Keep branch order stable so press behavior remains unchanged.
    if self._handle_mouse_press_moderngl(e):
        return
    if self._handle_mouse_press_example_pipeline(e):
        return
    if self._handle_mouse_press_legacy_left(e):
        return
    if self._handle_mouse_press_legacy_middle(e):
        return
    if self._handle_mouse_press_legacy_right(e):
        return
    _graph_gl_view_super(self).mousePressEvent(e)

def _handle_mouse_press_moderngl(self, e):
    # ModernGL path: gizmo interaction, scene picking, and Alt camera controls.
    if self._use_moderngl:
        try:
            if QtWidgets.QApplication.mouseGrabber() is self:
                self.releaseMouse()
        except Exception:
            pass
        alt_pressed = False
        try:
            alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
        except Exception:
            alt_pressed = False
        prefer_gizmo = False
        try:
            owner = str(getattr(self, "_xform_gizmo_owner", "") or "").strip()
            owner_kind = str(getattr(self, "_xform_gizmo_owner_kind", "") or "").strip().lower()
            if owner and owner_kind == "scene_skeleton_joint":
                prefer_gizmo = True
            elif owner:
                renderer = getattr(self, "_mgl_renderer", None) or self
                decode_joint = getattr(renderer, "_mgl_scene_skeleton_decode_joint_owner", None)
                prefer_gizmo = bool(callable(decode_joint) and decode_joint(owner))
        except Exception:
            prefer_gizmo = False
        if bool(prefer_gizmo) and self._handle_mouse_press_moderngl_left_gizmo(e):
            return True
        if self._handle_mouse_press_moderngl_retarget_joint(e, alt_pressed):
            return True
        if (not bool(prefer_gizmo)) and self._handle_mouse_press_moderngl_left_gizmo(e):
            return True
        if self._handle_mouse_press_moderngl_left_orbit_start(e, alt_pressed):
            return True
        if self._handle_mouse_press_moderngl_left_pick_start(e, alt_pressed):
            return True
        if self._handle_mouse_press_moderngl_middle_start(e, alt_pressed):
            return True
        if self._handle_mouse_press_moderngl_right_start(e, alt_pressed):
            return True
    return False

def _handle_mouse_retarget_viewport(self, e):
    dpr = 1.0
    try:
        dpr = float(self.devicePixelRatioF())
    except Exception:
        try:
            dpr = float(self.devicePixelRatio())
        except Exception:
            dpr = 1.0
    return (
        int(float(e.x()) * dpr),
        int(float(e.y()) * dpr),
        int(float(self.width()) * dpr),
        int(float(self.height()) * dpr),
    )

def _handle_mouse_press_moderngl_retarget_joint(self, e, alt_pressed):
    if e.button() not in (QtCore.Qt.LeftButton, QtCore.Qt.RightButton) or bool(alt_pressed):
        return False
    renderer = getattr(self, "_mgl_renderer", None) or self
    try:
        scene_skeleton_active = bool(str(getattr(renderer, "_mgl_scene_skeleton_active_owner", "") or "").strip())
        if e.button() == QtCore.Qt.RightButton and scene_skeleton_active:
            return False
    except Exception:
        pass
    try:
        nav_right_click = bool(e.button() == QtCore.Qt.RightButton) and (
            bool(getattr(self, "_fly_mode_enabled", False))
            or bool(getattr(self, "_camera_select_lock_enabled", False))
            or bool(getattr(self, "_fps_nav_active", False))
        )
        if nav_right_click:
            return False
    except Exception:
        pass
    is_unlink_click = bool(e.button() == QtCore.Qt.RightButton)
    pick = getattr(renderer, "pick_retarget_joint_at", None)
    if not callable(pick):
        return False
    try:
        px, py, vw, vh = self._handle_mouse_retarget_viewport(e)
        handle = pick(px, py, vw, vh)
    except Exception:
        handle = None
    if not isinstance(handle, dict):
        return False
    click = getattr(renderer, "_mgl_retarget_handle_click", None)
    handled = False
    if callable(click):
        try:
            if is_unlink_click:
                handled = bool(click(dict(handle), unlink=True))
            else:
                handled = bool(click(dict(handle)))
        except Exception:
            pass
    if is_unlink_click and not handled:
        return False
    self._retarget_joint_drag = None
    try:
        self._mgl_pick_press_pos = None
    except Exception:
        pass
    try:
        self.setCursor(QtCore.Qt.CrossCursor)
    except Exception:
        pass
    e.accept()
    return True

def _handle_mouse_press_moderngl_left_gizmo(self, e):
    if e.button() != QtCore.Qt.LeftButton:
        return False
    try:
        ctx = self._handle_mouse_press_moderngl_left_gizmo_build_context(e)
        if ctx is None:
            return False
        return self._handle_mouse_press_moderngl_left_gizmo_dispatch(e=e, ctx=ctx)
    except Exception as exc:
        print("[GIZMO_PICK] failed:", exc, flush=True)
    return False

def _handle_mouse_press_moderngl_left_gizmo_build_context(self, e):
    owner, pos = self._handle_mouse_press_moderngl_left_gizmo_owner_pos()
    if owner in (None, "") or pos is None or (np is None):
        return None

    mode = getattr(self, "_xform_gizmo_mode", "translate") or "translate"

    dpr, vw, vh = self._handle_mouse_press_moderngl_left_gizmo_pick_viewport()
    renderer = getattr(self, "_mgl_renderer", None) or self
    matrices = self._handle_mouse_press_moderngl_left_gizmo_pick_matrices(renderer=renderer)
    if matrices is None:
        return None
    P, V, M, PV = matrices

    g, axis_len, axes, p0 = self._handle_mouse_press_moderngl_left_gizmo_pick_basis(
        pos=pos,
        PV=PV,
        vw=vw,
        vh=vh,
    )

    px_dev, py_dev = self._handle_mouse_press_moderngl_left_gizmo_mouse_dev_pos(e=e, dpr=dpr)
    return self._handle_mouse_press_moderngl_left_gizmo_build_context_dict(
        owner=owner,
        mode=mode,
        dpr=dpr,
        vw=vw,
        vh=vh,
        renderer=renderer,
        P=P,
        V=V,
        M=M,
        PV=PV,
        g=g,
        axis_len=axis_len,
        axes=axes,
        p0=p0,
        px_dev=px_dev,
        py_dev=py_dev,
    )

def _handle_mouse_press_moderngl_left_gizmo_build_context_dict(
    self,
    *,
    owner,
    mode,
    dpr,
    vw,
    vh,
    renderer,
    P,
    V,
    M,
    PV,
    g,
    axis_len,
    axes,
    p0,
    px_dev,
    py_dev,
):
    return {
        "owner": owner,
        "mode": mode,
        "dpr": dpr,
        "vw": vw,
        "vh": vh,
        "renderer": renderer,
        "P": P,
        "V": V,
        "M": M,
        "PV": PV,
        "g": g,
        "axis_len": axis_len,
        "axes": axes,
        "p0": p0,
        "px_dev": px_dev,
        "py_dev": py_dev,
    }

def _handle_mouse_press_moderngl_left_gizmo_owner_pos(self):
    owner = getattr(self, "_xform_gizmo_owner", None)
    pos = getattr(self, "_xform_gizmo_pos", None)
    return owner, pos

def _handle_mouse_press_moderngl_left_gizmo_pick_viewport(self):
    dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())
    rect_fn = getattr(self, "_mgl_active_viewport_qrectf", None)
    rect = rect_fn() if callable(rect_fn) else None
    if isinstance(rect, QtCore.QRectF) and rect.width() > 1.0 and rect.height() > 1.0:
        vw = float(rect.width()) * dpr
        vh = float(rect.height()) * dpr
    else:
        vw = float(self.width()) * dpr
        vh = float(self.height()) * dpr
    return dpr, vw, vh

def _handle_mouse_press_moderngl_left_gizmo_pick_matrices(self, *, renderer):
    P = getattr(renderer, "_mgl_pick_proj", None)
    V = getattr(renderer, "_mgl_pick_view", None)
    M = getattr(renderer, "_mgl_pick_model", None)
    if P is None or V is None or M is None:
        return None
    PV = (P @ V @ M).astype("f4")
    return P, V, M, PV

def _handle_mouse_press_moderngl_left_gizmo_pick_basis(self, *, pos, PV, vw, vh):
    g = np.array([float(pos[0]), float(pos[1]), float(pos[2])], dtype="f4")
    axis_len = 1.0
    axes = {
        "x": np.array([1.0, 0.0, 0.0], dtype="f4"),
        "y": np.array([0.0, 1.0, 0.0], dtype="f4"),
        "z": np.array([0.0, 0.0, 1.0], dtype="f4"),
    }
    p0 = self._handle_mouse_press_moderngl_left_gizmo_project_world(
        world_xyz=g,
        PV=PV,
        vw=vw,
        vh=vh,
    )
    return g, axis_len, axes, p0

def _handle_mouse_press_moderngl_left_gizmo_dispatch(
    self,
    *,
    e,
    ctx,
):
    handled, _rot_shared, _hit = self._handle_mouse_press_moderngl_left_gizmo_dispatch_rotate(
        e=e,
        ctx=ctx,
    )
    if handled:
        return True
    return self._handle_mouse_press_moderngl_left_gizmo_dispatch_xform(e=e, ctx=ctx)

def _handle_mouse_press_moderngl_left_gizmo_dispatch_rotate(
    self,
    *,
    e,
    ctx,
):
    # --- shared rotate gizmo drag start (NEW, RotateGizmoShared only) ---
    handled, rot_shared, hit = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick(
        e=e,
        ctx=ctx,
    )
    if handled:
        return True, rot_shared, hit

    # --- ROT_SHARED center-disc arcball drag (smoketest style) ---
    if self._handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick(
        e=e,
        ctx=ctx,
        rot_shared=rot_shared,
        hit=hit,
    ):
        return True, rot_shared, hit
    return False, rot_shared, hit

def _handle_mouse_press_moderngl_left_gizmo_dispatch_xform(
    self,
    *,
    e,
    ctx,
):
    if self._handle_mouse_press_moderngl_left_gizmo_dispatch_xform_scale(e=e, ctx=ctx):
        return True
    return self._handle_mouse_press_moderngl_left_gizmo_dispatch_xform_translate(e=e, ctx=ctx)

def _handle_mouse_press_moderngl_left_gizmo_dispatch_xform_scale(
    self,
    *,
    e,
    ctx,
):
    # --- scale gizmo drag start (axis cubes + center) ---
    scale_ctx = self._handle_mouse_press_moderngl_left_gizmo_scale_pick_context(
        p0=ctx["p0"],
        mode=ctx["mode"],
        owner=ctx["owner"],
        g=ctx["g"],
        axis_len=ctx["axis_len"],
        dpr=ctx["dpr"],
        vw=ctx["vw"],
        vh=ctx["vh"],
        P=ctx["P"],
        V=ctx["V"],
        M=ctx["M"],
        renderer=ctx["renderer"],
        px_dev=ctx["px_dev"],
        py_dev=ctx["py_dev"],
    )
    return self._handle_mouse_press_moderngl_left_gizmo_scale_pick(
        e=e,
        ctx=scale_ctx,
    )

def _handle_mouse_press_moderngl_left_gizmo_scale_pick_context(
    self,
    *,
    p0,
    mode,
    owner,
    g,
    axis_len,
    dpr,
    vw,
    vh,
    P,
    V,
    M,
    renderer,
    px_dev,
    py_dev,
):
    return {
        "p0": p0,
        "mode": mode,
        "owner": owner,
        "g": g,
        "axis_len": axis_len,
        "dpr": dpr,
        "vw": vw,
        "vh": vh,
        "P": P,
        "V": V,
        "M": M,
        "renderer": renderer,
        "px_dev": px_dev,
        "py_dev": py_dev,
    }

def _handle_mouse_press_moderngl_left_gizmo_dispatch_xform_translate(
    self,
    *,
    e,
    ctx,
):
    project = lambda world_xyz: self._handle_mouse_press_moderngl_left_gizmo_project_world(
        world_xyz=world_xyz,
        PV=ctx["PV"],
        vw=ctx["vw"],
        vh=ctx["vh"],
    )
    translate_ctx = self._handle_mouse_press_moderngl_left_gizmo_translate_pick_context(
        p0=ctx["p0"],
        mode=ctx["mode"],
        owner=ctx["owner"],
        g=ctx["g"],
        axes=ctx["axes"],
        axis_len=ctx["axis_len"],
        dpr=ctx["dpr"],
        vw=ctx["vw"],
        vh=ctx["vh"],
        P=ctx["P"],
        V=ctx["V"],
        M=ctx["M"],
        renderer=ctx["renderer"],
        px_dev=ctx["px_dev"],
        py_dev=ctx["py_dev"],
    )
    return self._handle_mouse_press_moderngl_left_gizmo_translate_pick(
        e=e,
        ctx=translate_ctx,
        project=project,
        dist_pt_seg=self._handle_mouse_press_moderngl_left_gizmo_dist_pt_seg,
    )

def _handle_mouse_press_moderngl_left_gizmo_translate_pick_context(
    self,
    *,
    p0,
    mode,
    owner,
    g,
    axes,
    axis_len,
    dpr,
    vw,
    vh,
    P,
    V,
    M,
    renderer,
    px_dev,
    py_dev,
):
    return {
        "p0": p0,
        "mode": mode,
        "owner": owner,
        "g": g,
        "axes": axes,
        "axis_len": axis_len,
        "dpr": dpr,
        "vw": vw,
        "vh": vh,
        "P": P,
        "V": V,
        "M": M,
        "renderer": renderer,
        "px_dev": px_dev,
        "py_dev": py_dev,
    }

def _handle_mouse_press_moderngl_left_gizmo_project_world(self, *, world_xyz, PV, vw, vh):
    return _gv_project_world(world_xyz=world_xyz, PV=PV, vw=vw, vh=vh)

def _handle_mouse_press_moderngl_left_gizmo_mouse_dev_pos(self, *, e, dpr):
    # mouse in device pixels (must match project() output space)
    try:
        mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
    except Exception:
        mp = QtCore.QPointF(e.x(), e.y())
    rect_fn = getattr(self, "_mgl_active_viewport_qrectf", None)
    rect = rect_fn() if callable(rect_fn) else None
    off_x = float(rect.left()) if isinstance(rect, QtCore.QRectF) else 0.0
    off_y = float(rect.top()) if isinstance(rect, QtCore.QRectF) else 0.0
    return (float(mp.x()) - off_x) * float(dpr), (float(mp.y()) - off_y) * float(dpr)

def _handle_mouse_press_moderngl_left_gizmo_dist_pt_seg(self, px2, py2, ax, ay, bx, by):
    return _gv_dist_pt_seg(px2=px2, py2=py2, ax=ax, ay=ay, bx=bx, by=by)

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick(
    self,
    *,
    e,
    ctx,
):
    if ctx["mode"] != "rotate":
        return False, None, None

    rot_shared, hit, mp = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_state(
        e=e,
        owner=ctx["owner"],
    )
    if mp is None:
        return False, rot_shared, hit

    handled = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_dispatch(
        e=e,
        ctx=ctx,
        hit=hit,
        rot_shared=rot_shared,
        mp=mp,
    )
    return handled, rot_shared, hit

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_state(self, *, e, owner):
    rot_shared = getattr(self, "_rot_shared", None)
    center = getattr(self, "_rot_shared_center_px", None)
    mvp = getattr(self, "_rot_shared_mvp", None)
    if not self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_ready(
        rot_shared=rot_shared,
        center=center,
        mvp=mvp,
        owner=owner,
    ):
        return rot_shared, None, None

    mp, hit = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_hit(
        e=e,
        rot_shared=rot_shared,
        center=center,
        mvp=mvp,
    )
    self._mgl_log(
        f"[ROT_SHARED] pick hit={hit} mode={getattr(self,'_xform_gizmo_mode',None)} owner={owner}"
    )
    return rot_shared, hit, mp

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_dispatch(
    self,
    *,
    e,
    ctx,
    hit,
    rot_shared,
    mp,
):
    if self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_pick(
        e=e,
        ctx=ctx,
        hit=hit,
        rot_shared=rot_shared,
        mp=mp,
    ):
        return True
    return self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_pick(
        e=e,
        owner=ctx["owner"],
        g=ctx["g"],
        p0=ctx["p0"],
        hit=hit,
        rot_shared=rot_shared,
        V=ctx["V"],
        M=ctx["M"],
    )

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_ready(self, *, rot_shared, center, mvp, owner):
    return (
        rot_shared is not None
        and center is not None
        and mvp is not None
        and owner not in (None, "")
    )

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_pick_hit(self, *, e, rot_shared, center, mvp):
    mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
    rect = getattr(self, "_rot_shared_viewport_rect", None)
    if not isinstance(rect, QtCore.QRectF) or rect.width() <= 1.0 or rect.height() <= 1.0:
        rect_fn = getattr(self, "_mgl_active_viewport_qrectf", None)
        rect = rect_fn() if callable(rect_fn) else None
    if not isinstance(rect, QtCore.QRectF) or rect.width() <= 1.0 or rect.height() <= 1.0:
        rect = QtCore.QRectF(self.rect())
    viewport_w = max(1, int(round(float(rect.width()))))
    viewport_h = max(1, int(round(float(rect.height()))))
    viewport_origin = QtCore.QPointF(float(rect.left()), float(rect.top()))

    # match smoketest-style picking (same args as hover/draw)
    band = max(12.0, float(rot_shared.xyz_ring_radius_px()) * 0.14)

    clip_enabled = bool(getattr(self, "_rot_clip_enabled", True))
    back_clip_cos = -math.cos(math.pi * float(getattr(self, "_rot_clip_frac", 0.30)))

    view_dir_local = getattr(self, "_rot_shared_view_dir_local", None)
    if view_dir_local is None:
        view_dir_local = QtGui.QVector3D(0.0, 0.0, 1.0)

    hit = rot_shared.pick_axis_2d(
        widget=self,
        center=center,
        mouse_px=QtCore.QPointF(mp),  # logical px
        viewport_w=viewport_w,  # logical px (must match center/mouse)
        viewport_h=viewport_h,
        mvp=mvp,
        view_dir_local=view_dir_local,
        back_clip_cos=float(back_clip_cos),
        clip_enabled=bool(clip_enabled),
        threshold_px=float(band),
        viewport_origin=viewport_origin,
    )
    return mp, hit

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_pick(
    self,
    *,
    e,
    ctx,
    hit,
    rot_shared,
    mp,
):
    if hit not in ("x", "y", "z"):
        return False

    self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_begin_owner(owner=ctx["owner"])
    vectors = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_pick_vectors(
        owner=ctx["owner"],
        hit=hit,
        g=ctx["g"],
        mp=mp,
        dpr=ctx["dpr"],
        vw=ctx["vw"],
        vh=ctx["vh"],
        PV=ctx["PV"],
    )
    if vectors is None:
        return False
    q0, axis_world, start_dir = vectors

    self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_pick_begin_drag(
        owner=ctx["owner"],
        rot_shared=rot_shared,
        hit=hit,
        q0=q0,
        axis_world=axis_world,
        start_dir=start_dir,
    )

    e.accept()
    return True

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_pick_vectors(
    self,
    *,
    owner,
    hit,
    g,
    mp,
    dpr,
    vw,
    vh,
    PV,
):
    q0, axis_world, center_w = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_vectors(
        owner=owner,
        hit=hit,
        g=g,
    )
    start_dir = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir(
        mp=mp,
        dpr=dpr,
        vw=vw,
        vh=vh,
        PV=PV,
        center_w=center_w,
        axis_world=axis_world,
    )
    if start_dir is None:
        return None
    return q0, axis_world, start_dir

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_pick_begin_drag(
    self,
    *,
    owner,
    rot_shared,
    hit,
    q0,
    axis_world,
    start_dir,
):
    start_rot_deg, _is_splat = self._get_owner_rot_deg(owner)
    self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_begin_drag(
        rot_shared=rot_shared,
        hit=hit,
        q0=q0,
        axis_world=axis_world,
        start_dir=start_dir,
        start_rot_deg=start_rot_deg,
    )

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_begin_owner(self, *, owner):
    self._rot_shared_owner = owner
    self._begin_xform_history(owner)
    self._begin_xform_history(owner)

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_vectors(self, *, owner, hit, g):
    q0 = self._rot_shared_sync_q0_from_owner(owner)

    axis_local = (
        QtGui.QVector3D(1.0, 0.0, 0.0)
        if hit == "x"
        else (QtGui.QVector3D(0.0, 1.0, 0.0) if hit == "y" else QtGui.QVector3D(0.0, 0.0, 1.0))
    )
    axis_world = q0.rotatedVector(axis_local)
    if axis_world.length() > 1e-6:
        axis_world = axis_world / axis_world.length()
    else:
        axis_world = axis_local

    center_w = QtGui.QVector3D(float(g[0]), float(g[1]), float(g[2]))
    self._rot_shared_axis_center_world = center_w
    return q0, axis_world, center_w

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir(
    self,
    *,
    mp,
    dpr,
    vw,
    vh,
    PV,
    center_w,
    axis_world,
):
    invPV = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir_cache_invpv(
        PV=PV,
        vw=vw,
        vh=vh,
        dpr=dpr,
    )
    if invPV is None:
        return None

    ray = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir_ray(
        mp=mp,
        dpr=dpr,
        vw=vw,
        vh=vh,
        invPV=invPV,
    )
    if ray is None:
        return None

    ro, rd = self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir_qvectors(ray=ray)
    # Smoketest-style: closest point on ray to gizmo center, then project onto ring plane
    return self._axis_ring_dir_world(
        cam=ro,  # ro is fine as ray origin (it lies on the same ray)
        ray_d=rd,
        center_w=center_w,
        axis_world=axis_world,
    )

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir_cache_invpv(self, *, PV, vw, vh, dpr):
    # Cache invPV + viewport info for mouseMoveEvent (so move does not need P/V/M)
    try:
        invPV = np.linalg.inv(PV)
    except Exception as ex:
        print("[ROT_SHARED_AXIS_BEGIN_ERR] invPV", repr(ex), flush=True)
        return None
    self._rot_shared_invPV = invPV
    self._rot_shared_vw = float(vw)
    self._rot_shared_vh = float(vh)
    self._rot_shared_dpr = float(dpr)
    return invPV

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir_ray(self, *, mp, dpr, vw, vh, invPV):
    rect_fn = getattr(self, "_mgl_active_viewport_qrectf", None)
    rect = rect_fn() if callable(rect_fn) else None
    off_x = float(rect.left()) if isinstance(rect, QtCore.QRectF) else 0.0
    off_y = float(rect.top()) if isinstance(rect, QtCore.QRectF) else 0.0
    px = (float(mp.x()) - off_x) * dpr
    py = (float(mp.y()) - off_y) * dpr
    ray = _gv_ray_from_screen(invPV=invPV, px=px, py=py, vw=vw, vh=vh)
    if ray is None:
        print("[ROT_SHARED_AXIS_BEGIN_ERR] ray", flush=True)
    return ray

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_start_dir_qvectors(self, *, ray):
    ray_o, ray_d = ray
    ro = QtGui.QVector3D(float(ray_o[0]), float(ray_o[1]), float(ray_o[2]))
    rd = QtGui.QVector3D(float(ray_d[0]), float(ray_d[1]), float(ray_d[2]))
    return ro, rd

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_axis_begin_drag(
    self,
    *,
    rot_shared,
    hit,
    q0,
    axis_world,
    start_dir,
    start_rot_deg,
):
    # cache axis name + start euler for an axis-only update (prevents X/Z drift when dragging Y)
    self._rot_shared_axis = str(hit)
    self._rot_shared_axis_start_euler_deg = (
        float(start_rot_deg[0]),
        float(start_rot_deg[1]),
        float(start_rot_deg[2]),
    )
    self._rot_shared_axis_last_ang_deg = 0.0

    rot_shared.begin_axis_drag(
        axis=str(hit),
        start_rot=q0,
        axis_world=axis_world,
        start_dir=start_dir,
        start_euler_deg=(float(start_rot_deg[0]), float(start_rot_deg[1]), float(start_rot_deg[2])),
    )

    rot_shared.drag_axis.last_dir = QtGui.QVector3D(start_dir)

    # keep continuity so cur_dir can't flip 180 degrees mid-drag
    try:
        rot_shared.drag_axis.last_dir = QtGui.QVector3D(start_dir)
    except Exception:
        rot_shared.drag_axis.last_dir = start_dir

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_pick(
    self,
    *,
    e,
    owner,
    g,
    p0,
    hit,
    rot_shared,
    V,
    M,
):
    if hit != "view":
        return False

    self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_begin_owner(owner=owner)
    self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_sync_q0(owner=owner)
    self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_set_center(p0=p0)
    self._handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_set_forward(g=g, V=V, M=M)

    try:
        rot_shared.begin_view_ring_drag()  # smoketest-style: no ray math here
    except Exception:
        pass

    e.accept()
    return True

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_begin_owner(self, *, owner):
    self._rot_shared_owner = owner
    self._begin_xform_history(owner)

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_sync_q0(self, *, owner):
    # Persisted quaternion for this owner (same idea as axis rings).
    q0 = None
    try:
        q0 = self._rot_owner_quat.get(owner)
    except Exception:
        q0 = None

    if q0 is None:
        start_rot_deg, is_splat = self._get_owner_rot_deg(owner)
        self._rot_shared_start_rot = start_rot_deg
        self._rot_shared_is_splat = bool(is_splat)

        try:
            rx = float(start_rot_deg[0])
            ry = float(start_rot_deg[1])
            rz = float(start_rot_deg[2])
        except Exception:
            rx, ry, rz = 0.0, 0.0, 0.0

        q0 = self._rot_shared_q_from_euler_deg((rx, ry, rz))
        try:
            self._rot_owner_quat[owner] = q0
        except Exception:
            pass

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_set_center(self, *, p0):
    # Store view ring center in DEVICE pixels (project() returns device px).
    try:
        self._rot_shared_view_center_px = QtCore.QPointF(float(p0[0]), float(p0[1]))
    except Exception:
        self._rot_shared_view_center_px = None

def _handle_mouse_press_moderngl_left_gizmo_rotate_ring_view_set_forward(self, *, g, V, M):
    # Compute camera forward direction in world (same logic you had before).
    forward_world = QtGui.QVector3D(0.0, 0.0, -1.0)
    try:
        VM = (np.asarray(V, dtype=np.float32) @ np.asarray(M, dtype=np.float32)).astype(np.float32)
        invVM = np.linalg.inv(VM)
        cam4 = invVM @ np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
        cw = float(cam4[3]) if abs(float(cam4[3])) > 1e-8 else 1.0
        cam = cam4[:3] / cw

        f = g - cam
        ln = float(np.linalg.norm(f))
        if ln > 1e-6:
            f = f / ln
            forward_world = QtGui.QVector3D(float(f[0]), float(f[1]), float(f[2]))
    except Exception:
        pass

    self._rot_shared_view_forward_world = forward_world

def _handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick(
    self,
    *,
    e,
    ctx,
    rot_shared,
    hit,
):
    p0 = ctx["p0"]
    if ctx["mode"] != "rotate" or rot_shared is None or p0 is None or hit is not None:
        return False
    try:
        return self._handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_try(
            e=e,
            ctx=ctx,
            rot_shared=rot_shared,
        )
    except Exception as ex:
        try:
            self._mgl_log("[ROT_SHARED_ARC_BEGIN_ERR] " + repr(ex))
        except Exception:
            print("[ROT_SHARED_ARC_BEGIN_ERR] " + repr(ex), flush=True)
    return False

def _handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_try(
    self,
    *,
    e,
    ctx,
    rot_shared,
):
    p0 = ctx["p0"]
    prepared = self._handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_prepare(
        owner=ctx["owner"],
        rot_shared=rot_shared,
        p0=p0,
        dpr=ctx["dpr"],
        px_dev=ctx["px_dev"],
        py_dev=ctx["py_dev"],
        V=ctx["V"],
        M=ctx["M"],
    )
    if prepared is None:
        return False

    self._handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_begin_state(
        p0=p0,
        px_dev=ctx["px_dev"],
        py_dev=ctx["py_dev"],
        **prepared,
    )
    e.accept()
    return True

def _handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_prepare(
    self,
    *,
    owner,
    rot_shared,
    p0,
    dpr,
    px_dev,
    py_dev,
    V,
    M,
):
    disc_r = float(rot_shared.xyz_ring_radius_px()) * float(dpr)
    dx0 = float(px_dev) - float(p0[0])
    dy0 = float(py_dev) - float(p0[1])
    if (dx0 * dx0 + dy0 * dy0) > (disc_r * disc_r):
        return None

    self._rot_shared_owner = owner
    self._begin_xform_history(owner)

    # Always sync q0 from the owner's CURRENT outliner rotation
    # (prevents first-drag snap after manual edits / zeroing)
    q0 = self._rot_shared_sync_q0_from_owner(owner)
    right_world, up_world, forward_world = self._handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_basis(
        V=V,
        M=M,
    )
    return {
        "disc_r": disc_r,
        "q0": q0,
        "right_world": right_world,
        "up_world": up_world,
        "forward_world": forward_world,
    }

def _handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_basis(self, *, V, M):
    # camera basis from inverse view*model (matches pick/unproject space)
    invVM = np.linalg.inv(
        (np.asarray(V, dtype=np.float32) @ np.asarray(M, dtype=np.float32)).astype(np.float32)
    )
    r = invVM[:3, 0]
    u = invVM[:3, 1]
    f = -invVM[:3, 2]  # camera looks down -Z

    right_world = QtGui.QVector3D(float(r[0]), float(r[1]), float(r[2]))
    up_world = QtGui.QVector3D(float(u[0]), float(u[1]), float(u[2]))
    forward_world = QtGui.QVector3D(float(f[0]), float(f[1]), float(f[2]))

    # normalize basis for stability
    try:
        if right_world.length() > 1e-6:
            right_world = right_world / right_world.length()
        if up_world.length() > 1e-6:
            up_world = up_world / up_world.length()
        if forward_world.length() > 1e-6:
            forward_world = forward_world / forward_world.length()
    except Exception:
        pass
    return right_world, up_world, forward_world

def _handle_mouse_press_moderngl_left_gizmo_rotate_arc_pick_begin_state(
    self,
    *,
    p0,
    px_dev,
    py_dev,
    disc_r,
    q0,
    right_world,
    up_world,
    forward_world,
):
    # stash arcball drag state
    self._rot_shared_arc_active = True
    self._rot_shared_arc_center_px = QtCore.QPointF(float(p0[0]), float(p0[1]))
    self._rot_shared_arc_radius_px = float(disc_r)
    self._rot_shared_arc_right_world = right_world
    self._rot_shared_arc_up_world = up_world
    self._rot_shared_arc_forward_world = forward_world
    self._rot_shared_arc_start_q = q0

    mouse_pf = QtCore.QPointF(float(px_dev), float(py_dev))
    start_vec = self._arcball_vec_world(
        mouse_px_dev=mouse_pf,
        center_px_dev=self._rot_shared_arc_center_px,
        radius_px=self._rot_shared_arc_radius_px,
        right_world=right_world,
        up_world=up_world,
        forward_world=forward_world,
    )
    self._rot_shared_arc_start_vec = start_vec

def _handle_mouse_press_moderngl_left_gizmo_axis_local(self, axis):
    if axis == "x":
        return np.array([1.0, 0.0, 0.0], dtype="f4")
    if axis == "y":
        return np.array([0.0, 1.0, 0.0], dtype="f4")
    if axis == "z":
        return np.array([0.0, 0.0, 1.0], dtype="f4")
    return None

def _handle_mouse_press_moderngl_left_gizmo_owner_rotation_matrix(self, *, owner):
    try:
        rot_deg, _is_splat = self._get_owner_rot_deg(owner)
        rx, ry, rz = float(rot_deg[0]), float(rot_deg[1]), float(rot_deg[2])
        cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
        cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
        cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))

        Rx = np.array(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, cx, sx, 0.0],
                [0.0, -sx, cx, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        )
        Ry = np.array(
            [
                [cy, 0.0, -sy, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [sy, 0.0, cy, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        )
        Rz = np.array(
            [
                [cz, sz, 0.0, 0.0],
                [-sz, cz, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        )
        return (Rz @ Ry @ Rx).astype(np.float32)
    except Exception:
        return np.eye(4, dtype=np.float32)

def _handle_mouse_press_moderngl_left_gizmo_scale_pick(
    self,
    *,
    e,
    ctx,
):
    if ctx["p0"] is not None and ctx["mode"] == "scale":
        try:
            use_local, R, pick_axis, dx0, dy0 = self._handle_mouse_press_moderngl_left_gizmo_scale_prepare(
                p0=ctx["p0"],
                mode=ctx["mode"],
                owner=ctx["owner"],
                g=ctx["g"],
                axis_len=ctx["axis_len"],
                dpr=ctx["dpr"],
                vw=ctx["vw"],
                vh=ctx["vh"],
                P=ctx["P"],
                V=ctx["V"],
                M=ctx["M"],
                renderer=ctx["renderer"],
                px_dev=ctx["px_dev"],
                py_dev=ctx["py_dev"],
            )
            if self._handle_mouse_press_moderngl_left_gizmo_scale_start(
                e=e,
                ctx=ctx,
                pick_axis=pick_axis,
                dx0=dx0,
                dy0=dy0,
                use_local=use_local,
                R=R,
            ):
                return True
        except Exception as ex:
            print("[GIZMO_SCALE_PICK] failed:", ex, flush=True)
    return False

def _handle_mouse_press_moderngl_left_gizmo_scale_prepare(
    self,
    *,
    p0,
    mode,
    owner,
    g,
    axis_len,
    dpr,
    vw,
    vh,
    P,
    V,
    M,
    renderer,
    px_dev,
    py_dev,
):
    use_local, R = self._handle_mouse_press_moderngl_left_gizmo_scale_basis(owner=owner)
    axis_proj = self._handle_mouse_press_moderngl_left_gizmo_scale_axis_proj(
        g=g,
        axis_len=axis_len,
        dpr=dpr,
        vw=vw,
        vh=vh,
        P=P,
        V=V,
        M=M,
        R=R,
        renderer=renderer,
    )
    pick_axis, dx0, dy0 = self._handle_mouse_press_moderngl_left_gizmo_scale_pick_axis(
        p0=p0,
        axis_proj=axis_proj,
        px_dev=px_dev,
        py_dev=py_dev,
    )
    return use_local, R, pick_axis, dx0, dy0

def _handle_mouse_press_moderngl_left_gizmo_scale_basis(self, *, owner):
    use_local = bool(getattr(self, "_xform_use_local", False))
    # Rotation matrix (scale gizmo follows object orientation).
    R = np.eye(4, dtype=np.float32)
    if use_local:
        R = self._handle_mouse_press_moderngl_left_gizmo_owner_rotation_matrix(owner=owner)
    return use_local, R

def _handle_mouse_press_moderngl_left_gizmo_scale_axis_proj(
    self,
    *,
    g,
    axis_len,
    dpr,
    vw,
    vh,
    P,
    V,
    M,
    R,
    renderer,
):
    T = self._handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_translate(g=g)
    s = self._handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_scale(
        dpr=dpr,
        P=P,
        V=V,
        M=M,
        T=T,
        R=R,
        renderer=renderer,
    )
    PVTRS = self._handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_pvtrs(
        P=P,
        V=V,
        M=M,
        T=T,
        R=R,
        s=s,
    )
    cube_axis_pos = self._handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_cube_axis_pos(axis_len=axis_len)
    return self._handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_map(
        cube_axis_pos=cube_axis_pos,
        PVTRS=PVTRS,
        vw=vw,
        vh=vh,
    )

def _handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_translate(self, *, g):
    T = np.eye(4, dtype=np.float32)
    T[0, 3] = float(g[0])
    T[1, 3] = float(g[1])
    T[2, 3] = float(g[2])
    return T

def _handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_scale(
    self,
    *,
    dpr,
    P,
    V,
    M,
    T,
    R,
    renderer,
):
    # Match rotate gizmo scaling so cubes stay screen-sized.
    return self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale(
        dpr=dpr,
        P=P,
        V=V,
        M=M,
        T=T,
        R=R,
        renderer=renderer,
    )

def _handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_pvtrs(self, *, P, V, M, T, R, s):
    S = np.eye(4, dtype=np.float32)
    S[0, 0] = s
    S[1, 1] = s
    S[2, 2] = s
    TRS = (T @ R @ S).astype(np.float32)
    return (P @ V @ M @ TRS).astype(np.float32)

def _handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_cube_axis_pos(self, *, axis_len):
    return axis_len - 0.18 + (0.12 * 0.5)

def _handle_mouse_press_moderngl_left_gizmo_scale_axis_proj_map(self, *, cube_axis_pos, PVTRS, vw, vh):
    return {
        "x": self._handle_mouse_press_moderngl_left_gizmo_translate_project_local(
            local_xyz=(cube_axis_pos, 0.0, 0.0),
            PVTRS=PVTRS,
            vw=vw,
            vh=vh,
        ),
        "y": self._handle_mouse_press_moderngl_left_gizmo_translate_project_local(
            local_xyz=(0.0, cube_axis_pos, 0.0),
            PVTRS=PVTRS,
            vw=vw,
            vh=vh,
        ),
        "z": self._handle_mouse_press_moderngl_left_gizmo_translate_project_local(
            local_xyz=(0.0, 0.0, cube_axis_pos),
            PVTRS=PVTRS,
            vw=vw,
            vh=vh,
        ),
    }

def _handle_mouse_press_moderngl_left_gizmo_scale_pick_axis(self, *, p0, axis_proj, px_dev, py_dev):
    # Avoid stealing orbit clicks far from the gizmo center.
    max_axis_len = 0.0
    for p1 in axis_proj.values():
        if p1 is None:
            continue
        dx1 = float(p1[0]) - float(p0[0])
        dy1 = float(p1[1]) - float(p0[1])
        dist = (dx1 * dx1 + dy1 * dy1) ** 0.5
        if dist > max_axis_len:
            max_axis_len = dist

    dx0 = float(px_dev) - float(p0[0])
    dy0 = float(py_dev) - float(p0[1])
    max_center = max(24.0, max_axis_len + 12.0)
    if (dx0 * dx0 + dy0 * dy0) > (max_center * max_center):
        axis_proj = {}

    pick_axis = None
    center_r = 16.0
    if (dx0 * dx0 + dy0 * dy0) <= (center_r * center_r):
        pick_axis = "u"
    else:
        best_axis = None
        best_d = 1e30
        for name, p1 in axis_proj.items():
            if p1 is None:
                continue
            dx1 = float(px_dev) - float(p1[0])
            dy1 = float(py_dev) - float(p1[1])
            d = (dx1 * dx1 + dy1 * dy1) ** 0.5
            if d < best_d:
                best_d = d
                best_axis = name
        if best_axis is not None and best_d <= 16.0:
            pick_axis = best_axis
    return pick_axis, dx0, dy0

def _handle_mouse_press_moderngl_left_gizmo_scale_start(
    self,
    *,
    e,
    ctx,
    pick_axis,
    dx0,
    dy0,
    use_local,
    R,
):
    if pick_axis is None:
        return False

    owner = self._handle_mouse_press_moderngl_left_gizmo_scale_start_begin(ctx=ctx, pick_axis=pick_axis)
    self._handle_mouse_press_moderngl_left_gizmo_scale_start_mode(
        pick_axis=pick_axis,
        p0=ctx["p0"],
        dx0=dx0,
        dy0=dy0,
        px_dev=ctx["px_dev"],
        use_local=use_local,
        R=R,
    )
    self._handle_mouse_press_moderngl_left_gizmo_scale_start_finalize(e=e, pick_axis=pick_axis, owner=owner)
    return True

def _handle_mouse_press_moderngl_left_gizmo_scale_start_begin(self, *, ctx, pick_axis):
    renderer = ctx["renderer"]
    owner = ctx["owner"]
    is_splat = self._handle_mouse_press_moderngl_left_gizmo_owner_is_splat(
        renderer=renderer,
        owner=owner,
    )
    start_scl = self._handle_mouse_press_moderngl_left_gizmo_scale_start_scl(
        renderer=renderer,
        owner=owner,
        is_splat=is_splat,
    )
    self._handle_mouse_press_moderngl_left_gizmo_scale_begin_drag(
        owner=owner,
        g=ctx["g"],
        pick_axis=pick_axis,
        is_splat=is_splat,
        start_scl=start_scl,
    )
    return owner

def _handle_mouse_press_moderngl_left_gizmo_scale_start_mode(
    self,
    *,
    pick_axis,
    p0,
    dx0,
    dy0,
    px_dev,
    use_local,
    R,
):
    if pick_axis == "u":
        self._handle_mouse_press_moderngl_left_gizmo_scale_start_uniform(
            p0=p0,
            dx0=dx0,
            dy0=dy0,
            px_dev=px_dev,
        )
        return
    self._handle_mouse_press_moderngl_left_gizmo_scale_start_axis(
        pick_axis=pick_axis,
        use_local=use_local,
        R=R,
    )

def _handle_mouse_press_moderngl_left_gizmo_scale_start_finalize(self, *, e, pick_axis, owner):
    # Important: prevent old click-pick/orbit press state from interfering.
    self._mgl_pick_press_pos = None
    print("[GIZMO_SCALE_PICK] axis=", pick_axis, "owner=", owner, flush=True)
    self.setCursor(QtCore.Qt.SizeAllCursor)
    e.accept()

def _handle_mouse_press_moderngl_left_gizmo_scale_start_scl(self, *, renderer, owner, is_splat):
    get_xf = (
        getattr(renderer, "_mgl_get_scene_splat_xform", None)
        if is_splat
        else getattr(renderer, "_mgl_get_scene_asset_xform", None)
    )
    xf = get_xf(owner) if callable(get_xf) else {}
    scl = tuple((xf or {}).get("scl", (1.0, 1.0, 1.0)))
    try:
        return (float(scl[0]), float(scl[1]), float(scl[2]))
    except Exception:
        return (1.0, 1.0, 1.0)

def _handle_mouse_press_moderngl_left_gizmo_scale_begin_drag(self, *, owner, g, pick_axis, is_splat, start_scl):
    self._xform_dragging = True
    self._begin_xform_history(owner)
    self._xform_drag_mode = "scale"
    self._xform_drag_axis = pick_axis
    self._xform_drag_owner = owner
    drag_kind = "splat" if is_splat else "mesh"
    try:
        renderer = getattr(self, "_mgl_renderer", None) or self
        is_pose_joint = getattr(renderer, "_mgl_retarget_owner_is_target_pose_joint", None)
        if callable(is_pose_joint) and bool(is_pose_joint(owner)):
            drag_kind = "retarget_target_joint"
    except Exception:
        pass
    self._xform_drag_kind = drag_kind
    self._xform_drag_start_pos = g.copy()
    self._xform_gizmo_pos_locked = True
    self._xform_drag_s0 = None
    self._xform_drag_start_scl = start_scl
    self._xform_scale_start_dist = None
    self._xform_scale_axis_world = None
    self._xform_scale_center_px = None
    self._xform_scale_start_px = None

def _handle_mouse_press_moderngl_left_gizmo_scale_start_uniform(self, *, p0, dx0, dy0, px_dev):
    self._xform_scale_center_px = QtCore.QPointF(float(p0[0]), float(p0[1]))
    self._xform_scale_start_dist = max(1e-6, (dx0 * dx0 + dy0 * dy0) ** 0.5)
    try:
        self._xform_scale_start_px = float(px_dev)
    except Exception:
        self._xform_scale_start_px = None

def _handle_mouse_press_moderngl_left_gizmo_scale_start_axis(self, *, pick_axis, use_local, R):
    axis_local = self._handle_mouse_press_moderngl_left_gizmo_axis_local(pick_axis)
    if axis_local is None:
        self._xform_scale_axis_world = None
        return
    axis_world = axis_local
    if use_local:
        try:
            axis_world = (R[:3, :3] @ axis_local).astype(np.float32)
        except Exception:
            axis_world = axis_local
    ln = float(np.linalg.norm(axis_world))
    if ln > 1e-8:
        axis_world = axis_world / ln
    self._xform_scale_axis_world = axis_world

def _handle_mouse_press_moderngl_left_gizmo_translate_pick(
    self,
    *,
    e,
    ctx,
    project,
    dist_pt_seg,
):
    p0, axis_dirs, axis_proj = self._handle_mouse_press_moderngl_left_gizmo_translate_prepare(
        ctx=ctx,
        project=project,
    )
    return self._handle_mouse_press_moderngl_left_gizmo_translate_pick_start(
        e=e,
        ctx=ctx,
        p0=p0,
        axis_dirs=axis_dirs,
        axis_proj=axis_proj,
        dist_pt_seg=dist_pt_seg,
    )

def _handle_mouse_press_moderngl_left_gizmo_translate_pick_start(
    self,
    *,
    e,
    ctx,
    p0,
    axis_dirs,
    axis_proj,
    dist_pt_seg,
):
    return bool(
        self._handle_mouse_press_moderngl_left_gizmo_translate_start(
            e=e,
            ctx=ctx,
            p0=p0,
            axis_dirs=axis_dirs,
            axis_proj=axis_proj,
            dist_pt_seg=dist_pt_seg,
        )
    )

def _handle_mouse_press_moderngl_left_gizmo_translate_prepare(
    self,
    *,
    ctx,
    project,
):
    p0 = ctx["p0"]
    axis_dirs = ctx["axes"]
    axis_proj = {}
    if p0 is not None and ctx["mode"] == "translate":
        axis_dirs, R = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_dirs(
            owner=ctx["owner"],
            axes=ctx["axes"],
        )
        axis_proj, max_axis_len = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj(
            p0=p0,
            g=ctx["g"],
            axis_dirs=axis_dirs,
            axis_len=ctx["axis_len"],
            dpr=ctx["dpr"],
            vw=ctx["vw"],
            vh=ctx["vh"],
            P=ctx["P"],
            V=ctx["V"],
            M=ctx["M"],
            R=R,
            renderer=ctx["renderer"],
            project=project,
        )
        if self._handle_mouse_press_moderngl_left_gizmo_translate_is_far_from_center(
            p0=p0,
            px_dev=ctx["px_dev"],
            py_dev=ctx["py_dev"],
            max_axis_len=max_axis_len,
        ):
            p0 = None
    return p0, axis_dirs, axis_proj

def _handle_mouse_press_moderngl_left_gizmo_translate_axis_dirs(self, *, owner, axes):
    axis_dirs = axes
    R = np.eye(4, dtype=np.float32)
    if bool(getattr(self, "_xform_use_local", False)):
        R = self._handle_mouse_press_moderngl_left_gizmo_owner_rotation_matrix(owner=owner)
        try:
            axis_dirs = {
                "x": (R[:3, :3] @ axes["x"]).astype("f4"),
                "y": (R[:3, :3] @ axes["y"]).astype("f4"),
                "z": (R[:3, :3] @ axes["z"]).astype("f4"),
            }
        except Exception:
            axis_dirs = axes
            R = np.eye(4, dtype=np.float32)
    return axis_dirs, R

def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj(
    self,
    *,
    p0,
    g,
    axis_dirs,
    axis_len,
    dpr,
    vw,
    vh,
    P,
    V,
    M,
    R,
    renderer,
    project,
):
    axis_proj, max_axis_len = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled(
        p0=p0,
        g=g,
        axis_len=axis_len,
        dpr=dpr,
        vw=vw,
        vh=vh,
        P=P,
        V=V,
        M=M,
        R=R,
        renderer=renderer,
    )
    if not axis_proj:
        axis_proj, max_axis_len = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_fallback(
            p0=p0,
            g=g,
            axis_dirs=axis_dirs,
            axis_len=axis_len,
            project=project,
        )
    return axis_proj, max_axis_len

def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled(
    self,
    *,
    p0,
    g,
    axis_len,
    dpr,
    vw,
    vh,
    P,
    V,
    M,
    R,
    renderer,
):
    # Project using the same scaled gizmo transform as the draw path.
    try:
        PVTRS = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled_pvtrs(
            g=g,
            dpr=dpr,
            P=P,
            V=V,
            M=M,
            R=R,
            renderer=renderer,
        )
        axis_proj = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled_map(
            axis_len=axis_len,
            PVTRS=PVTRS,
            vw=vw,
            vh=vh,
        )
        max_axis_len = self._handle_mouse_press_moderngl_left_gizmo_axis_proj_max_len(p0=p0, axis_proj=axis_proj)
        return axis_proj, max_axis_len
    except Exception:
        return {}, 0.0

def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled_pvtrs(
    self,
    *,
    g,
    dpr,
    P,
    V,
    M,
    R,
    renderer,
):
    T = np.eye(4, dtype=np.float32)
    T[0, 3] = float(g[0])
    T[1, 3] = float(g[1])
    T[2, 3] = float(g[2])

    s = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale(
        dpr=dpr,
        P=P,
        V=V,
        M=M,
        T=T,
        R=R,
        renderer=renderer,
    )

    S = np.eye(4, dtype=np.float32)
    S[0, 0] = s
    S[1, 1] = s
    S[2, 2] = s

    TRS = (T @ R @ S).astype(np.float32)
    return (P @ V @ M @ TRS).astype(np.float32)

def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scaled_map(self, *, axis_len, PVTRS, vw, vh):
    line_end = float(axis_len) - 0.18
    return {
        "x": self._handle_mouse_press_moderngl_left_gizmo_translate_project_local(
            local_xyz=(line_end, 0.0, 0.0),
            PVTRS=PVTRS,
            vw=vw,
            vh=vh,
        ),
        "y": self._handle_mouse_press_moderngl_left_gizmo_translate_project_local(
            local_xyz=(0.0, line_end, 0.0),
            PVTRS=PVTRS,
            vw=vw,
            vh=vh,
        ),
        "z": self._handle_mouse_press_moderngl_left_gizmo_translate_project_local(
            local_xyz=(0.0, 0.0, line_end),
            PVTRS=PVTRS,
            vw=vw,
            vh=vh,
        ),
    }

def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale(self, *, dpr, P, V, M, T, R, renderer):
    rect_fn = getattr(self, "_mgl_active_viewport_qrectf", None)
    rect = rect_fn() if callable(rect_fn) else None
    if isinstance(rect, QtCore.QRectF) and rect.height() > 1.0:
        vh_s = float(max(1.0, rect.height())) * float(dpr)
    else:
        vh_s = float(max(1, self.height())) * float(dpr)
    sm = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale_multiplier(renderer=renderer)
    target_ring_px, ring_r = self._handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale_target()
    return _gv_gizmo_screen_scale(
        P=P,
        V=V,
        M=M,
        T=T,
        R=R,
        viewport_height_px=vh_s,
        scale_multiplier=sm,
        target_ring_px=target_ring_px,
        ring_radius=ring_r,
    )

def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale_multiplier(self, *, renderer):
    try:
        return float(getattr(renderer, "_mgl_scale_multiplier", 1.0))
    except Exception:
        return 1.0

def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_scale_target(self):
    rot_shared = getattr(self, "_rot_shared", None)
    if rot_shared is not None:
        return float(rot_shared.xyz_ring_radius_px()), float(getattr(rot_shared, "gizmo_radius", 0.9))
    return 110.0 * 1.3, 0.9

def _handle_mouse_press_moderngl_left_gizmo_translate_project_local(self, *, local_xyz, PVTRS, vw, vh):
    return _gv_project_local(local_xyz=local_xyz, PVTRS=PVTRS, vw=vw, vh=vh)

def _handle_mouse_press_moderngl_left_gizmo_axis_proj_max_len(self, *, p0, axis_proj):
    return _gv_axis_proj_max_len(p0=p0, axis_proj=axis_proj)

def _handle_mouse_press_moderngl_left_gizmo_translate_axis_proj_fallback(
    self,
    *,
    p0,
    g,
    axis_dirs,
    axis_len,
    project,
):
    axis_proj = {}
    max_axis_len = 0.0
    for name, a in axis_dirs.items():
        p1 = project(g + a * axis_len)
        if p1 is None:
            continue
        axis_proj[name] = p1
    max_axis_len = self._handle_mouse_press_moderngl_left_gizmo_axis_proj_max_len(
        p0=p0,
        axis_proj=axis_proj,
    )
    return axis_proj, max_axis_len

def _handle_mouse_press_moderngl_left_gizmo_translate_is_far_from_center(self, *, p0, px_dev, py_dev, max_axis_len):
    # Avoid stealing orbit clicks far from the gizmo center
    dx0 = float(px_dev) - float(p0[0])
    dy0 = float(py_dev) - float(p0[1])
    max_center = max(24.0, max_axis_len + 12.0)
    return (dx0 * dx0 + dy0 * dy0) > (max_center * max_center)

def _handle_mouse_press_moderngl_left_gizmo_translate_start(
    self,
    *,
    e,
    ctx,
    p0,
    axis_dirs,
    axis_proj,
    dist_pt_seg,
):
    if p0 is None or ctx["mode"] != "translate":
        return False

    dx0, dy0 = self._handle_mouse_press_moderngl_left_gizmo_translate_start_offsets(
        p0=p0,
        px_dev=ctx["px_dev"],
        py_dev=ctx["py_dev"],
    )
    if self._handle_mouse_press_moderngl_left_gizmo_translate_start_try_view(
        e=e,
        ctx=ctx,
        dx0=dx0,
        dy0=dy0,
    ):
        return True
    return self._handle_mouse_press_moderngl_left_gizmo_translate_start_try_axis(
        e=e,
        ctx=ctx,
        p0=p0,
        axis_dirs=axis_dirs,
        axis_proj=axis_proj,
        dist_pt_seg=dist_pt_seg,
    )

def _handle_mouse_press_moderngl_left_gizmo_translate_start_offsets(self, *, p0, px_dev, py_dev):
    dx0 = float(px_dev) - float(p0[0])
    dy0 = float(py_dev) - float(p0[1])
    return dx0, dy0

def _handle_mouse_press_moderngl_left_gizmo_translate_start_try_view(
    self,
    *,
    e,
    ctx,
    dx0,
    dy0,
):
    return self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_drag(
        e=e,
        ctx=ctx,
        dx0=dx0,
        dy0=dy0,
    )

def _handle_mouse_press_moderngl_left_gizmo_translate_start_try_axis(
    self,
    *,
    e,
    ctx,
    p0,
    axis_dirs,
    axis_proj,
    dist_pt_seg,
):
    return self._handle_mouse_press_moderngl_left_gizmo_translate_start_axis_drag(
        e=e,
        owner=ctx["owner"],
        g=ctx["g"],
        p0=p0,
        axis_dirs=axis_dirs,
        axis_proj=axis_proj,
        renderer=ctx["renderer"],
        px_dev=ctx["px_dev"],
        py_dev=ctx["py_dev"],
        dist_pt_seg=dist_pt_seg,
    )

def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_drag(
    self,
    *,
    e,
    ctx,
    dx0,
    dy0,
):
    if not self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_is_center_hit(
        dx0=dx0,
        dy0=dy0,
        dpr=ctx["dpr"],
    ):
        return False

    self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_init_drag(
        owner=ctx["owner"],
        g=ctx["g"],
        renderer=ctx["renderer"],
    )
    self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_set_plane_hit(
        g=ctx["g"],
        ctx=ctx,
    )
    self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_finalize(e=e)
    return True

def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_init_drag(self, *, owner, g, renderer):
    # free-move on view plane (camera-facing)
    is_splat = self._handle_mouse_press_moderngl_left_gizmo_owner_is_splat(
        renderer=renderer,
        owner=owner,
    )
    self._handle_mouse_press_moderngl_left_gizmo_translate_begin_drag(
        owner=owner,
        g=g,
        is_splat=is_splat,
        axis="view",
    )
    self._xform_drag_axis_world = None
    self._xform_drag_plane_normal = None
    self._xform_drag_plane_start = None

def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_set_plane_hit(
    self,
    *,
    g,
    ctx,
):
    n = self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_plane_normal(
        V=ctx["V"],
        M=ctx["M"],
    )
    ray = self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_ray(
        px_dev=ctx["px_dev"],
        py_dev=ctx["py_dev"],
        vw=ctx["vw"],
        vh=ctx["vh"],
        P=ctx["P"],
        V=ctx["V"],
        M=ctx["M"],
    )
    if n is not None and ray is not None:
        ray_o, ray_d = ray
        hit = self._handle_mouse_press_moderngl_left_gizmo_translate_start_view_plane_hit(
            g=g,
            plane_normal=n,
            ray_o=ray_o,
            ray_d=ray_d,
        )
        if hit is not None:
            self._xform_drag_plane_normal = n
            self._xform_drag_plane_start = hit

def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_finalize(self, *, e):
    # Important: prevent old click-pick/orbit press state from interfering.
    self._mgl_pick_press_pos = None
    self.setCursor(QtCore.Qt.SizeAllCursor)
    e.accept()

def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_is_center_hit(self, *, dx0, dy0, dpr):
    try:
        center_r = 14.0 * float(dpr)
    except Exception:
        center_r = 14.0
    return (dx0 * dx0 + dy0 * dy0) <= (center_r * center_r)

def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_plane_normal(self, *, V, M):
    return _gv_plane_normal_from_vm(V=V, M=M)

def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_ray(self, *, px_dev, py_dev, vw, vh, P, V, M):
    return self._handle_mouse_move_moderngl_xform_ray(px=px_dev, py=py_dev, vw=vw, vh=vh, P=P, V=V, M=M)

def _handle_mouse_press_moderngl_left_gizmo_translate_start_view_plane_hit(self, *, g, plane_normal, ray_o, ray_d):
    return _gv_plane_hit(point_on_plane=g, plane_normal=plane_normal, ray_o=ray_o, ray_d=ray_d)

def _handle_mouse_press_moderngl_left_gizmo_translate_start_axis_drag(
    self,
    *,
    e,
    owner,
    g,
    p0,
    axis_dirs,
    axis_proj,
    renderer,
    px_dev,
    py_dev,
    dist_pt_seg,
):
    best_axis, best_d = self._handle_mouse_press_moderngl_left_gizmo_translate_start_axis_best(
        p0=p0,
        axis_proj=axis_proj,
        px_dev=px_dev,
        py_dev=py_dev,
        dist_pt_seg=dist_pt_seg,
    )
    if best_axis is None or best_d > 20.0:
        return False

    self._handle_mouse_press_moderngl_left_gizmo_translate_start_axis_begin(
        owner=owner,
        g=g,
        renderer=renderer,
        best_axis=best_axis,
        axis_dirs=axis_dirs,
    )
    self._handle_mouse_press_moderngl_left_gizmo_translate_start_axis_finalize(
        e=e,
        best_axis=best_axis,
        best_d=best_d,
        owner=owner,
    )
    return True

def _handle_mouse_press_moderngl_left_gizmo_translate_start_axis_best(
    self,
    *,
    p0,
    axis_proj,
    px_dev,
    py_dev,
    dist_pt_seg,
):
    best_axis = None
    best_d = 1e30
    for name, p1 in axis_proj.items():
        d = dist_pt_seg(px_dev, py_dev, p0[0], p0[1], p1[0], p1[1])
        if d < best_d:
            best_d = d
            best_axis = name
    return best_axis, best_d

def _handle_mouse_press_moderngl_left_gizmo_translate_start_axis_begin(
    self,
    *,
    owner,
    g,
    renderer,
    best_axis,
    axis_dirs,
):
    # Determine kind directly from renderer state to avoid stale selection state
    is_splat = self._handle_mouse_press_moderngl_left_gizmo_owner_is_splat(
        renderer=renderer,
        owner=owner,
    )
    self._handle_mouse_press_moderngl_left_gizmo_translate_begin_drag(
        owner=owner,
        g=g,
        is_splat=is_splat,
        axis=best_axis,
    )
    self._xform_drag_axis_world = axis_dirs.get(best_axis) if isinstance(axis_dirs, dict) else None

def _handle_mouse_press_moderngl_left_gizmo_translate_start_axis_finalize(self, *, e, best_axis, best_d, owner):
    # Important: prevent old click-pick/orbit press state from interfering
    self._mgl_pick_press_pos = None
    print("[GIZMO_PICK] axis=", best_axis, "d=", best_d, "owner=", owner, flush=True)
    self.setCursor(QtCore.Qt.SizeAllCursor)
    e.accept()

def _handle_mouse_press_moderngl_left_gizmo_translate_begin_drag(self, *, owner, g, is_splat, axis):
    self._xform_dragging = True
    self._begin_xform_history(owner)
    self._xform_drag_mode = "translate"
    self._xform_drag_axis = axis
    self._xform_drag_owner = owner
    drag_kind = "splat" if is_splat else "mesh"
    try:
        renderer = getattr(self, "_mgl_renderer", None) or self
        is_pose_joint = getattr(renderer, "_mgl_retarget_owner_is_target_pose_joint", None)
        if callable(is_pose_joint) and bool(is_pose_joint(owner)):
            drag_kind = "retarget_target_joint"
    except Exception:
        pass
    self._xform_drag_kind = drag_kind
    self._xform_drag_start_pos = g.copy()
    self._xform_gizmo_pos_locked = True
    self._xform_drag_s0 = None

def _handle_mouse_press_moderngl_left_gizmo_owner_is_splat(self, *, renderer, owner):
    is_splat = False
    try:
        splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
        if not isinstance(splat_map, dict) or not splat_map:
            splat_map = getattr(renderer, "_mgl_scene_splats", None)
        if isinstance(splat_map, dict):
            if owner in splat_map:
                is_splat = True
            else:
                owner_l = str(owner or "").strip().lower()
                for k in splat_map.keys():
                    try:
                        if str(k).strip().lower() == owner_l:
                            is_splat = True
                            break
                    except Exception:
                        continue
    except Exception:
        is_splat = False
    return is_splat

def _handle_mouse_press_moderngl_left_orbit_start(self, e, alt_pressed):
    if e.button() == QtCore.Qt.LeftButton and alt_pressed and self._mgl_arcball is not None:
        if bool(getattr(self, "_mgl_orbit_locked", True)):
            self._sync_locked_orbit_from_arcball()
            self._mgl_orbit_dragging = True
            self._mgl_orbit_last_pos = e.pos()
        else:
            self._mgl_arcball.onClickLeftDown(e.x(), e.y())
        self._mgl_pick_press_pos = e.pos()
        self.setCursor(QtCore.Qt.ClosedHandCursor)
        e.accept()
        return True
    return False

def _handle_mouse_press_moderngl_left_pick_start(self, e, alt_pressed):
    if e.button() == QtCore.Qt.LeftButton and not alt_pressed:
        # allow click-pick without orbiting
        self._mgl_pick_press_pos = e.pos()
        self.setCursor(QtCore.Qt.ArrowCursor)
        e.accept()
        return True
    return False

def _handle_mouse_press_moderngl_middle_start(self, e, alt_pressed):
    if e.button() == QtCore.Qt.MiddleButton:
        fly_mode = bool(getattr(self, "_fly_mode_enabled", False))
        if not alt_pressed and not fly_mode:
            e.ignore()
            return True
        self._mgl_prev_x = e.x()
        self._mgl_prev_y = e.y()
        self.setCursor(QtCore.Qt.OpenHandCursor)
        e.accept()
        return True
    return False

def _handle_mouse_press_moderngl_right_start(self, e, alt_pressed):
    if e.button() != QtCore.Qt.RightButton:
        return False
    if not alt_pressed:
        return self._handle_mouse_press_moderngl_right_start_fly(e)
    return self._handle_mouse_press_moderngl_right_start_zoom(e)

def _handle_mouse_press_moderngl_right_start_fly(self, e):
    if not bool(getattr(self, "_fly_mode_enabled", False)):
        e.ignore()
        return True
    self._handle_mouse_press_moderngl_right_start_fly_nav_state(e)
    self._handle_mouse_press_moderngl_right_start_fly_camera_state()
    self._handle_mouse_press_moderngl_right_start_fly_ui_state()
    e.accept()
    return True

def _handle_mouse_press_moderngl_right_start_fly_nav_state(self, e):
    try:
        self._fps_nav_active = True
        self._fps_nav_last_t = time.perf_counter()
        self._fps_nav_look_last_pos = e.pos()
        self._fps_nav_cursor_anchor = self._handle_mouse_press_moderngl_right_start_fly_anchor_from_event(e)
        self._fps_nav_warping = False
    except Exception:
        pass

def _handle_mouse_press_moderngl_right_start_fly_anchor_from_event(self, e):
    try:
        if hasattr(e, "globalPosition"):
            return e.globalPosition().toPoint()
        if hasattr(e, "globalPos"):
            return e.globalPos()
        return QtGui.QCursor.pos()
    except Exception:
        return QtGui.QCursor.pos()

def _handle_mouse_press_moderngl_right_start_fly_camera_state(self):
    try:
        self._fps_camera_active = True
        locked_synced = False
        try:
            sync_locked = getattr(self, "_camera_selector_sync_fps_from_locked_owner", None)
            if callable(sync_locked):
                locked_synced = bool(sync_locked())
        except Exception:
            locked_synced = False
        orbit_enabled = bool(getattr(self, "_orbit_cam_enabled", True))
        if (not bool(locked_synced)) and (orbit_enabled or getattr(self, "_fps_camera", None) is None):
            self._fps_cam_sync_from_orbit()
    except Exception:
        pass

def _handle_mouse_press_moderngl_right_start_fly_ui_state(self):
    try:
        self._mgl_zoom_press_pos = None
    except Exception:
        pass
    try:
        self.setFocus(QtCore.Qt.MouseFocusReason)
    except Exception:
        pass
    try:
        if bool(getattr(self, "_fly_mode_enabled", False)):
            self.setCursor(QtCore.Qt.BlankCursor)
        else:
            self.setCursor(QtCore.Qt.ArrowCursor)
    except Exception:
        pass

def _handle_mouse_press_moderngl_right_start_zoom(self, e):
    self._mgl_zoom_press_pos = e.pos()
    self._mgl_zoom_start = float(self._mgl_camera_zoom)
    self._handle_mouse_press_moderngl_right_start_zoom_center()
    self._handle_mouse_press_moderngl_right_start_zoom_ray(e)
    self._handle_mouse_press_moderngl_right_start_zoom_cam_start()
    self._handle_mouse_press_moderngl_right_start_zoom_cam_dir()
    self.setCursor(QtCore.Qt.SizeVerCursor)
    e.accept()
    return True

def _handle_mouse_press_moderngl_right_start_zoom_center(self):
    try:
        center = getattr(self, "_mgl_center", None)
        if center is None:
            center = (0.0, 0.0, 0.0)
        if np is not None:
            self._mgl_zoom_center_start = np.array(center, dtype=np.float32)
        else:
            self._mgl_zoom_center_start = (
                float(center[0]),
                float(center[1]),
                float(center[2]),
            )
    except Exception:
        self._mgl_zoom_center_start = None

def _handle_mouse_press_moderngl_right_start_zoom_ray(self, e):
    try:
        ray = self._ray_from_mouse(e.pos())
        if ray is not None:
            _, ray_d = ray
            if np is not None:
                self._mgl_zoom_ray_dir = np.array(ray_d, dtype=np.float32)
            else:
                self._mgl_zoom_ray_dir = (
                    float(ray_d[0]),
                    float(ray_d[1]),
                    float(ray_d[2]),
                )
        else:
            self._mgl_zoom_ray_dir = None
    except Exception:
        self._mgl_zoom_ray_dir = None

def _handle_mouse_press_moderngl_right_start_zoom_cam_start(self):
    try:
        cam_world = getattr(self, "_mgl_cam_world", None)
        if cam_world is None and np is not None:
            arc = getattr(self, "_mgl_arcball", None)
            zoom = float(getattr(self, "_mgl_camera_zoom", 0.0))
            if arc is not None and hasattr(arc, "Transform"):
                rot = np.array(arc.Transform[:3, :3], dtype=np.float32)
                scale = float(np.linalg.norm(rot, ord="fro") / math.sqrt(3.0))
                if scale > 1e-6:
                    rot = rot / scale
                cam_local = np.array([0.0, 0.0, zoom], dtype=np.float32)
                cam_rot = rot.T @ cam_local
                c0 = getattr(self, "_mgl_zoom_center_start", None)
                if c0 is not None:
                    cam_world = cam_rot + np.array(c0, dtype=np.float32)
                else:
                    cam_world = cam_rot
        if cam_world is not None:
            if np is not None:
                self._mgl_zoom_cam_start = np.array(cam_world, dtype=np.float32)
            else:
                self._mgl_zoom_cam_start = (
                    float(cam_world[0]),
                    float(cam_world[1]),
                    float(cam_world[2]),
                )
        else:
            self._mgl_zoom_cam_start = None
    except Exception:
        self._mgl_zoom_cam_start = None

def _handle_mouse_press_moderngl_right_start_zoom_cam_dir(self):
    try:
        cam_start = self._mgl_zoom_cam_start
        c0 = self._mgl_zoom_center_start
        if cam_start is not None and c0 is not None:
            if np is not None:
                cs = np.array(cam_start, dtype=np.float32)
                c = np.array(c0, dtype=np.float32)
                v = cs - c
                vn = float(np.linalg.norm(v))
                if vn > 1e-6:
                    self._mgl_zoom_cam_dir = (v / vn).astype("f4")
                else:
                    self._mgl_zoom_cam_dir = None
            else:
                dx = float(cam_start[0]) - float(c0[0])
                dy = float(cam_start[1]) - float(c0[1])
                dz = float(cam_start[2]) - float(c0[2])
                dn = math.sqrt(dx * dx + dy * dy + dz * dz)
                if dn > 1e-6:
                    self._mgl_zoom_cam_dir = (dx / dn, dy / dn, dz / dn)
                else:
                    self._mgl_zoom_cam_dir = None
        else:
            self._mgl_zoom_cam_dir = None
    except Exception:
        self._mgl_zoom_cam_dir = None

def _handle_mouse_press_example_pipeline(self, e):
    if self._use_example_pipeline:
        alt_pressed = False
        try:
            alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
        except Exception:
            alt_pressed = False

        if e.button() == QtCore.Qt.LeftButton:
            if not alt_pressed:
                e.ignore()
                return True
            self._orbit_dragging = True
            self._orbit_last_pos = e.pos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            e.accept()
            return True

        if e.button() == QtCore.Qt.MiddleButton:
            if not alt_pressed:
                e.ignore()
                return True
            self._pan_dragging = True
            self._pan_last_pos = e.pos()
            self.setCursor(QtCore.Qt.OpenHandCursor)
            e.accept()
            return True

        if e.button() == QtCore.Qt.RightButton:
            if not alt_pressed:
                e.ignore()
                return True
            self._dolly_dragging = True
            self._dolly_press_pos = e.pos()
            self.setCursor(QtCore.Qt.SizeVerCursor)
            e.accept()
            return True
    return False

def _handle_mouse_press_legacy_left(self, e):
    if e.button() == QtCore.Qt.LeftButton:
        alt_pressed = False
        try:
            alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
        except Exception:
            alt_pressed = False
        if not alt_pressed:
            e.ignore()
            return True
        self._orbit_dragging = True
        self._orbit_last_pos = e.pos()
        self.setCursor(QtCore.Qt.ClosedHandCursor)
        e.accept()
        return True
    return False

def _handle_mouse_press_legacy_middle(self, e):
    if e.button() == QtCore.Qt.MiddleButton:
        alt_pressed = False
        try:
            alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
        except Exception:
            alt_pressed = False
        if not alt_pressed:
            e.ignore()
            return True
        self._pan_dragging = True
        self._pan_last_pos = e.pos()
        self.setCursor(QtCore.Qt.OpenHandCursor)
        e.accept()
        return True
    return False

def _handle_mouse_press_legacy_right(self, e):
    if e.button() == QtCore.Qt.RightButton:
        alt_pressed = False
        try:
            alt_pressed = bool(e.modifiers() & QtCore.Qt.AltModifier)
        except Exception:
            alt_pressed = False
        if not alt_pressed:
            e.ignore()
            return True
        self._dolly_dragging = True
        self._dolly_press_pos = e.pos()
        self._dolly_start_dist = float(self._cam_dist)
        self.setCursor(QtCore.Qt.SizeVerCursor)
        e.accept()
        return True
    return False

def mouseMoveEvent(self, e):
    def _rot_dbg(msg: str) -> None:
        try:
            fn = getattr(self, "_mgl_log", None)
            if callable(fn):
                fn(msg)
            else:
                print(msg, flush=True)
        except Exception:
            try:
                print(msg, flush=True)
            except Exception:
                pass
    if self._handle_mouse_move_moderngl(e, _rot_dbg):
        return
    if self._handle_mouse_move_example_pipeline(e):
        return
    if self._handle_mouse_move_legacy_orbit(e):
        return
    if self._handle_mouse_move_legacy_pan(e):
        return
    if self._handle_mouse_move_legacy_dolly(e):
        return
    _graph_gl_view_super(self).mouseMoveEvent(e)

def _handle_mouse_move_moderngl(self, e, _rot_dbg):
    if self._use_moderngl:
        # cache mouse pos for ROT_SHARED hover (logical pixels, matches project_to_screen usage)
        try:
            mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
            self._rot_shared_mouse_px = QtCore.QPointF(mp)
            self._xform_mouse_px = QtCore.QPointF(mp)
        except Exception:
            mp = None

        if self._handle_mouse_move_moderngl_fps_nav(e):
            return True

        if self._handle_mouse_move_moderngl_retarget_drag(e):
            return True

        self._log_mouse_move_rot_shared_state(_rot_dbg)
        self._update_mouse_move_xform_hover()

        if self._handle_mouse_move_moderngl_rot_shared_view_drag(e, _rot_dbg):
            return True

        if self._handle_mouse_move_moderngl_rot_shared_arc_drag(e):
            return True

        if self._handle_mouse_move_moderngl_rot_shared_axis_drag(e, mp, _rot_dbg):
            return True


        if self._handle_mouse_move_moderngl_xform_drag(e):
            return True

        if self._handle_mouse_move_moderngl_arcball_drag(e):
            return True

        if self._handle_mouse_move_moderngl_pan_drag(e):
            return True

        if self._handle_mouse_move_moderngl_zoom_drag(e):
            return True
    return False

def _handle_mouse_move_moderngl_retarget_drag(self, e):
    drag = getattr(self, "_retarget_joint_drag", None)
    if not isinstance(drag, dict):
        return False
    renderer = getattr(self, "_mgl_renderer", None) or self
    pick = getattr(renderer, "pick_retarget_joint_at", None)
    if callable(pick):
        try:
            px, py, vw, vh = self._handle_mouse_retarget_viewport(e)
            drag["target_hover"] = pick(px, py, vw, vh, role="target")
        except Exception:
            drag["target_hover"] = None
    try:
        self.setCursor(QtCore.Qt.CrossCursor)
    except Exception:
        pass
    try:
        self.update()
    except Exception:
        pass
    e.accept()
    return True

def _handle_mouse_move_moderngl_fps_nav(self, e):
    if not self._handle_mouse_move_moderngl_fps_nav_active(e):
        return False
    try:
        if bool(getattr(self, "_fly_mode_enabled", False)):
            self._handle_mouse_move_moderngl_fps_nav_fly_look(e)
        else:
            self._handle_mouse_move_moderngl_fps_nav_drag_look(e)
    except Exception:
        pass
    self._handle_mouse_move_moderngl_fps_nav_finish(e)
    return True

def _handle_mouse_move_moderngl_fps_nav_active(self, e):
    return bool(getattr(self, "_fps_nav_active", False)) and bool(e.buttons() & QtCore.Qt.RightButton)

def _handle_mouse_move_moderngl_fps_nav_fly_look(self, e):
    if bool(getattr(self, "_fps_nav_warping", False)):
        self._fps_nav_warping = False

    anchor = self._handle_mouse_move_moderngl_fps_nav_anchor()
    gpos = self._handle_mouse_move_moderngl_fps_nav_global_pos(e)
    if anchor is None or gpos is None:
        return

    dx = float(gpos.x() - anchor.x())
    dy = float(gpos.y() - anchor.y())
    if abs(dx) <= 0.0 and abs(dy) <= 0.0:
        return

    self._fps_apply_look(dx, dy)
    try:
        self._fps_nav_warping = True
        QtGui.QCursor.setPos(anchor)
    except Exception:
        pass

def _handle_mouse_move_moderngl_fps_nav_anchor(self):
    anchor = getattr(self, "_fps_nav_cursor_anchor", None)
    if anchor is not None:
        return anchor
    try:
        anchor = QtGui.QCursor.pos()
    except Exception:
        anchor = None
    self._fps_nav_cursor_anchor = anchor
    return anchor

def _handle_mouse_move_moderngl_fps_nav_global_pos(self, e):
    try:
        if hasattr(e, "globalPosition"):
            return e.globalPosition().toPoint()
        if hasattr(e, "globalPos"):
            return e.globalPos()
        return QtGui.QCursor.pos()
    except Exception:
        return QtGui.QCursor.pos()

def _handle_mouse_move_moderngl_fps_nav_drag_look(self, e):
    last = getattr(self, "_fps_nav_look_last_pos", None)
    if last is None:
        last = e.pos()
    dx = float(e.pos().x() - last.x())
    dy = float(e.pos().y() - last.y())
    self._fps_nav_look_last_pos = e.pos()
    self._fps_apply_look(dx, dy)

def _handle_mouse_move_moderngl_fps_nav_finish(self, e):
    try:
        self.update()
    except Exception:
        pass
    e.accept()

def _log_mouse_move_rot_shared_state(self, _rot_dbg):
    # light state dump, throttled
    try:
        if bool(getattr(self, "_mgl_splat_log", False)):
            now = time.perf_counter()
            last = float(getattr(self, "_rot_shared_dbg_t", 0.0))
            if (now - last) > 0.20:
                self._rot_shared_dbg_t = now
                rot_shared = getattr(self, "_rot_shared", None)
                drag_axis = getattr(rot_shared, "drag_axis", None) if rot_shared is not None else None
                _rot_dbg(
                    "[ROT_DBG]"
                    f" mode={getattr(self,'_xform_gizmo_mode',None)}"
                    f" _rot_shared_dragging={bool(getattr(self,'_rot_shared_dragging',False))}"
                    f" _rot_shared_axis={getattr(self,'_rot_shared_axis',None)}"
                    f" drag_axis.active={bool(getattr(drag_axis,'active',False))}"
                    f" drag_axis.axis={getattr(drag_axis,'axis',None)}"
                )
    except Exception:
        pass

def _update_mouse_move_xform_hover(self):
    # Hover highlight needs repaints even when not dragging
    if (
        getattr(self, "_xform_gizmo_mode", "") == "rotate"
        and getattr(self, "_rot_shared", None) is not None
        and not getattr(self, "_rot_shared_dragging", False)
    ):
        self.update()
    if (
        getattr(self, "_xform_gizmo_mode", "") in ("translate", "scale")
        and not getattr(self, "_xform_dragging", False)
    ):
        self.update()

def _handle_mouse_move_moderngl_rot_shared_view_drag(self, e, _rot_dbg):
    # --- ROT_SHARED view-ring drag update (smoketest style) ---
    rot_shared = getattr(self, "_rot_shared", None)
    if (
        rot_shared is not None
        and bool(getattr(rot_shared, "drag_view", False))
        and (e.buttons() & QtCore.Qt.LeftButton)
    ):
        try:
            prepared = self._handle_mouse_move_moderngl_rot_shared_view_drag_prepare(
                e=e,
                rot_shared=rot_shared,
            )
            if prepared is None:
                return True
            owner, qnew = prepared
            self._handle_mouse_move_moderngl_rot_shared_view_drag_apply(
                e=e,
                owner=owner,
                qnew=qnew,
            )
            return True

        except Exception as ex:
            _rot_dbg("[ROT_SHARED_VIEW_MOVE_ERR] " + repr(ex))
    return False

def _handle_mouse_move_moderngl_rot_shared_view_drag_prepare(self, *, e, rot_shared):
    owner = getattr(self, "_rot_shared_owner", None)
    if owner is None:
        return None

    center_pf = getattr(self, "_rot_shared_view_center_px", None)
    if not isinstance(center_pf, QtCore.QPointF):
        return None

    forward_world = getattr(self, "_rot_shared_view_forward_world", None)
    if not isinstance(forward_world, QtGui.QVector3D):
        return None

    dpr = float(self.devicePixelRatioF()) if hasattr(self, "devicePixelRatioF") else 1.0
    mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
    mouse_pf = QtCore.QPointF(float(mp.x()) * dpr, float(mp.y()) * dpr)

    qcur = self._handle_mouse_move_moderngl_rot_shared_view_drag_qcur(owner=owner)
    qnew = rot_shared.update_view_ring_drag(mouse_pf, center_pf, forward_world, qcur)
    if qnew is None:
        return None

    try:
        if hasattr(qnew, "normalized"):
            qnew = qnew.normalized()
    except Exception:
        pass
    return owner, qnew

def _handle_mouse_move_moderngl_rot_shared_view_drag_qcur(self, *, owner):
    qcur = None
    try:
        qcur = self._rot_owner_quat.get(owner)
    except Exception:
        qcur = None

    if qcur is None:
        rot_deg, is_splat = self._get_owner_rot_deg(owner)
        self._rot_shared_is_splat = bool(is_splat)
        qcur = self._rot_shared_q_from_euler_deg(
            (float(rot_deg[0]), float(rot_deg[1]), float(rot_deg[2]))
        )
        try:
            self._rot_owner_quat[owner] = qcur
        except Exception:
            pass
    return qcur

def _handle_mouse_move_moderngl_rot_shared_view_drag_apply(self, *, e, owner, qnew):
    try:
        self._rot_owner_quat[owner] = qnew
    except Exception:
        pass

    rx, ry, rz = self._rot_shared_euler_deg_from_q(qnew)

    cur_rot_deg, _is_splat = self._get_owner_rot_deg(owner)
    rx, ry, rz = self._rot_shared_euler_deg_from_q_continuous(qnew, cur_rot_deg)

    self._set_owner_rot_deg(
        owner,
        (rx, ry, rz),
        bool(getattr(self, "_rot_shared_is_splat", False)),
    )

    self.update()
    e.accept()

def _handle_mouse_move_moderngl_rot_shared_arc_drag(self, e):
    if bool(getattr(self, "_rot_shared_arc_active", False)) and (e.buttons() & QtCore.Qt.LeftButton):
        try:
            prepared = self._handle_mouse_move_moderngl_rot_shared_arc_drag_prepare(e=e)
            if prepared is None:
                return True
            owner, qnew = prepared
            self._handle_mouse_move_moderngl_rot_shared_arc_drag_apply(
                e=e,
                owner=owner,
                qnew=qnew,
            )
            return True

        except Exception as ex:
            try:
                self._mgl_log("[ROT_SHARED_ARC_MOVE_ERR] " + repr(ex))
            except Exception:
                pass
    return False

def _handle_mouse_move_moderngl_rot_shared_arc_drag_prepare(self, *, e):
    owner = self._handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_owner()
    if owner is None:
        return None

    mouse_ctx = self._handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_mouse(e=e)
    if mouse_ctx is None:
        return None
    center_pf, mouse_pf = mouse_ctx

    basis = self._handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_basis()
    if basis is None:
        return None
    start_vec, right_world, up_world, forward_world = basis

    qnew = self._handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_qnew(
        start_vec=start_vec,
        center_pf=center_pf,
        mouse_pf=mouse_pf,
        right_world=right_world,
        up_world=up_world,
        forward_world=forward_world,
    )
    if qnew is None:
        return None
    return owner, qnew

def _handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_owner(self):
    return getattr(self, "_rot_shared_owner", None)

def _handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_mouse(self, *, e):
    center_pf = getattr(self, "_rot_shared_arc_center_px", None)
    if not isinstance(center_pf, QtCore.QPointF):
        return None
    dpr = float(self.devicePixelRatioF()) if hasattr(self, "devicePixelRatioF") else 1.0
    mp = e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
    mouse_pf = QtCore.QPointF(float(mp.x()) * dpr, float(mp.y()) * dpr)
    return center_pf, mouse_pf

def _handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_basis(self):
    start_vec = getattr(self, "_rot_shared_arc_start_vec", None)
    right_world = getattr(self, "_rot_shared_arc_right_world", None)
    up_world = getattr(self, "_rot_shared_arc_up_world", None)
    forward_world = getattr(self, "_rot_shared_arc_forward_world", None)
    if not isinstance(start_vec, QtGui.QVector3D):
        return None
    if not (
        isinstance(right_world, QtGui.QVector3D)
        and isinstance(up_world, QtGui.QVector3D)
        and isinstance(forward_world, QtGui.QVector3D)
    ):
        return None
    return start_vec, right_world, up_world, forward_world

def _handle_mouse_move_moderngl_rot_shared_arc_drag_prepare_qnew(
    self,
    *,
    start_vec,
    center_pf,
    mouse_pf,
    right_world,
    up_world,
    forward_world,
):
    radius_px = float(getattr(self, "_rot_shared_arc_radius_px", 1.0))
    cur_vec = self._arcball_vec_world(
        mouse_px_dev=mouse_pf,
        center_px_dev=center_pf,
        radius_px=radius_px,
        right_world=right_world,
        up_world=up_world,
        forward_world=forward_world,
    )

    q_delta = quat_from_two_vectors(start_vec, cur_vec)
    q0 = getattr(self, "_rot_shared_arc_start_q", None)
    if q0 is None:
        return None
    qnew = q_delta * q0
    try:
        if hasattr(qnew, "normalized"):
            qnew = qnew.normalized()
    except Exception:
        pass
    return qnew

def _handle_mouse_move_moderngl_rot_shared_arc_drag_apply(self, *, e, owner, qnew):
    # persist quaternion cache (arcball uses qnew directly)
    try:
        self._rot_owner_quat[owner] = qnew
    except Exception:
        pass

    # APPLY to owner so the object visibly rotates during arcball drag
    # unwrap vs current outliner values so angles keep accumulating past 180
    try:
        rx, ry, rz = self._rot_shared_euler_deg_from_q(qnew)

        cur_rot_deg, _is_splat = self._get_owner_rot_deg(owner)
        rx = self._unwrap_deg(float(cur_rot_deg[0]), float(rx))
        ry = self._unwrap_deg(float(cur_rot_deg[1]), float(ry))
        rz = self._unwrap_deg(float(cur_rot_deg[2]), float(rz))

        self._set_owner_rot_deg(
            owner,
            (rx, ry, rz),
            bool(getattr(self, "_rot_shared_is_splat", False)),
        )
    except Exception:
        pass

    self.update()
    e.accept()

def _handle_mouse_move_moderngl_rot_shared_axis_drag(self, e, mp, _rot_dbg):
    rot_shared = getattr(self, "_rot_shared", None)
    if not (
        rot_shared is not None
        and getattr(rot_shared, "drag_axis", None) is not None
        and bool(getattr(rot_shared.drag_axis, "active", False))
        and (e.buttons() & QtCore.Qt.LeftButton)
    ):
        return False
    try:
        prepared = self._handle_mouse_move_moderngl_rot_shared_axis_prepare(
            e=e,
            mp=mp,
            rot_shared=rot_shared,
            _rot_dbg=_rot_dbg,
        )
        if prepared is None:
            try:
                e.accept()
            except Exception:
                pass
            return True
        owner, qnew = prepared
        self._handle_mouse_move_moderngl_rot_shared_axis_apply(
            owner=owner,
            rot_shared=rot_shared,
            qnew=qnew,
            _rot_dbg=_rot_dbg,
        )
        try:
            e.accept()
        except Exception:
            pass
        return True

    except Exception as ex:
        _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] " + repr(ex))
        return False

def _handle_mouse_move_moderngl_rot_shared_axis_prepare(self, e, mp, rot_shared, _rot_dbg):
    if np is None:
        _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] np is None")
        return None

    owner = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_owner(_rot_dbg=_rot_dbg)
    if not owner:
        return None

    mp = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_mouse(e=e, mp=mp)
    ray = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_ray(mp=mp, _rot_dbg=_rot_dbg)
    return self._handle_mouse_move_moderngl_rot_shared_axis_prepare_from_ray(
        owner=owner,
        rot_shared=rot_shared,
        ray=ray,
        _rot_dbg=_rot_dbg,
    )

def _handle_mouse_move_moderngl_rot_shared_axis_prepare_from_ray(self, *, owner, rot_shared, ray, _rot_dbg):
    if ray is None:
        return None
    cam, ray_d = ray

    ring_dir = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_ring_dir(
        rot_shared=rot_shared,
        cam=cam,
        ray_d=ray_d,
        _rot_dbg=_rot_dbg,
    )
    if ring_dir is None:
        return None
    cur_dir, axis_world = ring_dir

    self._handle_mouse_move_moderngl_rot_shared_axis_prepare_log(
        rot_shared=rot_shared,
        owner=owner,
        axis_world=axis_world,
        ray_d=ray_d,
        cur_dir=cur_dir,
        _rot_dbg=_rot_dbg,
    )
    qnew = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_qnew(
        rot_shared=rot_shared,
        cur_dir=cur_dir,
        _rot_dbg=_rot_dbg,
    )
    if qnew is None:
        return None
    return owner, qnew

def _handle_mouse_move_moderngl_rot_shared_axis_prepare_owner(self, *, _rot_dbg):
    owner = getattr(self, "_rot_shared_owner", None)
    if not owner:
        _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] missing _rot_shared_owner")
        return None
    return owner

def _handle_mouse_move_moderngl_rot_shared_axis_prepare_mouse(self, *, e, mp):
    if mp is None:
        return e.position() if hasattr(e, "position") else QtCore.QPointF(e.x(), e.y())
    return mp

def _handle_mouse_move_moderngl_rot_shared_axis_prepare_log(
    self,
    *,
    rot_shared,
    owner,
    axis_world,
    ray_d,
    cur_dir,
    _rot_dbg,
):
    den_dbg = float(QtGui.QVector3D.dotProduct(axis_world, ray_d))
    _rot_dbg(
        "[ROT_SHARED_AXIS_MOVE]"
        f" axis={getattr(rot_shared.drag_axis,'axis',None)}"
        f" owner={owner}"
        f" den={den_dbg:.6f}"
        f" cur_dir=({cur_dir.x():.3f},{cur_dir.y():.3f},{cur_dir.z():.3f})"
    )

def _handle_mouse_move_moderngl_rot_shared_axis_prepare_qnew(self, *, rot_shared, cur_dir, _rot_dbg):
    qnew = rot_shared.update_axis_drag(cur_dir)
    if qnew is None:
        _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] qnew None")
        return None
    try:
        if hasattr(qnew, "normalized"):
            qnew = qnew.normalized()
    except Exception:
        pass
    return qnew

def _handle_mouse_move_moderngl_rot_shared_axis_prepare_ray(self, mp, _rot_dbg):
    dpr, vw, vh, px, py = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_viewport(mp=mp)
    invPV = self._handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_invpv(
        dpr=dpr,
        vw=vw,
        vh=vh,
        _rot_dbg=_rot_dbg,
    )
    if invPV is None:
        return None
    return self._handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_unproject(
        invPV=invPV,
        px=px,
        py=py,
        vw=vw,
        vh=vh,
        _rot_dbg=_rot_dbg,
    )

def _handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_viewport(self, *, mp):
    dpr = float(getattr(self, "_rot_shared_dpr", 0.0) or 0.0)
    if dpr <= 0.0:
        dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())

    vw = float(getattr(self, "_rot_shared_vw", 0.0) or 0.0)
    vh = float(getattr(self, "_rot_shared_vh", 0.0) or 0.0)
    rect_fn = getattr(self, "_mgl_active_viewport_qrectf", None)
    rect = rect_fn() if callable(rect_fn) else None
    if vw <= 1.0:
        if isinstance(rect, QtCore.QRectF) and rect.width() > 1.0:
            vw = float(rect.width()) * dpr
        else:
            vw = float(self.width()) * dpr
    if vh <= 1.0:
        if isinstance(rect, QtCore.QRectF) and rect.height() > 1.0:
            vh = float(rect.height()) * dpr
        else:
            vh = float(self.height()) * dpr

    # IMPORTANT: compute mouse in the same active-gate pixel space as vw/vh.
    off_x = float(rect.left()) if isinstance(rect, QtCore.QRectF) else 0.0
    off_y = float(rect.top()) if isinstance(rect, QtCore.QRectF) else 0.0
    px = (float(mp.x()) - off_x) * dpr
    py = (float(mp.y()) - off_y) * dpr
    return dpr, vw, vh, px, py

def _handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_invpv(self, *, dpr, vw, vh, _rot_dbg):
    # Prefer cached viewport + dpr from mousePressEvent so unproject stays stable during drag.
    invPV = getattr(self, "_rot_shared_invPV", None)
    if invPV is not None:
        return invPV

    renderer = getattr(self, "_mgl_renderer", None)
    Pn = getattr(renderer, "_mgl_pick_proj", None) if renderer is not None else None
    Vn = getattr(renderer, "_mgl_pick_view", None) if renderer is not None else None
    Mn = getattr(renderer, "_mgl_pick_model", None) if renderer is not None else None

    if np is None or Pn is None or Vn is None or Mn is None:
        _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] missing invPV and missing P/V/M")
        return None

    PV = (
        np.asarray(Pn, dtype=np.float32)
        @ np.asarray(Vn, dtype=np.float32)
        @ np.asarray(Mn, dtype=np.float32)
    ).astype(np.float32)

    try:
        invPV = np.linalg.inv(PV)
    except Exception as ex:
        _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] invPV " + repr(ex))
        return None

    # cache so subsequent move events do not need P/V/M
    self._rot_shared_invPV = invPV
    self._rot_shared_vw = vw
    self._rot_shared_vh = vh
    self._rot_shared_dpr = dpr
    return invPV

def _handle_mouse_move_moderngl_rot_shared_axis_prepare_ray_unproject(self, *, invPV, px, py, vw, vh, _rot_dbg):
    ray = _gv_ray_from_screen(invPV=invPV, px=px, py=py, vw=vw, vh=vh)
    if ray is None:
        _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] ray len too small")
        return None
    ray_o, ray_d_np = ray

    # Ray origin for the unprojected ray (near point is on the ray).
    cam = QtGui.QVector3D(float(ray_o[0]), float(ray_o[1]), float(ray_o[2]))
    ray_d = QtGui.QVector3D(float(ray_d_np[0]), float(ray_d_np[1]), float(ray_d_np[2]))
    return cam, ray_d

def _handle_mouse_move_moderngl_rot_shared_axis_prepare_ring_dir(self, rot_shared, cam, ray_d, _rot_dbg):
    center_w = getattr(self, "_rot_shared_axis_center_world", None)
    axis_world = getattr(rot_shared.drag_axis, "axis_world", None)

    if not isinstance(center_w, QtGui.QVector3D) or not isinstance(axis_world, QtGui.QVector3D):
        _rot_dbg("[ROT_SHARED_AXIS_MOVE_ERR] missing center_w/axis_world")
        return None

    if axis_world.length() > 1e-6:
        axis_world = axis_world / axis_world.length()

    # Smoketest-style: closest point on ray to gizmo center, then project onto ring plane
    cur_dir = self._axis_ring_dir_world(
        cam=cam,
        ray_d=ray_d,
        center_w=center_w,
        axis_world=axis_world,
    )

    # continuity: keep cur_dir on the same hemisphere as last_dir (prevents 180 flips / wobble)
    last_dir = getattr(rot_shared.drag_axis, "last_dir", None)
    if isinstance(last_dir, QtGui.QVector3D):
        if float(QtGui.QVector3D.dotProduct(last_dir, cur_dir)) < 0.0:
            cur_dir = QtGui.QVector3D(-cur_dir.x(), -cur_dir.y(), -cur_dir.z())

    # store for next move
    try:
        rot_shared.drag_axis.last_dir = QtGui.QVector3D(cur_dir)
    except Exception:
        pass
    return cur_dir, axis_world

def _handle_mouse_move_moderngl_rot_shared_axis_apply(self, owner, rot_shared, qnew, _rot_dbg):
    # APPLY: use quaternion result so rings stay constrained (no wobble)
    try:
        qapply = self._handle_mouse_move_moderngl_rot_shared_axis_apply_qapply(rot_shared=rot_shared, qnew=qnew)
        self._handle_mouse_move_moderngl_rot_shared_axis_apply_cache_quat(owner=owner, qapply=qapply)
        rx0, ry0, rz0, candidates = self._handle_mouse_move_moderngl_rot_shared_axis_apply_candidates(qapply=qapply)
        best = self._handle_mouse_move_moderngl_rot_shared_axis_apply_best(
            owner=owner,
            rx0=rx0,
            ry0=ry0,
            rz0=rz0,
            candidates=candidates,
        )
        self._handle_mouse_move_moderngl_rot_shared_axis_apply_set_owner(owner=owner, best=best)
    except Exception as ex:
        _rot_dbg("[ROT_SHARED_AXIS_APPLY_ERR] " + repr(ex))

def _handle_mouse_move_moderngl_rot_shared_axis_apply_qapply(self, *, rot_shared, qnew):
    qapply = qnew
    start_rot = getattr(rot_shared.drag_axis, "start_rot", None)
    if start_rot is not None:
        try:
            qdelta = (qnew * start_rot.conjugated()).normalized()
            qapply = (qdelta.conjugated() * start_rot).normalized()
        except Exception:
            qapply = qnew
    return qapply

def _handle_mouse_move_moderngl_rot_shared_axis_apply_cache_quat(self, *, owner, qapply):
    try:
        self._rot_owner_quat[owner] = qapply
    except Exception:
        pass

def _handle_mouse_move_moderngl_rot_shared_axis_apply_candidates(self, *, qapply):
    rx0, ry0, rz0 = self._rot_shared_euler_deg_from_q(qapply)
    candidates = [
        (float(rx0), float(ry0), float(rz0)),
        (float(rx0) + 180.0, 180.0 - float(ry0), float(rz0) + 180.0),
        (float(rx0) - 180.0, 180.0 - float(ry0), float(rz0) - 180.0),
    ]
    return rx0, ry0, rz0, candidates

def _handle_mouse_move_moderngl_rot_shared_axis_apply_best(self, *, owner, rx0, ry0, rz0, candidates):
    cur_rot_deg, _ = self._get_owner_rot_deg(owner)
    current_xyz = (
        float(cur_rot_deg[0]),
        float(cur_rot_deg[1]),
        float(cur_rot_deg[2]),
    )
    best = _gv_closest_unwrapped_euler(candidates=candidates, current_xyz=current_xyz)
    if best is None:
        cx, cy, cz = current_xyz
        return (self._unwrap_deg(cx, rx0), self._unwrap_deg(cy, ry0), self._unwrap_deg(cz, rz0))
    return best

def _handle_mouse_move_moderngl_rot_shared_axis_apply_set_owner(self, *, owner, best):
    self._set_owner_rot_deg(
        owner,
        best,
        bool(getattr(self, "_rot_shared_is_splat", False)),
    )

def _handle_mouse_move_moderngl_xform_drag(self, e):
    if getattr(self, "_xform_dragging", False) and (e.buttons() & QtCore.Qt.LeftButton):
        try:
            ctx = self._handle_mouse_move_moderngl_xform_drag_context(e=e)
            if ctx is None:
                return True

            if ctx["drag_mode"] == "scale":
                return self._handle_mouse_move_moderngl_xform_scale_drag(e=e, ctx=ctx)

            return self._handle_mouse_move_moderngl_xform_translate_drag(e=e, ctx=ctx)

        except Exception as exc:
            print("[GIZMO_DRAG] failed:", exc, flush=True)

    return False

def _handle_mouse_move_moderngl_xform_drag_context(self, *, e):
    if np is None:
        return None

    axis = getattr(self, "_xform_drag_axis", None)
    owner = getattr(self, "_xform_drag_owner", None)
    g0 = getattr(self, "_xform_drag_start_pos", None)
    if axis is None or owner is None or g0 is None:
        return None

    dpr = float(getattr(self, "devicePixelRatioF", lambda: 1.0)())
    rect_fn = getattr(self, "_mgl_active_viewport_qrectf", None)
    rect = rect_fn() if callable(rect_fn) else None
    if isinstance(rect, QtCore.QRectF) and rect.width() > 1.0 and rect.height() > 1.0:
        px = (float(e.x()) - float(rect.left())) * dpr
        py = (float(e.y()) - float(rect.top())) * dpr
        vw = float(rect.width()) * dpr
        vh = float(rect.height()) * dpr
    else:
        px = float(e.x()) * dpr
        py = float(e.y()) * dpr
        vw = float(self.width()) * dpr
        vh = float(self.height()) * dpr

    renderer = getattr(self, "_mgl_renderer", None) or self
    return {
        "axis": axis,
        "owner": owner,
        "g0": g0,
        "px": px,
        "py": py,
        "vw": vw,
        "vh": vh,
        "renderer": renderer,
        "P": getattr(renderer, "_mgl_pick_proj", None),
        "V": getattr(renderer, "_mgl_pick_view", None),
        "M": getattr(renderer, "_mgl_pick_model", None),
        "drag_mode": getattr(self, "_xform_drag_mode", "translate") or "translate",
    }

def _handle_mouse_move_moderngl_xform_ray(self, *, px, py, vw, vh, P, V, M):
    if P is None or V is None or M is None:
        return None
    try:
        PV = (P @ V @ M).astype("f4")
        invPV = np.linalg.inv(PV)
        return _gv_ray_from_screen(invPV=invPV, px=px, py=py, vw=vw, vh=vh)
    except Exception:
        return None

def _handle_mouse_move_moderngl_xform_translate_drag(
    self,
    e,
    ctx,
):
    new_pos = self._handle_mouse_move_moderngl_xform_translate_drag_new_pos(ctx=ctx)
    if new_pos is None:
        return True

    self._handle_mouse_move_moderngl_xform_translate_apply_owner(
        new_pos=new_pos,
        owner=ctx["owner"],
        renderer=ctx["renderer"],
    )
    self._handle_mouse_move_moderngl_xform_translate_drag_finish(e=e)
    return True

def _handle_mouse_move_moderngl_xform_translate_drag_new_pos(self, *, ctx):
    ray = self._handle_mouse_move_moderngl_xform_ray(
        px=ctx["px"],
        py=ctx["py"],
        vw=ctx["vw"],
        vh=ctx["vh"],
        P=ctx["P"],
        V=ctx["V"],
        M=ctx["M"],
    )
    if ray is None:
        return None
    ray_o, ray_d = ray
    return self._handle_mouse_move_moderngl_xform_translate_compute_pos(
        axis=ctx["axis"],
        owner=ctx["owner"],
        g0=ctx["g0"],
        ray_o=ray_o,
        ray_d=ray_d,
        V=ctx["V"],
        M=ctx["M"],
    )

def _handle_mouse_move_moderngl_xform_translate_drag_finish(self, *, e):
    # redraw
    self.update()
    e.accept()

def _handle_mouse_move_moderngl_xform_translate_compute_pos(
    self,
    axis,
    owner,
    g0,
    ray_o,
    ray_d,
    V,
    M,
):
    if axis == "view":
        return self._handle_mouse_move_moderngl_xform_translate_compute_pos_view(
            g0=g0,
            ray_o=ray_o,
            ray_d=ray_d,
            V=V,
            M=M,
        )

    return self._handle_mouse_move_moderngl_xform_translate_compute_pos_axis(
        axis=axis,
        owner=owner,
        g0=g0,
        ray_o=ray_o,
        ray_d=ray_d,
    )

def _handle_mouse_move_moderngl_xform_translate_compute_pos_view(self, *, g0, ray_o, ray_d, V, M):
    nrm = getattr(self, "_xform_drag_plane_normal", None)
    if nrm is None:
        nrm = _gv_plane_normal_from_vm(V=V, M=M)
    start_hit = getattr(self, "_xform_drag_plane_start", None)
    if nrm is not None:
        hit = _gv_plane_hit(point_on_plane=g0, plane_normal=nrm, ray_o=ray_o, ray_d=ray_d)
        if hit is not None:
            if start_hit is None:
                self._xform_drag_plane_start = hit
                start_hit = hit
            delta = hit - start_hit
            return g0 + delta
    return None

def _handle_mouse_move_moderngl_xform_translate_compute_pos_axis(self, *, axis, owner, g0, ray_o, ray_d):
    a = self._handle_mouse_move_moderngl_xform_translate_axis_world(axis=axis, owner=owner)
    if a is None:
        return None

    s = self._handle_mouse_move_moderngl_xform_translate_axis_param(
        axis_world=a,
        g0=g0,
        ray_o=ray_o,
        ray_d=ray_d,
    )

    # On first move after pick, capture s0.
    s0 = getattr(self, "_xform_drag_s0", None)
    if s0 is None:
        self._xform_drag_s0 = s
        s0 = s

    delta = (float(s0) - s) * a
    return g0 + delta

def _handle_mouse_move_moderngl_xform_translate_axis_world(self, *, axis, owner):
    a = getattr(self, "_xform_drag_axis_world", None)
    if a is None:
        a = self._handle_mouse_press_moderngl_left_gizmo_axis_local(axis)
        if a is None:
            return None
        if bool(getattr(self, "_xform_use_local", False)):
            R = self._handle_mouse_press_moderngl_left_gizmo_owner_rotation_matrix(owner=owner)
            a = (R[:3, :3] @ a).astype(np.float32)

    if isinstance(a, QtGui.QVector3D):
        a = np.array([a.x(), a.y(), a.z()], dtype="f4")

    a = np.asarray(a, dtype="f4")
    al = float(np.linalg.norm(a))
    if al > 1e-8:
        a = a / al
    return a

def _handle_mouse_move_moderngl_xform_translate_axis_param(self, *, axis_world, g0, ray_o, ray_d):
    # Compute parameter "s" along axis line closest to the mouse ray.
    return _gv_axis_line_ray_param(axis_world=axis_world, g0=g0, ray_o=ray_o, ray_d=ray_d)

def _handle_mouse_move_moderngl_xform_translate_apply_owner(self, new_pos, owner, renderer):
    self._xform_gizmo_pos = (float(new_pos[0]), float(new_pos[1]), float(new_pos[2]))

    try:
        is_pose_joint = getattr(renderer, "_mgl_retarget_owner_is_target_pose_joint", None)
        apply_pose_pos = getattr(renderer, "_mgl_retarget_apply_target_pose_joint_position", None)
        if callable(is_pose_joint) and callable(apply_pose_pos) and bool(is_pose_joint(owner)):
            apply_pose_pos(owner, self._xform_gizmo_pos, notify_scene=False)
            return
    except Exception:
        return

    is_splat = self._handle_mouse_press_moderngl_left_gizmo_owner_is_splat(
        renderer=renderer,
        owner=owner,
    )
    set_pos = self._handle_mouse_move_moderngl_xform_translate_apply_owner_pos(
        new_pos=new_pos,
        owner=owner,
        renderer=renderer,
        is_splat=is_splat,
    )

    self._mgl_set_scene_asset_xform(
        owner,
        pos=set_pos,
        apply_to_scene_models=not is_splat,
        use_splat_xform=bool(is_splat),
    )
    self._handle_mouse_move_moderngl_xform_translate_apply_owner_sync(owner=owner)

def _handle_mouse_move_moderngl_xform_translate_apply_owner_pos(self, *, new_pos, owner, renderer, is_splat):
    # For splats, xform.pos is a translation offset from the original pivot.
    set_pos = self._xform_gizmo_pos
    if not is_splat:
        return set_pos
    try:
        pivot = self._handle_mouse_move_moderngl_xform_translate_apply_owner_pivot(
            owner=owner,
            renderer=renderer,
        )
        if pivot is not None:
            set_pos = (
                float(new_pos[0] - pivot[0]),
                float(new_pos[1] - pivot[1]),
                float(new_pos[2] - pivot[2]),
            )
    except Exception:
        set_pos = self._xform_gizmo_pos
    return set_pos

def _handle_mouse_move_moderngl_xform_translate_apply_owner_pivot(self, *, owner, renderer):
    bounds_map = getattr(renderer, "_mgl_scene_splats_bounds_local", None) or getattr(
        renderer, "_mgl_scene_splat_bounds_by_owner", None
    )
    bmin = bmax = None
    try:
        if isinstance(bounds_map, dict):
            if owner in bounds_map:
                bmin, bmax = bounds_map.get(owner) or (None, None)
            else:
                owner_l = str(owner or "").strip().lower()
                for k, v in bounds_map.items():
                    try:
                        if str(k).strip().lower() == owner_l:
                            bmin, bmax = v or (None, None)
                            break
                    except Exception:
                        continue
    except Exception:
        bmin = bmax = None
    try:
        pivot_fn = getattr(renderer, "_mgl_owner_pivot_local", None)
        if callable(pivot_fn):
            raw = pivot_fn(owner, bmin, bmax) if (bmin is not None and bmax is not None) else pivot_fn(owner)
            return (float(raw[0]), float(raw[1]), float(raw[2]))
    except Exception:
        pass
    if bmin is not None and bmax is not None:
        try:
            return (
                (float(bmin[0]) + float(bmax[0])) * 0.5,
                (float(bmin[1]) + float(bmax[1])) * 0.5,
                (float(bmin[2]) + float(bmax[2])) * 0.5,
            )
        except Exception:
            return None
    return None

def _handle_mouse_move_moderngl_xform_translate_apply_owner_sync(self, *, owner):
    # Sync transform panel in the outliner (if visible)
    try:
        w = self.window()
        if hasattr(w, "update_scene_asset_xform"):
            w.update_scene_asset_xform(owner)
    except Exception:
        pass

def _handle_mouse_move_moderngl_xform_scale_drag(
    self,
    e,
    ctx,
):
    try:
        start_scl = self._handle_mouse_move_moderngl_xform_scale_get_start_scl(owner=ctx["owner"])
        is_splat = bool(getattr(self, "_xform_drag_kind", None) == "splat")
        new_scl = self._handle_mouse_move_moderngl_xform_scale_drag_new_scale(
            ctx=ctx,
            start_scl=start_scl,
        )
        if new_scl is None:
            return True

        self._handle_mouse_move_moderngl_xform_scale_apply_owner(
            e=e,
            owner=ctx["owner"],
            new_scl=new_scl,
            is_splat=is_splat,
        )
        return True
    except Exception as exc:
        print("[GIZMO_SCALE] failed:", exc, flush=True)
        return True

def _handle_mouse_move_moderngl_xform_scale_drag_new_scale(self, *, ctx, start_scl):
    if ctx["axis"] == "u":
        return self._handle_mouse_move_moderngl_xform_scale_uniform_drag(
            start_scl=start_scl,
            px=ctx["px"],
        )
    return self._handle_mouse_move_moderngl_xform_scale_axis_drag(
        ctx=ctx,
        start_scl=start_scl,
    )

def _handle_mouse_move_moderngl_xform_scale_get_start_scl(self, owner):
    start_scl = getattr(self, "_xform_drag_start_scl", None)
    if start_scl is None:
        try:
            start_scl, _ = self._get_owner_scl(owner)
        except Exception:
            start_scl = (1.0, 1.0, 1.0)
        self._xform_drag_start_scl = start_scl
    return start_scl

def _handle_mouse_move_moderngl_xform_scale_uniform_drag(self, start_scl, px):
    start_px = getattr(self, "_xform_scale_start_px", None)
    if start_px is None:
        try:
            start_px = float(px)
            self._xform_scale_start_px = start_px
        except Exception:
            start_px = float(px)

    dx = float(px) - float(start_px)
    # Horizontal-only uniform scale: right = bigger, left = smaller.
    sensitivity = 0.0020
    factor = 1.0 + (dx * sensitivity)
    factor = max(0.01, float(factor))
    return (
        max(0.01, float(start_scl[0]) * factor),
        max(0.01, float(start_scl[1]) * factor),
        max(0.01, float(start_scl[2]) * factor),
    )

def _handle_mouse_move_moderngl_xform_scale_axis_drag(self, *, ctx, start_scl):
    if ctx["P"] is None or ctx["V"] is None or ctx["M"] is None:
        return None

    axis_world = self._handle_mouse_move_moderngl_xform_scale_axis_world(
        axis=ctx["axis"],
        owner=ctx["owner"],
    )
    if axis_world is None:
        return None

    factor = self._handle_mouse_move_moderngl_xform_scale_axis_factor(
        axis_world=axis_world,
        g0=ctx["g0"],
        px=ctx["px"],
        py=ctx["py"],
        vw=ctx["vw"],
        vh=ctx["vh"],
        P=ctx["P"],
        V=ctx["V"],
        M=ctx["M"],
    )
    if factor is None:
        return None

    return self._handle_mouse_move_moderngl_xform_scale_axis_drag_apply_factor(
        start_scl=start_scl,
        axis=ctx["axis"],
        factor=factor,
    )

def _handle_mouse_move_moderngl_xform_scale_axis_drag_apply_factor(self, *, start_scl, axis, factor):
    new_scl = [float(start_scl[0]), float(start_scl[1]), float(start_scl[2])]
    idx = 0 if axis == "x" else (1 if axis == "y" else 2)
    new_scl[idx] = max(0.01, new_scl[idx] * factor)
    return tuple(new_scl)

def _handle_mouse_move_moderngl_xform_scale_axis_world(self, axis, owner):
    axis_world = getattr(self, "_xform_scale_axis_world", None)
    if axis_world is None:
        axis_local = self._handle_mouse_press_moderngl_left_gizmo_axis_local(axis)
        if axis_local is None:
            return None
        axis_world = axis_local

        if bool(getattr(self, "_xform_use_local", False)):
            R = self._handle_mouse_press_moderngl_left_gizmo_owner_rotation_matrix(owner=owner)
            axis_world = (R[:3, :3] @ axis_local).astype(np.float32)

    if isinstance(axis_world, QtGui.QVector3D):
        axis_world = np.array([axis_world.x(), axis_world.y(), axis_world.z()], dtype="f4")

    axis_world = np.asarray(axis_world, dtype=np.float32)
    ln = float(np.linalg.norm(axis_world))
    if ln <= 1e-8:
        return None

    axis_world = axis_world / ln
    self._xform_scale_axis_world = axis_world
    return axis_world

def _handle_mouse_move_moderngl_xform_scale_axis_factor(self, axis_world, g0, px, py, vw, vh, P, V, M):
    ray = self._handle_mouse_move_moderngl_xform_ray(
        px=px,
        py=py,
        vw=vw,
        vh=vh,
        P=P,
        V=V,
        M=M,
    )
    if ray is None:
        return None
    ray_o, ray_d = ray

    w0 = ray_o - g0
    ad = float(np.dot(axis_world, ray_d))
    denom = 1.0 - ad * ad
    if abs(denom) < 1e-6:
        s = float(np.dot(axis_world, w0))
    else:
        s = float((ad * float(np.dot(ray_d, w0)) - float(np.dot(axis_world, w0))) / denom)

    s0 = getattr(self, "_xform_drag_s0", None)
    if s0 is None:
        self._xform_drag_s0 = s
        s0 = s

    if abs(float(s0)) > 1e-6:
        factor = float(s) / float(s0)
    else:
        factor = 1.0
    return max(0.01, float(factor))

def _handle_mouse_move_moderngl_xform_scale_apply_owner(self, e, owner, new_scl, is_splat):
    try:
        renderer = getattr(self, "_mgl_renderer", None) or self
        is_pose_joint = getattr(renderer, "_mgl_retarget_owner_is_target_pose_joint", None)
        if callable(is_pose_joint) and bool(is_pose_joint(owner)):
            self.update()
            e.accept()
            return
    except Exception:
        pass
    self._mgl_set_scene_asset_xform(
        owner,
        scl=new_scl,
        apply_to_scene_models=not is_splat,
        use_splat_xform=bool(is_splat),
    )
    try:
        w = self.window()
        if hasattr(w, "update_scene_asset_xform"):
            w.update_scene_asset_xform(owner)
    except Exception:
        pass

    self.update()
    e.accept()

def _handle_mouse_move_moderngl_arcball_drag(self, e):
    if self._mgl_arcball is not None and (e.buttons() & QtCore.Qt.LeftButton):
        try:
            if not (e.modifiers() & QtCore.Qt.AltModifier):
                return True
        except Exception:
            pass
        if bool(getattr(self, "_mgl_orbit_locked", True)):
            if not self._mgl_orbit_dragging:
                self._sync_locked_orbit_from_arcball()
                self._mgl_orbit_dragging = True
                self._mgl_orbit_last_pos = e.pos()
            delta = e.pos() - self._mgl_orbit_last_pos
            self._mgl_orbit_last_pos = e.pos()
            self._mgl_orbit_yaw -= float(delta.x()) * self._orbit_sensitivity
            self._mgl_orbit_pitch += float(delta.y()) * self._orbit_sensitivity
            if self._mgl_orbit_pitch > math.pi:
                self._mgl_orbit_pitch -= (2.0 * math.pi)
            elif self._mgl_orbit_pitch < -math.pi:
                self._mgl_orbit_pitch += (2.0 * math.pi)
            self._apply_locked_orbit()
        else:
            self._mgl_arcball.onDrag(e.x(), e.y())
        self.update()  # ensure pick matrices stay fresh
        e.accept()
        return True
    return False

def _handle_mouse_move_moderngl_pan_drag(self, e):
    if e.buttons() & QtCore.Qt.MiddleButton:
        fly_mode = bool(getattr(self, "_fly_mode_enabled", False))
        if (not fly_mode) and (self._mgl_center is None):
            return False
        try:
            if (not fly_mode) and not (e.modifiers() & QtCore.Qt.AltModifier):
                return True
        except Exception:
            pass
        dx = e.x() - self._mgl_prev_x
        dy = e.y() - self._mgl_prev_y
        pan_scale = self._handle_mouse_move_moderngl_pan_drag_scale()
        moved = False
        if fly_mode:
            cam = getattr(self, "_fps_camera", None)
            if cam is not None and bool(getattr(self, "_fps_camera_active", False)):
                try:
                    cam.move(
                        0.0,
                        (-dx * pan_scale),
                        (dy * pan_scale),
                        roll_locked=False,
                    )
                    moved = True
                    try:
                        self._camera_selector_apply_fps_to_locked_owner(sync_ui=True)
                    except Exception:
                        pass
                except Exception:
                    moved = False
        if not moved and self._mgl_center is not None:
            right, up = self._handle_mouse_move_moderngl_pan_drag_basis()
            delta = (-dx * pan_scale) * right + (dy * pan_scale) * up
            self._mgl_center += delta
            moved = True
        self._mgl_prev_x = e.x()
        self._mgl_prev_y = e.y()
        if moved:
            self.update()
        e.accept()
        return True
    return False

def _handle_mouse_move_moderngl_pan_drag_scale(self):
    pan_scale = float(getattr(self, "_mgl_pan_base", 0.01))
    zoom, ref_zoom = self._handle_mouse_move_moderngl_pan_drag_scale_zoom_ref()
    if ref_zoom > 0.0:
        ratio = zoom / ref_zoom if zoom > 0.0 else 0.0
        if ratio > 1.0:
            pan_scale *= self._handle_mouse_move_moderngl_pan_drag_scale_boost(ratio)
    return pan_scale

def _handle_mouse_move_moderngl_pan_drag_scale_zoom_ref(self):
    try:
        zoom = float(getattr(self, "_mgl_camera_zoom", 0.0))
    except Exception:
        zoom = 0.0
    ref_zoom = self._handle_mouse_move_moderngl_pan_drag_scale_ref_zoom(zoom=zoom)
    return zoom, ref_zoom

def _handle_mouse_move_moderngl_pan_drag_scale_ref_zoom(self, *, zoom):
    ref_zoom = getattr(self, "_mgl_pan_ref_zoom", None)
    try:
        ref_zoom = float(ref_zoom) if ref_zoom is not None else 0.0
    except Exception:
        ref_zoom = 0.0
    if not math.isfinite(ref_zoom) or ref_zoom <= 0.0:
        try:
            ref_zoom = float(self._mgl_camera_distance(self._mgl_fov))
        except Exception:
            ref_zoom = max(1e-6, zoom)
        try:
            self._mgl_pan_ref_zoom = ref_zoom
        except Exception:
            pass
    return ref_zoom

def _handle_mouse_move_moderngl_pan_drag_scale_boost(self, ratio):
    try:
        exp_out = float(getattr(self, "_mgl_pan_zoom_exp_out", 1.2))
    except Exception:
        exp_out = 1.2
    if exp_out <= 0.0:
        exp_out = 1.0
    try:
        boost = float(getattr(self, "_mgl_pan_zoom_boost", 10.0))
    except Exception:
        boost = 10.0
    if boost < 1.0:
        boost = 1.0
    try:
        threshold = float(getattr(self, "_mgl_pan_zoom_threshold", 2.0))
    except Exception:
        threshold = 2.0
    if threshold <= 1.0:
        threshold = 1.0
    ramp = (ratio - 1.0) / (threshold - 1.0) if threshold > 1.0 else 1.0
    ramp = max(0.0, min(1.0, ramp))
    return (ratio ** exp_out) * (1.0 + (boost - 1.0) * ramp)

def _handle_mouse_move_moderngl_pan_drag_basis(self):
    right = np.array([1.0, 0.0, 0.0], dtype="f4")
    up = np.array([0.0, 1.0, 0.0], dtype="f4")
    if self._mgl_arcball is not None:
        rot = np.array(self._mgl_arcball.Transform[:3, :3], dtype="f4")
        scale = np.linalg.norm(rot, axis=0)
        denom = float(scale.mean()) if scale.size else 1.0
        if denom > 1e-6:
            rot = rot / denom
        right = rot @ right
        up = rot @ up
    return right, up

def _handle_mouse_move_moderngl_zoom_drag(self, e):
    if e.buttons() & QtCore.Qt.RightButton and self._mgl_zoom_press_pos is not None:
        try:
            if not (e.modifiers() & QtCore.Qt.AltModifier):
                return True
        except Exception:
            pass
        dx = e.pos().x() - self._mgl_zoom_press_pos.x()
        dy = e.pos().y() - self._mgl_zoom_press_pos.y()
        try:
            infinite = bool(getattr(self, "_mgl_zoom_infinite", True))
        except Exception:
            infinite = True
        if infinite:
            self._handle_mouse_move_moderngl_zoom_drag_infinite(dx=dx, dy=dy)
        else:
            self._handle_mouse_move_moderngl_zoom_drag_finite(dx=dx, dy=dy, e=e)
        self.update()
        e.accept()
        return True
    return False

def _handle_mouse_move_moderngl_zoom_drag_infinite(self, *, dx, dy):
    cam_start = getattr(self, "_mgl_zoom_cam_start", None)
    cam_dir = getattr(self, "_mgl_zoom_cam_dir", None)
    ray_dir = getattr(self, "_mgl_zoom_ray_dir", None)
    center_start = getattr(self, "_mgl_zoom_center_start", None)
    scale = float(getattr(self, "_mgl_zoom_pan_scale", 0.02))
    delta = (dx - dy) * scale

    if cam_start is not None and cam_dir is not None and ray_dir is not None:
        if np is not None:
            cs = np.array(cam_start, dtype=np.float32)
            rd = np.array(ray_dir, dtype=np.float32)
            cd = np.array(cam_dir, dtype=np.float32)
            cam_pos = cs + (rd * float(delta))
            self._mgl_center = (cam_pos - cd * float(self._mgl_camera_zoom)).astype("f4")
        else:
            csx, csy, csz = float(cam_start[0]), float(cam_start[1]), float(cam_start[2])
            rdx, rdy, rdz = float(ray_dir[0]), float(ray_dir[1]), float(ray_dir[2])
            cdx, cdy, cdz = float(cam_dir[0]), float(cam_dir[1]), float(cam_dir[2])
            cam_x = csx + rdx * float(delta)
            cam_y = csy + rdy * float(delta)
            cam_z = csz + rdz * float(delta)
            z = float(self._mgl_camera_zoom)
            self._mgl_center = (cam_x - cdx * z, cam_y - cdy * z, cam_z - cdz * z)
        return

    if ray_dir is not None and center_start is not None:
        if np is not None:
            c0 = np.array(center_start, dtype=np.float32)
            rd = np.array(ray_dir, dtype=np.float32)
            self._mgl_center = (c0 + rd * float(delta)).astype("f4")
        else:
            cx, cy, cz = float(center_start[0]), float(center_start[1]), float(center_start[2])
            rdx, rdy, rdz = float(ray_dir[0]), float(ray_dir[1]), float(ray_dir[2])
            self._mgl_center = (
                cx + rdx * float(delta),
                cy + rdy * float(delta),
                cz + rdz * float(delta),
            )

def _handle_mouse_move_moderngl_zoom_drag_finite(self, *, dx, dy, e):
    distance = dx - dy
    exponent = abs(distance) / self._drag_divisor
    base = self._zoom_multiplier
    factor = base ** exponent
    start = self._mgl_zoom_start if self._mgl_zoom_start is not None else self._mgl_camera_zoom
    if distance > 0:
        target = start / factor
    else:
        target = start * factor
    min_zoom = float(getattr(self, "_mgl_min_zoom", 0.001))
    self._mgl_camera_zoom = max(min_zoom, min(10000.0, target))
    self._mgl_zoom_start = self._mgl_camera_zoom
    self._mgl_zoom_press_pos = e.pos()

def _handle_mouse_move_example_pipeline(self, e):
    if self._use_example_pipeline:
        if self._handle_mouse_move_example_pipeline_orbit(e):
            return True
        if self._handle_mouse_move_example_pipeline_pan(e):
            return True
        if self._handle_mouse_move_example_pipeline_dolly(e):
            return True
    return False

def _handle_mouse_move_example_pipeline_orbit(self, e):
    if self._orbit_dragging and self._orbit_last_pos is not None:
        try:
            if not (e.modifiers() & QtCore.Qt.AltModifier):
                self._orbit_dragging = False
                self._orbit_last_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return True
        except Exception:
            pass
        if not (e.buttons() & QtCore.Qt.LeftButton):
            self._orbit_dragging = False
            self._orbit_last_pos = None
            self.setCursor(QtCore.Qt.ArrowCursor)
            e.accept()
            return True
        delta = e.pos() - self._orbit_last_pos
        self._orbit_last_pos = e.pos()
        self._example_orbit(QtCore.QPointF(delta))
        self.update()
        e.accept()
        return True
    return False

def _handle_mouse_move_example_pipeline_pan(self, e):
    if self._pan_dragging and self._pan_last_pos is not None:
        try:
            if not (e.modifiers() & QtCore.Qt.AltModifier):
                self._pan_dragging = False
                self._pan_last_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return True
        except Exception:
            pass
        if not (e.buttons() & QtCore.Qt.MiddleButton):
            self._pan_dragging = False
            self._pan_last_pos = None
            self.setCursor(QtCore.Qt.ArrowCursor)
            e.accept()
            return True
        delta = e.pos() - self._pan_last_pos
        self._pan_last_pos = e.pos()
        self._example_pan(QtCore.QPointF(delta))
        self.update()
        e.accept()
        return True
    return False

def _handle_mouse_move_example_pipeline_dolly(self, e):
    if self._dolly_dragging and self._dolly_press_pos is not None:
        try:
            if not (e.modifiers() & QtCore.Qt.AltModifier):
                self._dolly_dragging = False
                self._dolly_press_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return True
        except Exception:
            pass
        if not (e.buttons() & QtCore.Qt.RightButton):
            self._dolly_dragging = False
            self._dolly_press_pos = None
            self.setCursor(QtCore.Qt.ArrowCursor)
            e.accept()
            return True
        delta = e.pos() - self._dolly_press_pos
        self._dolly_press_pos = e.pos()
        self._example_zoom(-float(delta.y()) / 60.0)
        self.update()
        e.accept()
        return True
    return False

def _handle_mouse_move_legacy_orbit(self, e):
    if self._orbit_dragging and self._orbit_last_pos is not None:
        try:
            if not (e.modifiers() & QtCore.Qt.AltModifier):
                self._orbit_dragging = False
                self._orbit_last_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return True
        except Exception:
            pass
        if not (e.buttons() & QtCore.Qt.LeftButton):
            self._orbit_dragging = False
            self._orbit_last_pos = None
            self.setCursor(QtCore.Qt.ArrowCursor)
            e.accept()
            return True
        delta = e.pos() - self._orbit_last_pos
        self._orbit_last_pos = e.pos()
        self._cam_yaw -= float(delta.x()) * self._orbit_sensitivity
        self._cam_pitch -= float(delta.y()) * self._orbit_sensitivity
        self._cam_pitch = max(-1.45, min(1.45, self._cam_pitch))
        self.update()
        e.accept()
        return True
    return False

def _handle_mouse_move_legacy_pan(self, e):
    if self._pan_dragging and self._pan_last_pos is not None:
        try:
            if not (e.modifiers() & QtCore.Qt.AltModifier):
                self._pan_dragging = False
                self._pan_last_pos = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return True
        except Exception:
            pass
        if not (e.buttons() & QtCore.Qt.MiddleButton):
            self._pan_dragging = False
            self._pan_last_pos = None
            self.setCursor(QtCore.Qt.ArrowCursor)
            e.accept()
            return True
        delta = e.pos() - self._pan_last_pos
        self._pan_last_pos = e.pos()
        fov_rad = math.radians(self._fov_deg)
        scale = (2.0 * self._cam_dist * math.tan(fov_rad * 0.5)) / max(1.0, self.height())
        self._cam_target = QtCore.QPointF(
            self._cam_target.x() - float(delta.x()) * scale,
            self._cam_target.y() - float(delta.y()) * scale,
        )
        self.update()
        e.accept()
        return True
    return False

def _handle_mouse_move_legacy_dolly(self, e):
    if self._dolly_dragging and self._dolly_press_pos is not None:
        try:
            if not (e.modifiers() & QtCore.Qt.AltModifier):
                self._dolly_dragging = False
                self._dolly_press_pos = None
                self._dolly_start_dist = None
                self.setCursor(QtCore.Qt.ArrowCursor)
                e.accept()
                return True
        except Exception:
            pass
        if not (e.buttons() & QtCore.Qt.RightButton):
            self._dolly_dragging = False
            self._dolly_press_pos = None
            self._dolly_start_dist = None
            self.setCursor(QtCore.Qt.ArrowCursor)
            e.accept()
            return True
        dx = e.pos().x() - self._dolly_press_pos.x()
        dy = e.pos().y() - self._dolly_press_pos.y()
        distance = dy - dx
        exponent = abs(distance) / self._drag_divisor
        base = self._zoom_multiplier
        factor = base ** (-exponent) if distance > 0 else base ** (exponent)
        target = (self._dolly_start_dist or self._cam_dist) / factor
        self._cam_dist = max(self._min_cam_dist, min(self._max_cam_dist, target))
        self.update()
        e.accept()
        return True
    return False

def _handle_mouse_release_rot_shared_arc(self, e):
    if e.button() == QtCore.Qt.LeftButton and bool(getattr(self, "_rot_shared_arc_active", False)):
        owner = getattr(self, "_rot_shared_owner", None)
        self._rot_shared_arc_active = False
        self._handle_mouse_release_moderngl_commit_retarget_pose_if_needed(owner=owner)
        self._commit_xform_history()
        self.update()
        e.accept()
        return True
    return False

def _handle_mouse_release_rot_shared_view(self, e, rot_shared):
    # --- ROT_SHARED view-ring drag end ---
    if (
        e.button() == QtCore.Qt.LeftButton
        and rot_shared is not None
        and bool(getattr(rot_shared, "drag_view", False))
    ):
        owner = getattr(self, "_rot_shared_owner", None)
        try:
            rot_shared.end_view_ring_drag()
        except Exception:
            pass
        self._handle_mouse_release_moderngl_commit_retarget_pose_if_needed(owner=owner)
        self._commit_xform_history()
        self.update()
        e.accept()
        return True
    return False

def _handle_mouse_release_rot_shared_axis(self, e, rot_shared):
    # --- ROT_SHARED axis-ring drag end ---
    if (
        e.button() == QtCore.Qt.LeftButton
        and rot_shared is not None
        and bool(getattr(getattr(rot_shared, "drag_axis", None), "active", False))
    ):
        owner = getattr(self, "_rot_shared_owner", None)
        try:
            rot_shared.end_axis_drag()
        except Exception:
            pass
        self._rot_shared_axis_center_world = None
        self._rot_shared_axis = None
        self._rot_shared_axis_start_euler_deg = None
        self._rot_shared_axis_last_ang_deg = 0.0
        self._handle_mouse_release_moderngl_commit_retarget_pose_if_needed(owner=owner)
        self._commit_xform_history()
        self.update()
        e.accept()
        return True
    return False

def _handle_mouse_release_moderngl(self, e):
    if self._use_moderngl:
        if e.button() == QtCore.Qt.RightButton:
            self._handle_mouse_release_moderngl_right_button_nav()
        if self._handle_mouse_release_moderngl_retarget_drag(e):
            return True
        # --- 1) If we were dragging the gizmo, ALWAYS end that first ---
        if self._handle_mouse_release_moderngl_end_xform_drag(e):
            return True

        # --- 2) Normal click-pick (only if it was a click, not a drag) ---
        self._handle_mouse_release_moderngl_click_pick(e)

        # --- 3) Always end camera interactions cleanly ---
        self._handle_mouse_release_moderngl_end_camera_interactions(e)

        # Safety: if we somehow grabbed the mouse, release it now
        try:
            if QtWidgets.QApplication.mouseGrabber() is self:
                self.releaseMouse()
        except Exception:
            pass

        self.setCursor(QtCore.Qt.ArrowCursor)
        _graph_gl_view_super(self).mouseReleaseEvent(e)
        return True

    return False

def _handle_mouse_release_moderngl_retarget_drag(self, e):
    drag = getattr(self, "_retarget_joint_drag", None)
    if not isinstance(drag, dict):
        return False
    if e.button() != QtCore.Qt.LeftButton:
        return False
    renderer = getattr(self, "_mgl_renderer", None) or self
    target = None
    pick = getattr(renderer, "pick_retarget_joint_at", None)
    if callable(pick):
        try:
            px, py, vw, vh = self._handle_mouse_retarget_viewport(e)
            target = pick(px, py, vw, vh, role="target")
        except Exception:
            target = None
    if not isinstance(target, dict):
        target = drag.get("target_hover") if isinstance(drag.get("target_hover"), dict) else None

    source = drag.get("source") if isinstance(drag.get("source"), dict) else None
    if isinstance(source, dict) and isinstance(target, dict):
        linker = getattr(renderer, "_mgl_retarget_set_joint_link", None)
        if callable(linker):
            try:
                linker(str(source.get("name") or ""), str(target.get("name") or ""))
            except Exception:
                pass

    self._retarget_joint_drag = None
    try:
        self._mgl_pick_press_pos = None
    except Exception:
        pass
    try:
        if QtWidgets.QApplication.mouseGrabber() is self:
            self.releaseMouse()
    except Exception:
        pass
    try:
        self.setCursor(QtCore.Qt.ArrowCursor)
    except Exception:
        pass
    try:
        self.update()
    except Exception:
        pass
    e.accept()
    return True

def _handle_mouse_release_moderngl_right_button_nav(self):
    try:
        orbit_enabled = bool(getattr(self, "_orbit_cam_enabled", True))
        fly_mode = bool(getattr(self, "_fly_mode_enabled", False))
        if orbit_enabled and bool(getattr(self, "_fps_camera_active", False)):
            self._fps_cam_sync_orbit_from_camera()
        if not fly_mode:
            self._fps_nav_active = False
            self._fps_nav_keys = set()
            self._fps_nav_look_last_pos = None
        else:
            self._fps_nav_look_last_pos = None
            try:
                anchor = getattr(self, "_fps_nav_cursor_anchor", None)
                if anchor is not None:
                    QtGui.QCursor.setPos(anchor)
            except Exception:
                pass
            self._fps_nav_cursor_anchor = None
            self._fps_nav_warping = False
        try:
            apply_locked = getattr(self, "_camera_selector_apply_fps_to_locked_owner", None)
            if callable(apply_locked):
                apply_locked(sync_ui=True)
        except Exception:
            pass
        if orbit_enabled:
            self._fps_camera_active = False
    except Exception:
        pass

def _handle_mouse_release_moderngl_end_xform_drag(self, e):
    if not getattr(self, "_xform_dragging", False):
        return False

    self._handle_mouse_release_moderngl_commit_retarget_pose_if_needed(
        owner=getattr(self, "_xform_drag_owner", None)
    )
    self._commit_xform_history()
    self._handle_mouse_release_moderngl_log_splat_drag_end()
    self._handle_mouse_release_moderngl_reset_xform_drag_state()

    # Important: don't let a gizmo drag "fall through" into click-pick or orbit
    self._mgl_pick_press_pos = None

    # Safety: if we somehow grabbed the mouse, release it now
    self._handle_mouse_release_moderngl_release_mouse_grab()

    self.setCursor(QtCore.Qt.ArrowCursor)
    e.accept()
    return True

def _handle_mouse_release_moderngl_commit_retarget_pose_if_needed(self, owner=None):
    try:
        owner = owner or getattr(self, "_xform_drag_owner", None) or getattr(self, "_rot_shared_owner", None)
        if not owner:
            owner = getattr(self, "_xform_gizmo_owner", None)
        renderer = getattr(self, "_mgl_renderer", None) or self
        is_pose_joint = getattr(renderer, "_mgl_retarget_owner_is_target_pose_joint", None)
        commit_pose = getattr(renderer, "_mgl_retarget_commit_target_pose_edit", None)
        if callable(is_pose_joint) and callable(commit_pose) and bool(is_pose_joint(owner)):
            commit_pose(notify_scene=True)
            return True
        decode_joint = getattr(renderer, "_mgl_scene_skeleton_decode_joint_owner", None)
        apply_joint_keys = getattr(renderer, "_mgl_scene_skeleton_apply_timeline_keys", None)
        if callable(decode_joint) and decode_joint(owner):
            if callable(apply_joint_keys):
                try:
                    apply_joint_keys(
                        owner,
                        getattr(self, "_timeline_keys", {}) or {},
                        float(getattr(self, "_timeline_fps", 24.0) or 24.0),
                    )
                except Exception:
                    pass
            if bool(getattr(self, "_timeline_scene_skeleton_dirty", False)):
                try:
                    self._timeline_save_to_disk()
                except Exception:
                    pass
                try:
                    self._timeline_scene_skeleton_dirty = False
                except Exception:
                    pass
            try:
                self._timeline_update_key_markers()
                self._timeline_refresh_coord_labels()
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False

def _handle_mouse_release_moderngl_log_splat_drag_end(self):
    # Log splat drag end with gizmo + xform state.
    try:
        if getattr(self, "_xform_drag_kind", None) == "splat":
            renderer = getattr(self, "_mgl_renderer", None) or self
            owner = getattr(self, "_xform_drag_owner", None)
            xf = {}
            get_xf = getattr(renderer, "_mgl_get_scene_splat_xform", None)
            if callable(get_xf) and owner:
                xf = get_xf(owner) or {}
            xf_pos = tuple((xf or {}).get("pos", (0.0, 0.0, 0.0)))
            pivot = self._handle_mouse_move_moderngl_xform_translate_apply_owner_pivot(
                owner=owner,
                renderer=renderer,
            )
            self._mgl_log(
                "splat: drag_end owner="
                + str(owner)
                + " gizmo_pos="
                + str(getattr(self, "_xform_gizmo_pos", None))
                + " xf_pos="
                + str(xf_pos)
                + " pivot="
                + str(pivot)
                + " drag_start="
                + str(getattr(self, "_xform_drag_start_pos", None))
            )
    except Exception:
        pass

def _handle_mouse_release_moderngl_reset_xform_drag_state(self):
    self._xform_dragging = False
    self._xform_drag_axis = None
    self._xform_drag_owner = None
    self._xform_drag_s0 = None
    self._xform_drag_kind = None
    self._xform_drag_mode = None
    self._xform_drag_start_scl = None
    self._xform_scale_start_dist = None
    self._xform_scale_start_px = None
    self._xform_scale_axis_world = None
    self._xform_scale_center_px = None
    self._xform_drag_axis_world = None
    self._xform_drag_plane_normal = None
    self._xform_drag_plane_start = None
    self._xform_gizmo_pos_locked = False

def _handle_mouse_release_moderngl_release_mouse_grab(self):
    try:
        if QtWidgets.QApplication.mouseGrabber() is self:
            self.releaseMouse()
    except Exception:
        pass

def _handle_mouse_release_moderngl_click_pick(self, e):
    if e.button() != QtCore.Qt.LeftButton:
        return
    try:
        if not self._handle_mouse_release_moderngl_click_pick_is_click(e):
            return
        renderer = getattr(self, "_mgl_renderer", None) or self
        owner = self._handle_mouse_release_moderngl_click_pick_owner(
            e=e,
            renderer=renderer,
        )
        self._handle_mouse_release_moderngl_click_pick_apply(owner=owner, renderer=renderer)
    except Exception:
        pass

def _handle_mouse_release_moderngl_click_pick_is_click(self, e):
    press = getattr(self, "_mgl_pick_press_pos", None)
    if press is None:
        return False
    dx = abs(int(e.x()) - int(press.x()))
    dy = abs(int(e.y()) - int(press.y()))
    return dx <= 8 and dy <= 8

def _handle_mouse_release_moderngl_click_pick_owner(self, *, e, renderer):
    pick = getattr(renderer, "pick_owner_at", None)
    if not callable(pick):
        return None

    px, py, vw, vh = self._handle_mouse_release_moderngl_click_pick_viewport(e=e)
    pick_hit = getattr(renderer, "pick_hit_at", None)
    if callable(pick_hit):
        owner, _hit = pick_hit(px, py, vw, vh)
        return owner
    return pick(px, py, vw, vh)

def _handle_mouse_release_moderngl_click_pick_viewport(self, *, e):
    dpr = self._handle_mouse_release_moderngl_click_pick_dpr()
    px = int(e.x() * dpr)
    py = int(e.y() * dpr)
    vw = int(self.width() * dpr)
    vh = int(self.height() * dpr)
    return px, py, vw, vh

def _handle_mouse_release_moderngl_click_pick_dpr(self):
    dpr = 1.0
    try:
        dpr = float(self.devicePixelRatioF())
    except Exception:
        try:
            dpr = float(self.devicePixelRatio())
        except Exception:
            dpr = 1.0
    return dpr

def _handle_mouse_release_moderngl_click_pick_apply(self, *, owner, renderer):
    if owner:
        self._handle_mouse_release_moderngl_pick_owner(owner, renderer)
    else:
        self._handle_mouse_release_moderngl_pick_empty()

def _handle_mouse_release_moderngl_pick_owner(self, owner, renderer):
    self._handle_mouse_release_moderngl_pick_owner_select(owner=owner, renderer=renderer)
    self._handle_mouse_release_moderngl_pick_owner_place_gizmo(owner=owner, renderer=renderer)

    # Allow outliner edits to reposition the gizmo after selection.
    try:
        self._xform_gizmo_pos_locked = False
    except Exception:
        pass

    self.update()

def _handle_mouse_release_moderngl_pick_owner_select(self, *, owner, renderer):
    w = self.window()
    if hasattr(w, "select_scene_asset"):
        w.select_scene_asset(owner)

    try:
        kind = getattr(renderer, "_mgl_last_pick_kind", None)
    except Exception:
        kind = None

    try:
        self._mgl_log("scene: pick owner=" + str(owner) + " kind=" + str(kind))
    except Exception:
        pass

    # Force gizmo to the owner pivot (stored xform) or bounds center fallback.
    self._xform_gizmo_owner = owner
    try:
        self._xform_gizmo_owner_kind = kind
    except Exception:
        self._xform_gizmo_owner_kind = None

def _handle_mouse_release_moderngl_pick_owner_place_gizmo(self, *, owner, renderer):
    try:
        is_splat = self._handle_mouse_release_moderngl_pick_owner_is_splat(owner=owner, renderer=renderer)
        xf_pos = self._handle_mouse_release_moderngl_pick_owner_xf_pos(
            owner=owner,
            renderer=renderer,
            is_splat=is_splat,
        )

        if is_splat:
            self._handle_mouse_release_moderngl_pick_owner_place_gizmo_splat(
                owner=owner,
                renderer=renderer,
                xf_pos=xf_pos,
            )
        else:
            self._handle_mouse_release_moderngl_pick_owner_place_gizmo_mesh(xf_pos=xf_pos)
    except Exception:
        pass

def _handle_mouse_release_moderngl_pick_owner_place_gizmo_splat(self, *, owner, renderer, xf_pos):
    pivot = self._handle_mouse_release_moderngl_pick_owner_splat_pivot(owner=owner, renderer=renderer)
    self._handle_mouse_release_moderngl_pick_owner_set_splat_pos(
        owner=owner,
        renderer=renderer,
        xf_pos=xf_pos,
        pivot=pivot,
    )
    try:
        self._mgl_log(
            "splat: select owner="
            + str(owner)
            + " xf_pos="
            + str(xf_pos)
            + " pivot="
            + str(pivot)
            + " gizmo_pos="
            + str(getattr(self, "_xform_gizmo_pos", None))
        )
    except Exception:
        pass

def _handle_mouse_release_moderngl_pick_owner_place_gizmo_mesh(self, *, xf_pos):
    # mesh: pos is already world pivot (even if zero)
    self._xform_gizmo_pos = (
        float(xf_pos[0]),
        float(xf_pos[1]),
        float(xf_pos[2]),
    )
    self._xform_gizmo_pos_locked = True

def _handle_mouse_release_moderngl_pick_owner_is_splat(self, *, owner, renderer):
    is_splat = False
    try:
        splat_map = getattr(renderer, "_mgl_scene_splats_world", None)
        if not isinstance(splat_map, dict) or not splat_map:
            splat_map = getattr(renderer, "_mgl_scene_splats", None)
        if isinstance(splat_map, dict):
            if owner in splat_map:
                is_splat = True
            else:
                owner_l = str(owner or "").strip().lower()
                for k in splat_map.keys():
                    try:
                        if str(k).strip().lower() == owner_l:
                            is_splat = True
                            break
                    except Exception:
                        continue
    except Exception:
        is_splat = False
    return is_splat

def _handle_mouse_release_moderngl_pick_owner_xf_pos(self, *, owner, renderer, is_splat):
    xf = {}
    get_xf = (
        getattr(renderer, "_mgl_get_scene_splat_xform", None)
        if is_splat
        else getattr(renderer, "_mgl_get_scene_asset_xform", None)
    )
    if callable(get_xf):
        xf = get_xf(owner) or {}
    return tuple((xf or {}).get("pos", (0.0, 0.0, 0.0)))

def _handle_mouse_release_moderngl_pick_owner_splat_pivot(self, *, owner, renderer):
    # For splats, xform.pos is an offset from the local pivot.
    return self._handle_mouse_move_moderngl_xform_translate_apply_owner_pivot(
        owner=owner,
        renderer=renderer,
    )

def _handle_mouse_release_moderngl_pick_owner_splat_bounds_center(self, *, owner, renderer):
    try:
        bounds_map = (
            getattr(renderer, "_mgl_scene_splats_bounds_local", None)
            or getattr(renderer, "_mgl_scene_splat_bounds_by_owner", None)
        )
        if isinstance(bounds_map, dict):
            mins = maxs = None
            if owner in bounds_map:
                mins, maxs = bounds_map.get(owner) or (None, None)
            else:
                owner_l = str(owner or "").strip().lower()
                for k, v in bounds_map.items():
                    try:
                        if str(k).strip().lower() == owner_l:
                            mins, maxs = v or (None, None)
                            break
                    except Exception:
                        continue
            if mins is not None and maxs is not None:
                return (
                    (float(mins[0]) + float(maxs[0])) * 0.5,
                    (float(mins[1]) + float(maxs[1])) * 0.5,
                    (float(mins[2]) + float(maxs[2])) * 0.5,
                )
    except Exception:
        pass
    return None

def _handle_mouse_release_moderngl_pick_owner_set_splat_pos(self, *, owner, renderer, xf_pos, pivot):
    # splat xform.pos is an offset from local pivot
    if pivot is not None:
        self._xform_gizmo_pos = (
            float(xf_pos[0] + pivot[0]),
            float(xf_pos[1] + pivot[1]),
            float(xf_pos[2] + pivot[2]),
        )
        self._xform_gizmo_pos_locked = True
        return

    # fallback to bounds center
    center = self._handle_mouse_release_moderngl_pick_owner_splat_bounds_center(
        owner=owner,
        renderer=renderer,
    )
    if center is None:
        return
    self._xform_gizmo_pos = center
    self._xform_gizmo_pos_locked = True

def _handle_mouse_release_moderngl_pick_empty(self):
    # Clicked empty space: clear selection + hide gizmo
    try:
        self._mgl_log("scene: click empty -> clear selection")
    except Exception:
        pass

    # Clear gizmo selection state
    self._xform_gizmo_owner = None
    self._xform_gizmo_owner_kind = None
    self._xform_gizmo_pos_locked = False
    self._xform_gizmo_pos = (0.0, 0.0, 0.0)

    # Stop any active rotate drags safely
    try:
        rot_shared = getattr(self, "_rot_shared", None)
        if rot_shared is not None:
            rot_shared.end_drag()
    except Exception:
        pass

    # Clear outliner selection if the window exposes the helper
    try:
        w = self.window()
        if w is not None and hasattr(w, "clear_scene_asset_selection"):
            w.clear_scene_asset_selection()
    except Exception:
        pass

    self.update()

def _handle_mouse_release_moderngl_end_camera_interactions(self, e):
    if e.button() == QtCore.Qt.LeftButton:
        self._mgl_pick_press_pos = None
        self._mgl_orbit_dragging = False
        self._mgl_orbit_last_pos = None
        if self._mgl_arcball is not None:
            try:
                self._mgl_arcball.onClickLeftUp()
            except Exception:
                pass

    if e.button() == QtCore.Qt.RightButton:
        self._mgl_zoom_press_pos = None
        self._mgl_zoom_start = None
        self._mgl_zoom_center_start = None
        self._mgl_zoom_cam_start = None
        self._mgl_zoom_cam_dir = None
        self._mgl_zoom_ray_dir = None

def _handle_mouse_release_example_pipeline(self, e):
    if self._use_example_pipeline:
        if e.button() == QtCore.Qt.LeftButton:
            self._orbit_dragging = False
            self._orbit_last_pos = None
        if e.button() == QtCore.Qt.MiddleButton:
            self._pan_dragging = False
            self._pan_last_pos = None
        if e.button() == QtCore.Qt.RightButton:
            self._dolly_dragging = False
            self._dolly_press_pos = None
        self.setCursor(QtCore.Qt.ArrowCursor)
        _graph_gl_view_super(self).mouseReleaseEvent(e)
        return True
    return False

def _handle_mouse_release_legacy(self, e):
    if e.button() == QtCore.Qt.LeftButton:
        self._orbit_dragging = False
        self._orbit_last_pos = None
    if e.button() == QtCore.Qt.MiddleButton:
        self._pan_dragging = False
        self._pan_last_pos = None
    if e.button() == QtCore.Qt.RightButton:
        self._dolly_dragging = False
        self._dolly_press_pos = None
        self._dolly_start_dist = None
    self.setCursor(QtCore.Qt.ArrowCursor)
    _graph_gl_view_super(self).mouseReleaseEvent(e)

def mouseReleaseEvent(self, e):
    rot_shared = getattr(self, "_rot_shared", None)

    if self._handle_mouse_release_rot_shared_arc(e):
        return

    if self._handle_mouse_release_rot_shared_view(e, rot_shared):
        return

    if self._handle_mouse_release_rot_shared_axis(e, rot_shared):
        return


    if self._handle_mouse_release_moderngl(e):
        return

    # --- non-ModernGL paths unchanged ---
    if self._handle_mouse_release_example_pipeline(e):
        return

    self._handle_mouse_release_legacy(e)

def wheelEvent(self, e):
    if bool(getattr(self, "_fps_nav_active", False)):
        try:
            delta = e.angleDelta().y() / 120.0
        except Exception:
            delta = 0.0
        if delta:
            try:
                step = float(getattr(self, "_fps_nav_speed_step", 1.15))
            except Exception:
                step = 1.15
            speed = float(getattr(self, "_fps_nav_speed", 2.0))
            speed *= step ** float(delta)
            try:
                speed = max(float(getattr(self, "_fps_nav_speed_min", 0.1)), speed)
                speed = min(float(getattr(self, "_fps_nav_speed_max", 50.0)), speed)
            except Exception:
                pass
            self._fps_nav_speed = speed
            try:
                self.update()
            except Exception:
                pass
        try:
            e.accept()
        except Exception:
            pass
        return
    if self._use_moderngl:
        try:
            if not (e.modifiers() & QtCore.Qt.AltModifier):
                _graph_gl_view_super(self).wheelEvent(e)
                return
        except Exception:
            pass
        delta = e.angleDelta().y()
        if delta:
            self._mgl_camera_zoom += delta * 0.001
            min_zoom = float(getattr(self, "_mgl_min_zoom", 0.001))
            if self._mgl_camera_zoom < min_zoom:
                self._mgl_camera_zoom = min_zoom
            self.update()
        e.accept()
        return
    if self._use_example_pipeline:
        try:
            if not (e.modifiers() & QtCore.Qt.AltModifier):
                _graph_gl_view_super(self).wheelEvent(e)
                return
        except Exception:
            pass
        delta = e.angleDelta().y() / 120.0
        if delta:
            self._example_zoom(delta)
            self.update()
        e.accept()
        return
    _graph_gl_view_super(self).wheelEvent(e)

def install_graph_gl_view_mouse_methods(cls):
    _ensure_gl_view_globals()
    for _method_name in _MOUSE_METHOD_NAMES:
        _fn = globals().get(_method_name)
        if callable(_fn):
            setattr(cls, _method_name, _fn)

__all__ = ['install_graph_gl_view_mouse_methods']
