"""Rig inspection geometry using export proportions and interactive pose limits."""
from pathlib import Path
import json
import numpy as np
from scipy.spatial.transform import Rotation

from .conversion import (JOINT_NAMES, PARENTS, REST_DIRECTIONS, load_motion_archive,
                         reconstruct_animation)
from .rig_limits import PREVIEW_JOINT_LIMITS, rotation_parts, preview_limit_fields, clamp_preview_rotation
from echograph.rigging.ik import forward_kinematics
from .rig_volumes import capsules, volume_settings, rig_scale
from .hand_rig import primary_child


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


def capsule_surfaces(a, b, radius, end_radius=None):
    end_radius = radius if end_radius is None else end_radius
    direction = b-a
    length = np.linalg.norm(direction)
    if abs(end_radius-radius) >= length and length > 1e-8:
        center = b if end_radius > radius else a
        return capsule_surfaces(center, center, max(radius, end_radius))
    if length < 1e-8:
        radius = end_radius = max(radius, end_radius)
    axis, u, v = _basis(direction if np.linalg.norm(direction)>1e-8 else np.array([0.,1.,0.]))
    theta = np.linspace(0, 2*np.pi, 13)
    circle = np.cos(theta)[:,None]*u + np.sin(theta)[:,None]*v
    rings = []
    tangent = np.arcsin(np.clip((radius-end_radius)/max(length,1e-8),-1,1))
    for center, r, angles in ((a, radius, np.linspace(-np.pi/2,tangent,3)),
                              (b, end_radius, np.linspace(tangent,np.pi/2,3))):
        for angle in angles:
            rings.append(center + r*(axis*np.sin(angle)+circle*np.cos(angle)))
    faces = [np.array([lo[j],lo[j+1],hi[j+1],hi[j]]) for lo,hi in zip(rings[:-1],rings[1:]) for j in range(12)]
    return faces, [rings[2], rings[3], np.array([rings[2][0],rings[3][0]]), np.array([rings[2][6],rings[3][6]])]


