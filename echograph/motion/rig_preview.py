"""Rig inspection geometry using export proportions and interactive pose limits."""
from pathlib import Path
import json
import numpy as np
from scipy.spatial.transform import Rotation

from .conversion import (JOINT_NAMES, PARENTS, REST_DIRECTIONS, load_motion_archive,
                         reconstruct_animation, write_bvh)
from .rig_limits import PREVIEW_JOINT_LIMITS, rotation_parts, preview_limit_fields, clamp_preview_rotation
from .self_collision import ARMS, body_capsules, world_pose


def preview_offsets(profile, archive=None):
    if profile == 'standardman':
        data = json.loads((Path(__file__).parent/'profiles/standardman.json').read_text())
        return np.asarray(data['offsets'], dtype=float)
    if profile != 'model':
        raise ValueError(f'Unknown preview proportions: {profile}')
    if not archive or not Path(archive).is_file():
        raise ValueError('Original model proportions need a generated motion. Generate once, or select StandardMan in Text to Motion.')
    positions, _, _ = load_motion_archive(Path(archive))
    lengths = np.array([0.] + [np.median(np.linalg.norm(positions[:,j]-positions[:,PARENTS[j]], axis=1)) for j in range(1,22)])
    if np.any(lengths[1:] < 1e-7):
        raise ValueError('The last motion has degenerate bones; generate a new motion.')
    return REST_DIRECTIONS * lengths[:,None]


def _basis(axis):
    axis = axis / np.linalg.norm(axis)
    u = np.cross(axis, np.eye(3)[np.argmin(abs(axis))])
    u /= np.linalg.norm(u)
    return axis, u, np.cross(axis, u)


def capsule_surfaces(a, b, radius):
    direction = b-a
    axis, u, v = _basis(direction if np.linalg.norm(direction)>1e-8 else np.array([0.,1.,0.]))
    theta = np.linspace(0, 2*np.pi, 13)
    circle = np.cos(theta)[:,None]*u + np.sin(theta)[:,None]*v
    rings = []
    for center, angles in ((a, np.linspace(-np.pi/2,0,3)), (b,np.linspace(0,np.pi/2,3))):
        for angle in angles:
            rings.append(center + radius*(axis*np.sin(angle)+circle*np.cos(angle)))
    faces = [np.array([lo[j],lo[j+1],hi[j+1],hi[j]]) for lo,hi in zip(rings[:-1],rings[1:]) for j in range(12)]
    return faces, [rings[2], rings[3], np.array([a+radius*u,b+radius*u]), np.array([a-radius*u,b-radius*u])]


