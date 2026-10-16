"""Map FBX body names to preview roles without modifying the source skeleton."""
import re
import numpy as np

from .conversion import JOINT_NAMES, PARENTS
from .hands import DIGITS
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time


def _token(name):
    # FBX namespaces and scene paths are not anatomical identifiers.
    return re.sub(r'[^a-z0-9]', '', str(name).split('|')[-1].split(':')[-1].lower())


def preview_joint_indices(skeleton):
    lookup = {}
    for index, joint in enumerate(skeleton.joints):
        lookup.setdefault(_token(joint.name), []).append(index)

    def find(role, aliases):
        for alias in aliases:
            matches = lookup.get(_token(alias), [])
            if len(matches) > 1:
                raise ValueError(f'Collider preview cannot uniquely map {role}: '
                                 + ', '.join(skeleton.joints[i].name for i in matches))
            if matches:
                return matches[0]
        return None

    # Mixamo's Spine/Spine1/Spine2 correspond to the three internal spine roles.
    mixamo_spine = 'spine' in lookup and 'spine3' not in lookup and 'spine03' not in lookup
    aliases = {
        'pelvis': ('pelvis', 'hips', 'hip'),
        'spine1': ('spine',) if mixamo_spine else ('spine1', 'spine_01'),
        'spine2': ('spine1',) if mixamo_spine else ('spine2', 'spine_02'),
        'spine3': ('spine2',) if mixamo_spine else ('spine3', 'spine_03', 'chest'),
        'neck': ('neck', 'neck_01'), 'head': ('head',),
    }
    limbs = {
        'hip': ('thigh', 'upleg', 'upperleg'),
        'knee': ('calf', 'leg', 'lowerleg', 'shin'),
        'ankle': ('foot',), 'foot': ('ball', 'toebase', 'toe'),
        'collar': ('clavicle', 'shoulder'),
        'shoulder': ('upperarm', 'arm'),
        'elbow': ('lowerarm', 'forearm'), 'wrist': ('hand',),
    }
    for side, short in (('left', 'l'), ('right', 'r')):
        # Exact canonical names take precedence only for canonical-style limbs;
        # LeftShoulder in Mixamo means collar, and LeftFoot means ankle.
        canonical = any(_token(side + '_' + role) in lookup
                        for role in ('hip', 'knee', 'collar', 'elbow', 'wrist'))
        for role, terms in limbs.items():
            candidates = [side + '_' + role] if canonical else []
            for term in terms:
                candidates.extend((side + term, term + '_' + short, short + '_' + term))
            aliases[side + '_' + role] = candidates
    indices = [find(role, aliases[role]) for role in JOINT_NAMES]
    missing = [role for role, index in zip(JOINT_NAMES, indices) if index is None]
    if missing:
        raise ValueError('Collider preview could not map these body roles: '
                         + ', '.join(missing) + '. The skeleton is not invalid; '
                         'its joint naming needs a preview mapping.')
    if len(set(indices)) != len(indices):
        raise ValueError('Collider preview found overlapping body mappings; joint names are ambiguous.')
    names, parents = list(JOINT_NAMES), list(PARENTS)
    for side, short in (('left', 'l'), ('right', 'r')):
        for digit in DIGITS:
            finger = []
            for segment in (1, 2, 3):
                role = f'{side}_{digit}{segment}'
                terms = (digit, 'little') if digit == 'pinky' else (digit,)
                candidates = [role]
                for term in terms:
                    candidates.extend((f'{side}Hand{term}{segment}',
                                       f'{term}_{segment:02d}_{short}',
                                       f'{term}{segment}_{short}'))
                finger.append(find(role, candidates))
            # Fingers are optional; only create capsules for a complete chain.
            if any(index is None for index in finger):
                continue
            for segment, index in enumerate(finger, 1):
                parents.append(names.index(side + '_wrist') if segment == 1 else len(names)-1)
                names.append(f'{side}_{digit}{segment}')
                indices.append(index)
    return indices, tuple(names), tuple(parents)


def mapped_preview_layout(skeleton):
    indices, names, parents = preview_joint_indices(skeleton)
    evaluation = evaluate_rig_at_time(skeleton, None, 0.)
    matrices = np.asarray(evaluation.global_matrices).reshape(-1, 4, 4)[indices]
    offsets = np.zeros((len(indices), 3))
    for joint, parent in enumerate(parents):
        if parent < 0:
            offsets[joint] = matrices[joint, :3, 3]
        else:
            # Collapse intervening root/twist/helper joints in bind space. The
            # source matrices still drive every preview joint during playback.
            offsets[joint] = np.linalg.solve(matrices[parent], matrices[joint])[:3, 3]
    end_sites = skeleton.metadata.get('end_sites', {})
    mapped_ends = {name: end_sites[skeleton.joints[index].name]
                   for name, index in zip(names, indices)
                   if skeleton.joints[index].name in end_sites}
    return indices, names, parents, offsets, matrices, mapped_ends