def build_guide(offsets, matrices=None, *, joint_limits=None, volumes=None,
                names=JOINT_NAMES, parents=PARENTS,end_sites=None):
    if matrices is None:
        points, world = forward_kinematics(np.tile(np.eye(3),(len(names),1,1)), offsets, parents)
        points[:,1] -= points[:,1].min()
    else:
        matrices = np.asarray(matrices).reshape(len(names),4,4)
        world = matrices[:,:3,:3]
        points = matrices[:,:3,3].copy()
    scale = rig_scale(offsets)
    shapes = []
    volumes = volume_settings(volumes)
    for name,a,b,r in capsules(points,scale,volumes,world=world,offsets=offsets,names=names,parents=parents,end_sites=end_sites):
        faces, wires = capsule_surfaces(a,b,*r)
        shapes.append(dict(kind='collision',joint=name,faces=faces,wires=wires,volume=name))
    labels = {'pelvis':'Pelvis: unrestricted root rotation; flips remain possible.',
              'head':'Head: inherits neck orientation; no independent inferred twist.',
              'left_wrist':'Palm: follows the wrist; finger contacts use individual capsules.',
              'right_wrist':'Palm: follows the wrist; finger contacts use individual capsules.'}
    children = {j:primary_child(j,names,parents) for j in range(len(names))}
    limits = PREVIEW_JOINT_LIMITS if joint_limits is None else joint_limits
    for joint, limit in limits.items():
        fields = preview_limit_fields(joint,limit)
        bend_min,bend_max = fields['bend_min'],fields['bend_max']
        twist_min,twist_max = fields['twist_min'],fields['twist_max']
        origin = points[joint]
        axis,u,v = _basis(offsets[children[joint]])
        length = min(.13*scale,max(.018*scale,np.linalg.norm(offsets[children[joint]])*.7)) if joint >= 22 else .13*scale
        faces, wires = [], []
        local = np.eye(3) if matrices is None else matrices[parents[joint],:3,:3].T @ matrices[joint,:3,:3]
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
            shapes.append(dict(kind='limit_hit' if hit else 'pointer',joint=names[joint],faces=[],
                               wires=[np.array([origin,origin+local@axis*length])]))
            readout = f"Current bend {bend_degrees:.1f}° / {limit['flex']:g}°"
            if 'spread_axis' in limit:
                curl = Rotation.from_rotvec(np.asarray(limit['axis'])*np.deg2rad(bend_degrees))
                spread_axis = np.asarray(limit['spread_axis'])
                spread = float(np.degrees(rotation_parts(Rotation.from_matrix(local).as_quat(),limit['axis'])[0].as_rotvec()@spread_axis))
                angles = np.deg2rad(np.linspace(twist_min,twist_max,33))
                arc = origin+Rotation.from_rotvec(angles[:,None]*spread_axis).apply(np.tile(curl.apply(axis)*length*.8,(len(angles),1)))
                shapes.append(dict(kind='twist',joint=names[joint],faces=[],wires=[arc]))
                text += f"; spread {twist_min:g}° to {twist_max:g}°"
                readout += f"; spread {spread:.1f}°"
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
                shapes.append(dict(kind='limit_hit' if hit else 'pointer',joint=names[joint],faces=[],
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
                shapes.append(dict(kind='twist',joint=names[joint],faces=[np.array([center,a,b]) for a,b in zip(arc[:-1],arc[1:])],wires=dial_wires,follow_swing=True))
                hit = twist_degrees >= twist_max-.25 or twist_degrees <= twist_min+.25
                pointer = Rotation.from_rotvec(axis*twist).apply(u)
                shapes.append(dict(kind='limit_hit' if hit else 'pointer',joint=names[joint],faces=[],
                                   wires=[np.array([center,center+length*.7*pointer])],follow_swing=True))
                readout += f"; twist {twist_degrees:.1f}° / {twist_range}"
        labels[names[joint]] = f'{names[joint]}: {text}'
        labels[names[joint]] += '\n'+readout.lstrip('; ')
        if any(s['joint']==names[joint] and s['kind']=='limit_hit' for s in shapes):
            labels[names[joint]] += ' — AT LIMIT'
        shapes.append(dict(kind='limit',joint=names[joint],faces=faces,wires=wires))
    if matrices is not None:
        # Bend ranges follow the parent. Twist dials additionally follow swing
        # so their planes stay perpendicular to the posed bone; only their
        # pointers include axial twist, preserving a readable angular range.
        for shape in shapes:
            if shape['kind'] == 'collision':
                continue
            joint = names.index(shape['joint'])
            rotation = matrices[parents[joint],:3,:3]
            if shape.get('follow_swing'):
                axis = offsets[children[joint]] / np.linalg.norm(offsets[children[joint]])
                local = rotation.T @ matrices[joint,:3,:3]
                swing,_ = rotation_parts(Rotation.from_matrix(local).as_quat(),axis)
                rotation = rotation @ swing.as_matrix()
            for key in ('faces','wires'):
                shape[key] = [(rows-points[joint]) @ rotation.T + points[joint] for rows in shape[key]]
    return points, dict(shapes=shapes,labels=labels,offsets=offsets.copy(),
                        joint_names=tuple(names),parents=tuple(parents),end_sites=end_sites,volumes=volumes,
                        joint_limits={j:dict(limit) for j,limit in limits.items()})


def create_rig_asset(profile, archive, output_dir, owner, *, collisions=True, limits=True, joint_limits=None):
    from .rig_preview_settings import load_preview_limits, load_preview_volumes, load_preview_hand_limits
    from .hands import add_preview_hands, export_preview_bvh
    from .hand_rig import full_rig_layout, expanded_limits
    from echograph.rigging.fbx_canonical import QuatKeyframe, JointAnimationTrack
    joint_limits = load_preview_limits() if joint_limits is None else joint_limits
    offsets = preview_offsets(profile,archive)
    points, guide = build_guide(offsets,joint_limits=joint_limits,volumes=load_preview_volumes())
    positions = np.repeat(points[None],2,axis=0)
    skeleton, clip, rotations, _ = reconstruct_animation(positions,20,{},rest_offsets=offsets)
    skeleton = add_preview_hands(skeleton)
    indices,names,parents,offsets = full_rig_layout(skeleton)
    joint_limits = expanded_limits(names,joint_limits,load_preview_hand_limits())
    _,guide = build_guide(offsets,joint_limits=joint_limits,volumes=load_preview_volumes(),names=names,parents=parents)
    guide['joint_indices'] = indices
    tracks = {t.joint_name:t for t in clip.tracks}
    # Nonzero saved minima also constrain the initial preview pose.
    for joint in joint_limits:
        child = primary_child(joint,names,parents)
        axis = offsets[child]/np.linalg.norm(offsets[child])
        quat = clamp_preview_rotation(joint,[0.,0.,0.,1.],axis,limits=joint_limits)
        name = names[joint]
        if name not in tracks:
            tracks[name] = JointAnimationTrack(name)
            clip.tracks.append(tracks[name])
        tracks[name].rotation_keys = [QuatKeyframe(f/20,tuple(quat)) for f in range(2)]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    path = output_dir/'rig.bvh'
    export_preview_bvh(path,skeleton,clip)
    guide.update(profile=profile,collisions=collisions,limits=limits,
                 saved_volumes={name:list(r) for name,r in guide['volumes'].items()},
                 saved_joint_limits={j:dict(limit) for j,limit in joint_limits.items()})
    context = dict(skeleton=skeleton,clip=clip,meshes=[],loop=False,mesh_skinning_enabled=False,
                   show_capture_joints=False,show_animated_joints=True,source_format='bvh',priormdm_rig_debug=guide)
    return dict(path=str(path.resolve()),node=owner,kind='mocap',ext='.bvh',visible=True,fbx_rig_context=context)
