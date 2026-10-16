"""Lit collider previews using the viewport's standard mesh material pipeline."""
import numpy as np

from .gl_scene import MGLSceneItem
from .priormdm_rig_overlay import rig_guide_shapes
from .scene_rig_overlay import collider_guide


TAG = 'scene-rig-collider-solid'
COLOR = (65/255,210/255,235/255,1.)


def collider_mesh_arrays(faces):
    """Triangulate one convex capsule and average normals at shared vertices."""
    if not faces:
        return np.empty((0,3),dtype='f4'),np.empty((0,3),dtype='f4')
    triangles = np.asarray([face[[0,j,j+1]] for face in faces for j in range(1,len(face)-1)],dtype=float)
    normals = np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
    valid = np.linalg.norm(normals,axis=1)>1e-12
    triangles,normals = triangles[valid],normals[valid]
    if not len(triangles):
        return np.empty((0,3),dtype='f4'),np.empty((0,3),dtype='f4')
    center = np.concatenate(faces).mean(axis=0)
    flip = np.einsum('ij,ij->i',normals,triangles.mean(axis=1)-center)<0
    triangles[flip] = triangles[flip][:,[0,2,1]]
    normals[flip] *= -1
    points = triangles.reshape(-1,3)
    unique,inverse = np.unique(np.round(points,6),axis=0,return_inverse=True)
    smooth = np.zeros_like(unique)
    np.add.at(smooth,inverse,np.repeat(normals,3,axis=0))
    smooth /= np.maximum(np.linalg.norm(smooth,axis=1,keepdims=True),1e-12)
    return points.astype('f4'),smooth[inverse].astype('f4')


def solid_collider_data(renderer):
    data = {}
    enabled = getattr(renderer,'_scene_rig_collider_overlays',{})
    for owner,cached in list(enabled.items()):
        context = renderer._mgl_scene_owner_fbx_rig_context(owner)
        if not context or context.get('skeleton') is None:
            continue
        if cached[0] is not context['skeleton']:
            try:
                cached = (context['skeleton'],collider_guide(context))
            except ValueError:
                continue
            enabled[owner] = cached
        data[owner.casefold()] = dict(owner=owner,rig_guide=cached[1],rig_context=context)
    active = renderer._mgl_scene_skeleton_qt_overlay_data()
    if active and active.get('rig_guide',{}).get('collisions'):
        data[active['owner'].casefold()] = active
    return [entry for entry in data.values() if entry['rig_guide'].get('collisions')]


def draw_solid_rig_colliders(renderer,mvp):
    scene = getattr(renderer,'_mgl_scene',None)
    if scene is None:
        return
    if getattr(renderer,'_rig_colliders_xray',True):
        scene.remove_by_tag(TAG)
        return
    if getattr(renderer,'_mgl_render_output_active',False) or getattr(renderer,'_mgl_render_target_fbo_id',0):
        return
    positions,normals = [],[]
    for data in solid_collider_data(renderer):
        shapes,_ = rig_guide_shapes(renderer,data)
        for shape in shapes:
            if shape['kind'] != 'collision':
                continue
            faces = shape['faces']
            if not faces:
                continue
            sizes = np.cumsum([len(face) for face in faces])[:-1]
            points,_ = renderer._mgl_scene_skeleton_display_positions(data['owner'],np.concatenate(faces))
            points,normal = collider_mesh_arrays(np.split(points,sizes))
            positions.append(points)
            normals.append(normal)
    if not positions or not sum(len(points) for points in positions):
        scene.remove_by_tag(TAG)
        return
    points,normal = np.concatenate(positions),np.concatenate(normals)
    item = next(iter(scene.iter_by_tag(TAG)),None)
    entry = item.payload['mesh_entry'] if item is not None else None
    if entry is not None and entry['points'].shape != points.shape:
        scene.remove_by_tag(TAG)
        item = entry = None
    if item is None:
        entry = renderer._mgl_build_mesh_entry(points,normal)
        if entry is None:
            return
        item = MGLSceneItem('__rig_colliders__',type(renderer)._mgl_draw_scene_mesh,
            payload=dict(vao=entry['vao'],mesh_entry=entry,color=COLOR,
                         force_opaque_mesh=True,two_sided=True),
            resources=[entry.get(key) for key in ('vao','vbo','nbo','tbo','cbo','ibo')],
            tag=TAG,visible=False)
        # Scene ownership releases GPU resources on scene reload/close. The
        # ordinary scene pass skips this item; this preview pass draws it once.
        scene.add(item)
    elif not np.array_equal(entry['points'],points):
        entry['vbo'].write(points.tobytes())
        entry['nbo'].write(normal.tobytes())
        entry['points'],entry['normals'] = points,normal
    renderer._mgl_draw_scene_mesh(item,mvp)
