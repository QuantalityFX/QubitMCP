"""Regression coverage for skin-twisting drift in position-only motion exports."""
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

from echograph.motion.conversion import PARENTS, REST_DIRECTIONS, reconstruct_animation


def bent_pose():
    offsets = REST_DIRECTIONS * 0.2
    local = np.tile(np.eye(3), (22, 1, 1))
    for joint, axis, angle in [(1, 'x', -20), (2, 'x', -10), (4, 'x', 60), (5, 'x', 40),
                               (16, 'z', -45), (17, 'z', 45), (18, 'y', -60), (19, 'y', 45),
                               (12, 'x', 20), (7, 'x', -20), (8, 'x', -20)]:
        local[joint] = Rotation.from_euler(axis, angle, degrees=True).as_matrix()
    world = local.copy()
    positions = np.zeros((22, 3))
    for joint, parent in enumerate(PARENTS):
        if parent >= 0:
            world[joint] = world[parent] @ local[joint]
            positions[joint] = positions[parent] + world[parent] @ offsets[joint]
    return positions


class StableRotationTests(unittest.TestCase):
    def test_spine_reversals_and_chest_flips_are_bounded_without_snapping(self):
        positions = []
        offsets = REST_DIRECTIONS * .2
        for frame in range(30):
            local = np.tile(np.eye(3), (22, 1, 1))
            for joint in (3, 6, 9):
                local[joint] = Rotation.from_euler('xyz',
                    [170*(-1)**frame, 175*(-1)**frame, 40], degrees=True).as_matrix()
            world = local.copy()
            pose = np.zeros((22, 3))
            for joint, parent in enumerate(PARENTS):
                if parent >= 0:
                    pose[joint] = pose[parent] + world[parent] @ offsets[joint]
                    world[joint] = world[parent] @ local[joint]
            positions.append(pose)
        _, _, result, _ = reconstruct_animation(np.array(positions), 20, {})
        for joint, bend_limit, twist_limit, speed in ((3,35,0,180), (6,35,0,180), (9,45,40,240)):
            rotations = Rotation.from_matrix(result[:,joint])
            q = rotations.as_quat()
            twists = Rotation.from_quat(np.column_stack((np.zeros(len(q)), q[:,1], np.zeros(len(q)), q[:,3])))
            swings = rotations * twists.inv()
            self.assertLessEqual(np.max(np.degrees(swings.magnitude())), bend_limit+1e-6)
            self.assertLessEqual(np.max(np.degrees(twists.magnitude())), twist_limit+1e-6)
            self.assertLessEqual(np.max(np.degrees((rotations[:-1].inv()*rotations[1:]).magnitude())), speed/20+1e-6)

    def test_measured_chest_twist_is_preserved_inside_limits(self):
        pose = bent_pose()
        chest = 9
        # Rotate all descendants together; collar landmarks measure torso twist.
        descendants = []
        for joint in range(chest+1, 22):
            parent = PARENTS[joint]
            if parent == chest or parent in descendants:
                descendants.append(joint)
        pose[descendants] = (Rotation.from_euler('y', 30, degrees=True).apply(
            pose[descendants]-pose[chest]) + pose[chest])
        _, _, local, _ = reconstruct_animation(np.repeat(pose[None], 3, axis=0), 20, {})
        np.testing.assert_allclose(local[:,chest], np.tile(Rotation.from_euler('y',30,degrees=True).as_matrix(), (3,1,1)), atol=1e-8)

    def test_full_body_flips_and_spins_do_not_accumulate_local_twist(self):
        pose = bent_pose()
        time = np.linspace(0, 1, 121)
        orientations = Rotation.from_euler('xyz', np.column_stack((720*time, 40*np.sin(4*np.pi*time),
                                                                  70*np.sin(2*np.pi*time))), degrees=True)
        positions = np.array([r.apply(pose) + [0, 1 + t, t] for r, t in zip(orientations, time)])
        _, clip, local, _ = reconstruct_animation(positions, 60, {})
        np.testing.assert_allclose(local[:, 1:], np.broadcast_to(local[0, 1:], local[:, 1:].shape), atol=1e-6)
        np.testing.assert_allclose(local[:, 0], orientations.as_matrix(), atol=1e-6)
        for track in clip.tracks:
            keys = np.array([k.value for k in track.rotation_keys])
            self.assertTrue(np.all(np.sum(keys[1:] * keys[:-1], axis=1) >= -1e-10))

    def test_knees_and_elbows_use_hinges_and_wrists_follow_forearms(self):
        positions = np.repeat(bent_pose()[None], 10, axis=0)
        _, _, local, _ = reconstruct_animation(positions, 20, {})
        for joint, hinge in [(4, 0), (5, 0), (18, 1), (19, 1)]:
            vectors = Rotation.from_matrix(local[:, joint]).as_rotvec()
            np.testing.assert_allclose(np.delete(vectors, hinge, axis=1), 0, atol=1e-8)
            self.assertGreater(np.max(abs(vectors[:, hinge])), 0.3)
        for joint in (20, 21):
            np.testing.assert_allclose(local[:, joint], np.broadcast_to(np.eye(3), (10, 3, 3)), atol=1e-8)

    def test_bad_head_and_foot_directions_are_limited_without_restricting_root(self):
        pose = bent_pose()
        positions = np.repeat(pose[None], 12, axis=0)
        for frame in range(1, 12):
            positions[frame, 15] = positions[frame, 12] + [0.02*(-1)**frame, -0.2, 0]
            for ankle, toe in ((7, 10), (8, 11)):
                positions[frame, toe] = positions[frame, ankle] + [0.02*(-1)**frame, 0, -0.2]
        _, _, local, _ = reconstruct_animation(positions, 20, {})
        for joint, limit, step in ((12, 70, 18), (7, 85, 27), (8, 85, 27)):
            r = Rotation.from_matrix(local[:, joint])
            self.assertLessEqual(np.max(np.degrees(r.magnitude())), limit + 1e-6)
            self.assertLessEqual(np.max(np.degrees((r[:-1].inv() * r[1:]).magnitude())), step + 1e-6)
        np.testing.assert_allclose(np.linalg.det(local), 1, atol=1e-8)

    def test_nearly_straight_limb_does_not_flip_on_noisy_bend_plane(self):
        pose = np.zeros((22, 3))
        for joint in range(1, 22):
            pose[joint] = pose[PARENTS[joint]] + REST_DIRECTIONS[joint]*0.2
        positions = np.repeat(pose[None], 30, axis=0)
        for frame in range(30):
            positions[frame, 7, 2] += (-1)**frame * 1e-5
            positions[frame, 10, 2] += (-1)**frame * 1e-5
        _, _, local, _ = reconstruct_animation(positions, 20, {})
        thigh = Rotation.from_matrix(local[:, 1])
        self.assertLess(np.max(np.degrees((thigh[:-1].inv()*thigh[1:]).magnitude())), 0.1)

    def test_direction_reversal_does_not_leave_axial_twist_in_limited_joints(self):
        from echograph.motion.rotation_reconstruction import parent_swing, stabilize_joint
        for joint, axis in ((4, 1), (18, 0), (7, 2), (12, 1)):
            source = np.eye(3)[axis]
            previous = np.eye(3)
            for angle in np.linspace(150, 210, 40):
                direction = Rotation.from_euler('xyz', [angle, angle/7, angle/3], degrees=True).apply(source)
                local = parent_swing(source, direction, previous)
                previous = stabilize_joint(joint, source, direction, local, previous, 20)
                self.assertAlmostEqual(Rotation.from_matrix(previous).as_quat()[axis], 0, places=7)

    def test_inconsistent_limb_directions_cannot_create_sideways_or_backward_hinges(self):
        from echograph.motion.rotation_reconstruction import parent_swing, stabilize_joint
        for joint, source, hinge in ((4, [0., -1., 0.], [1, 0, 0]), (5, [0., -1., 0.], [1, 0, 0]),
                                    (18, [1., 0., 0.], [0, -1, 0]), (19, [-1., 0., 0.], [0, 1, 0])):
            source, hinge = np.array(source), np.array(hinge)
            previous = np.eye(3)
            for direction in ([0., 0., 1.], [0., 0., -1.], [0., 1., 0.], [1., 0., 0.]):
                target = np.array(direction)
                local = stabilize_joint(joint, source, target, parent_swing(source, target), previous, 20)
                vector = Rotation.from_matrix(local).as_rotvec()
                self.assertGreaterEqual(float(vector @ hinge), -1e-8)
                np.testing.assert_allclose(vector - hinge*(vector @ hinge), 0, atol=1e-8)
                previous = local


if __name__ == '__main__':
    unittest.main()