def build_guide(offsets, matrices=None, *, joint_limits=None):
    if matrices is None:
        points, _ = world_pose(np.tile(np.eye(3),(22,1,1)), offsets, np.zeros(3))
        points[:,1] -= points[:,1].min()
    else:
        matrices = np.asarray(matrices).reshape(22,4,4)
        points = matrices[:,:3,3].copy()
    scale = float(np.mean(np.linalg.norm(offsets[[4,5]],axis=1)))/.44
    shapes, capsules, seen = [], [], set()
    # Union of both arm obstacle sets, retaining the solver's actual radii.
    for arm in ARMS:
        starts, ends, radii = body_capsules(points,scale,arm)
        for a,b,r in zip(starts,ends,radii):
            key = tuple(np.r_[a,b,r])
            if key not in seen:
                seen.add(key)
                capsules.append((a,b,r))
    for wrist in (20,21):
        capsules.append((points[wrist],points[wrist],.06*scale))
    for a,b,r in capsules:
        faces, wires = capsule_surfaces(a,b,r)
        shapes.append(dict(kind='collision',joint='',faces=faces,wires=wires))
    labels = {'pelvis':'Pelvis: unrestricted root rotation; flips remain possible.',
              'head':'Head: inherits neck orientation; no independent inferred twist.',
              'left_wrist':'Wrist: follows forearm; fingers are outside collision cleanup.',
              'right_wrist':'Wrist: follows forearm; fingers are outside collision cleanup.'}
    children = {j:next((i for i,p in enumerate(PARENTS) if p==j),None) for j in range(22)}
    limits = PREVIEW_JOINT_LIMITS if joint_limits is None else joint_limits
    for joint, limit in limits.items():
        fields = preview_limit_fields(joint,limit)
        bend_min,bend_max = fields['bend_min'],fields['bend_max']
        twist_min,twist_max = fields['twist_min'],fields['twist_max']
        origin = points[joint]
        axis,u,v = _basis(offsets[children[joint]])
        length = .13*scale
        faces, wires = [], []
        local = np.eye(3) if matrices is None else matrices[PARENTS[joint],:3,:3].T @ matrices[joint,:3,:3]
        swing, twist = rotation_parts(Rotation.from_matrix(local).as_quat(),axis)
        bend_degrees = float(np.degrees(swing.magnitude()))
        twist_degrees = float(np.degrees(twist))
        readout = ''
        if 'flex' in limit:
            angles = np.linspace(np.deg2rad(bend_min),np.deg2rad(bend_max),33)
            arc = origin + Rotation.from_rotvec(angles[:,None]*np.array(limit['axis'])).apply(np.tile(axis*length,(len(angles),1)))
            text = f"Bend {bend_min:g}–{bend_max:g}°; anatomical hinge, no axial twist"
            wires.append(arc)
            faces.extend(np.array([origin,a,b]) for a,b in zip(arc[:-1],arc[1:]))
            ticks = np.unique(np.r_[bend_min,np.arange(np.ceil(bend_min/30)*30,bend_max,30),bend_max])
            for tick in ticks:
                direction = Rotation.from_rotvec(np.array(limit['axis'])*np.deg2rad(tick)).apply(axis)
                wires.append(np.array([origin+length*.90*direction,origin+length*1.06*direction]))
            _, angle = rotation_parts(Rotation.from_matrix(local).as_quat(),limit['axis'])
            bend_degrees = float(np.degrees(angle))
            hit = bend_degrees >= bend_max-.25 or bend_degrees <= bend_min+.25
            shapes.append(dict(kind='limit_hit' if hit else 'pointer',joint=JOINT_NAMES[joint],faces=[],
                               wires=[np.array([origin,origin+local@axis*length])]))
            readout = f"Current bend {bend_degrees:.1f}° / {limit['flex']:g}°"
        else:
            twist_range = f"±{twist_max:g}°" if twist_min == -twist_max else f"{twist_min:g}° to {twist_max:g}°"
            text = 'Twist '+twist_range
            if 'swing' in limit:
                bend = np.deg2rad(limit['swing'])
                theta = np.linspace(0,2*np.pi,33)
                rim = origin+length*(np.cos(bend)*axis+np.sin(bend)*(np.cos(theta)[:,None]*u+np.sin(theta)[:,None]*v))
                wires.append(rim)
                # Keep only the rim and straight sides of the cone.
                wires.extend(np.array([origin,rim[i]]) for i in range(0,32,8))
                faces.extend(np.array([origin,a,b]) for a,b in zip(rim[:-1],rim[1:]))
                if bend_min > 0:
                    inner = origin+length*(np.cos(np.deg2rad(bend_min))*axis+np.sin(np.deg2rad(bend_min))*(np.cos(theta)[:,None]*u+np.sin(theta)[:,None]*v))
                    wires.append(inner)
                    wires.extend(np.array([origin,inner[i]]) for i in range(0,32,8))
                text = (f"Bend {bend_min:g}–{bend_max:g}°; " if bend_min else f"Bend ≤{bend_max:g}°; ")+text
                hit = bend_degrees >= bend_max-.25 or (bend_min > 0 and bend_degrees <= bend_min+.25)
                shapes.append(dict(kind='limit_hit' if hit else 'pointer',joint=JOINT_NAMES[joint],faces=[],
                                   wires=[np.array([origin,origin+local@axis*length])]))
                readout = f"Current bend {bend_degrees:.1f}° / {limit['swing']:g}°"
            if 'twist' in limit:
                theta = np.linspace(np.deg2rad(twist_min),np.deg2rad(twist_max),33)
                center = origin
                arc = center+length*.7*(np.cos(theta)[:,None]*u+np.sin(theta)[:,None]*v)
                ticks = np.unique(np.r_[twist_min,np.arange(np.ceil(twist_min/30)*30,twist_max,30),twist_max])
                dial_wires = [arc]
                for tick in ticks:
                    direction = np.cos(np.deg2rad(tick))*u+np.sin(np.deg2rad(tick))*v
                    dial_wires.append(np.array([center+length*.64*direction,center+length*.75*direction]))
                shapes.append(dict(kind='twist',joint=JOINT_NAMES[joint],faces=[np.array([center,a,b]) for a,b in zip(arc[:-1],arc[1:])],wires=dial_wires,follow_swing=True))
                hit = twist_degrees >= twist_max-.25 or twist_degrees <= twist_min+.25
                pointer = Rotation.from_rotvec(axis*twist).apply(u)
                shapes.append(dict(kind='limit_hit' if hit else 'pointer',joint=JOINT_NAMES[joint],faces=[],
                                   wires=[np.array([center,center+length*.7*pointer])],follow_swing=True))
                readout += f"; twist {twist_degrees:.1f}° / {twist_range}"
        labels[JOINT_NAMES[joint]] = f'{JOINT_NAMES[joint]}: {text}'
        labels[JOINT_NAMES[joint]] += '\n'+readout.lstrip('; ')
        if any(s['joint']==JOINT_NAMES[joint] and s['kind']=='limit_hit' for s in shapes):
            labels[JOINT_NAMES[joint]] += ' — AT LIMIT'
        shapes.append(dict(kind='limit',joint=JOINT_NAMES[joint],faces=faces,wires=wires))
    if matrices is not None:
        # Bend ranges follow the parent. Twist dials additionally follow swing
        # so their planes stay perpendicular to the posed bone; only their
        # pointers include axial twist, preserving a readable angular range.
        for shape in shapes:
            if shape['kind'] == 'collision':
                continue
            joint = JOINT_NAMES.index(shape['joint'])
            rotation = matrices[PARENTS[joint],:3,:3]
            if shape.get('follow_swing'):
                axis = offsets[children[joint]] / np.linalg.norm(offsets[children[joint]])
                local = rotation.T @ matrices[joint,:3,:3]
                swing,_ = rotation_parts(Rotation.from_matrix(local).as_quat(),axis)
                rotation = rotation @ swing.as_matrix()
            for key in ('faces','wires'):
                shape[key] = [(rows-points[joint]) @ rotation.T + points[joint] for rows in shape[key]]
    return points, dict(shapes=shapes,labels=labels,offsets=offsets.copy(),
                        joint_limits={j:dict(limit) for j,limit in limits.items()})


