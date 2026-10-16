"""Read-only Rig Limiter volumes for characters in the scene viewport."""
import numpy as np

from echograph.motion.hand_rig import full_rig_layout
from echograph.motion.rig_preview import build_guide
from echograph.motion.rig_preview_settings import load_preview_volumes


def collider_guide(context):
    existing = context.get('priormdm_rig_debug')
    if existing:
        guide = dict(existing)
    else:
        matrices = None
        end_sites = context['skeleton'].metadata.get('end_sites')
        try:
            indices, names, parents, offsets = full_rig_layout(context['skeleton'])
        except (KeyError, ValueError):
            from echograph.motion.rig_preview_mapping import mapped_preview_layout
            indices, names, parents, offsets, matrices, end_sites = mapped_preview_layout(context['skeleton'])
        _, guide = build_guide(
            offsets, matrices, joint_limits={}, volumes=load_preview_volumes(),
            names=names, parents=parents,
            end_sites=end_sites)
        guide['joint_indices'] = indices
    guide.update(collisions=True, limits=False, read_only=True)
    guide.pop('_posed_shapes', None)
    return guide


def draw_scene_colliders(renderer, painter):
    enabled = getattr(renderer, '_scene_rig_collider_overlays', {})
    if not enabled:
        return
    from .priormdm_rig_overlay import draw_rig_guide
    matrices = [getattr(renderer, '_mgl_pick_' + name, None)
                for name in ('proj', 'view', 'model')]
    if any(m is None for m in matrices):
        return
    projection = np.asarray(matrices[0]) @ np.asarray(matrices[1]) @ np.asarray(matrices[2])
    width, height = renderer.width(), renderer.height()
    rw, rh = renderer._mgl_render_size()
    active = getattr(renderer, '_mgl_active_viewport_rect', None)
    if active is None:
        active = renderer._mgl_active_render_viewport()
    ax, ay, aw, ah = active if active is not None else (0, 0, rw, rh)
    sx, sy = width / max(1, rw), height / max(1, rh)
    viewport = (ax*sx, height-(ay+ah)*sy, aw*sx, ah*sy)
    for owner, cached in list(enabled.items()):
        # The skeleton collider toggle is independent of the geometry's eye.
        # Missing/deleted owners are still rejected by the context lookup.
        context = renderer._mgl_scene_owner_fbx_rig_context(owner)
        if not context or context.get('skeleton') is None:
            continue
        if cached[0] is not context['skeleton']:
            try:
                cached = (context['skeleton'], collider_guide(context))
            except ValueError:
                continue
            enabled[owner] = cached
        draw_rig_guide(renderer, painter, dict(
            owner=owner, rig_guide=cached[1], rig_context=context,
            rig_projection=projection, rig_viewport=viewport))
