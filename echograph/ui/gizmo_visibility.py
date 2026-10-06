"""Shared selection and visibility rules for drawing and picking transform gizmos."""


def has_gizmo_selection(view):
    owner = str(getattr(view, '_xform_gizmo_owner', '') or '').strip()
    if not owner:
        return False
    # Looking through a selected camera must not draw or pick that camera's
    # handles at the eye. Other selected objects keep their normal gizmos.
    active_camera = getattr(view, '_mgl_active_scene_camera_owner', None)
    camera_owner = (active_camera() if callable(active_camera)
                    else getattr(view, '_camera_select_mode', 'default'))
    if str(camera_owner or '').strip().casefold() == owner.casefold():
        return False
    kind = getattr(view, '_xform_gizmo_owner_kind', None)
    if kind in ('scene_skeleton_joint', 'retarget_target_joint'):
        return True
    mode = getattr(view, '_mesh_select_mode', '')
    if mode in ('point', 'edge', 'face'):
        elements = getattr(view, '_mesh_select_selected_many', None) or []
        if not elements:
            selected = getattr(view, '_mesh_select_selected', None)
            elements = [selected] if isinstance(selected, dict) else []
        return any(isinstance(element, dict) and element.get('mode') == mode
                   for element in elements)
    return True


def sync_gizmo_visibility(view):
    # Remember the user's switch separately from automatic selection hiding.
    enabled = bool(getattr(view, '_mgl_gizmo_enabled',
                           getattr(view, '_mgl_gizmo_visible', True)))
    view._mgl_gizmo_enabled = enabled
    visible = enabled and has_gizmo_selection(view)
    view._mgl_gizmo_visible = visible
    toggle = getattr(view, '_mgl_gizmo_toggle', None)
    if toggle is not None and toggle.isChecked() != visible:
        blocked = toggle.blockSignals(True)
        try:
            toggle.setChecked(visible)
        finally:
            toggle.blockSignals(blocked)
    return visible
