import unittest
import json
import tempfile
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from echograph.motion.conversion import REST_DIRECTIONS, JOINT_NAMES, save_generated_motion
from echograph.motion.self_collision import (ARMS, PARENTS, arm_penetrations, body_capsules,
                                           correct_arm_collisions, segment_distances, world_pose)


class ArmCollisionTests(unittest.TestCase):
    def fixture(self, frames=12):
        offsets = REST_DIRECTIONS * .25
        rotations = np.tile(np.eye(3), (frames, 22, 1, 1))
        roots = np.zeros((frames, 3))
        return rotations, offsets, roots

    def test_continuous_segment_distance_detects_crossing_with_clear_endpoints(self):
        distance = segment_distances(np.array([-1.,0,0]), np.array([1.,0,0]),
                                     np.array([[0.,-1,0],[0.,-1,2]]), np.array([[0.,1,0],[0.,1,2]]))
        np.testing.assert_allclose(distance, [0, 2])
        # Parallel and zero-length segments must remain finite.
        np.testing.assert_allclose(segment_distances(np.zeros(3), np.zeros(3),
                                                     np.array([[0.,1,0]]),np.array([[0.,1,0]])), [1])

    def test_clear_motion_is_unchanged(self):
        rotations, offsets, roots = self.fixture()
        result, changed, report = correct_arm_collisions(rotations, offsets, roots, 20)
        np.testing.assert_array_equal(result, rotations)
        self.assertFalse(changed.any())
        self.assertEqual(report['corrected_frames'], 0)

    def test_forearm_and_hand_escape_torso_without_stretching_or_moving_body(self):
        rotations, offsets, roots = self.fixture()
        rotations[:,16] = Rotation.from_euler('z',180,degrees=True).as_matrix()
        original = rotations.copy()
        result, changed, report = correct_arm_collisions(rotations, offsets, roots, 20)
        np.testing.assert_array_equal(rotations, original)
        self.assertTrue(changed[:,0].any())
        self.assertGreater(report['max_proxy_overlap_before'], .04)
        self.assertLess(report['max_proxy_overlap_after'], .001)
        unaffected = [i for i in range(22) if i not in (16,17,18,19)]
        np.testing.assert_array_equal(result[:,unaffected], original[:,unaffected])
        # Collision avoidance must retain the anatomical elbow hinge limits.
        for elbow, sign in ((18, -1), (19, 1)):
            bends = Rotation.from_matrix(result[:,elbow]).as_rotvec()
            np.testing.assert_allclose(bends[:,[0,2]], 0, atol=1e-8)
            self.assertTrue(np.all(sign*bends[:,1] >= -1e-8))
            self.assertTrue(np.all(sign*bends[:,1] <= np.deg2rad(155)+1e-8))
        for frame in range(len(result)):
            points, _ = world_pose(result[frame], offsets, roots[frame])
            for joint, parent in enumerate(PARENTS):
                if parent >= 0:
                    self.assertAlmostEqual(np.linalg.norm(points[joint]-points[parent]), np.linalg.norm(offsets[joint]))
        shoulder = Rotation.from_matrix(result[:,16])
        self.assertLess(np.max((shoulder[:-1].inv()*shoulder[1:]).magnitude()), .1)

    def test_leg_collisions_are_detected_along_the_whole_forearm(self):
        rotations, offsets, roots = self.fixture(1)
        points, _ = world_pose(rotations[0], offsets, roots[0])
        # A forearm crosses a thigh even though neither endpoint is inside it.
        points[18] = points[1] + [-.15,-.10,0]
        points[20] = points[1] + [.15,-.10,0]
        penetration = arm_penetrations(points, body_capsules(points, 1, ARMS[0]), ARMS[0], 1)
        self.assertGreater(penetration[11+4], .1)

    def test_cleanup_is_equivariant_under_world_rotation_and_scale(self):
        rotations, offsets, roots = self.fixture(3)
        rotations[:,16] = Rotation.from_euler('z',180,degrees=True).as_matrix()
        expected, _, _ = correct_arm_collisions(rotations, offsets, roots, 20)
        spin = Rotation.from_euler('xyz',[140,70,-50],degrees=True).as_matrix()
        rotated = rotations.copy()
        rotated[:,0] = spin
        actual, _, _ = correct_arm_collisions(rotated, offsets*100, roots+50, 20)
        np.testing.assert_allclose(actual[:,1:], expected[:,1:], atol=1e-5)
        np.testing.assert_array_equal(actual[:,0], rotated[:,0])

    def test_saved_bvh_keeps_collision_correction_and_original_archive(self):
        from echograph.rigging.bvh_ingest import ingest_bvh_animation_data
        from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
        rotations, offsets, roots = self.fixture(3)
        rotations[:,16] = Rotation.from_euler('z',180,degrees=True).as_matrix()
        positions = np.array([world_pose(r,offsets,p)[0] for r,p in zip(rotations,roots)])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            archive = path/'motion.npz'
            np.savez(archive,positions=positions,fps=20,metadata=json.dumps({}))
            original = archive.read_bytes()
            result = save_generated_motion(path)
            self.assertEqual(archive.read_bytes(), original)
            self.assertGreater(result.metadata['self_collision_cleanup']['corrected_frames'],0)
            loaded = ingest_bvh_animation_data(result.bvh_path)
            lookup = {j.name:i for i,j in enumerate(loaded.skeleton.joints)}
            for frame in range(3):
                matrices = np.array(evaluate_rig_at_time(loaded.skeleton,loaded.clips[0],frame/20).global_matrices).reshape(-1,4,4)
                points = matrices[[lookup[name] for name in JOINT_NAMES],:3,3]
                for arm in ARMS:
                    error = arm_penetrations(points, body_capsules(points,.25/.44,arm),arm,.25/.44)
                    self.assertLess(max(error), .001)


if __name__ == '__main__':
    unittest.main()