def create_rig_asset(profile, archive, output_dir, owner, *, collisions=True, limits=True, joint_limits=None):
    from .rig_preview_settings import load_preview_limits
    from echograph.rigging.fbx_canonical import QuatKeyframe
    joint_limits = load_preview_limits() if joint_limits is None else joint_limits
    offsets = preview_offsets(profile,archive)
    points, guide = build_guide(offsets,joint_limits=joint_limits)
    positions = np.repeat(points[None],2,axis=0)
    skeleton, clip, rotations, _ = reconstruct_animation(positions,20,{},rest_offsets=offsets)
    # Nonzero saved minima also constrain the initial preview pose.
    for joint in joint_limits:
        child = next(i for i,p in enumerate(PARENTS) if p==joint)
        axis = offsets[child]/np.linalg.norm(offsets[child])
        quat = clamp_preview_rotation(joint,[0.,0.,0.,1.],axis,limits=joint_limits)
        rotations[:,joint] = Rotation.from_quat(quat).as_matrix()
        clip.tracks[joint].rotation_keys = [QuatKeyframe(f/20,tuple(quat)) for f in range(2)]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    path = output_dir/'rig.bvh'
    write_bvh(path,positions,20,rotations,offsets)
    guide.update(profile=profile,collisions=collisions,limits=limits,
                 saved_joint_limits={j:dict(limit) for j,limit in joint_limits.items()})
    context = dict(skeleton=skeleton,clip=clip,meshes=[],loop=False,mesh_skinning_enabled=False,
                   show_capture_joints=False,show_animated_joints=True,source_format='bvh',priormdm_rig_debug=guide)
    return dict(path=str(path.resolve()),node=owner,kind='mocap',ext='.bvh',visible=True,fbx_rig_context=context)
