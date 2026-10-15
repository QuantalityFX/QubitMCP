"""Regressions for thin body parts and contact between cloth vertices."""
from dataclasses import replace
from itertools import product
import unittest

import numpy as np

from echograph.physics.cloth import ClothSettings, ClothSolver
from echograph.physics.cloth.asset import ClothAsset
from echograph.physics.cloth.collisions import MeshCollider, closest_points


def box(lower, upper):
    vertices = np.array(list(product(*zip(lower, upper))), dtype=float)
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
             (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    faces = np.array([(a, b, c) for a, b, c, d in quads] +
                     [(a, c, d) for a, b, c, d in quads], dtype=np.int32)
    return MeshCollider(vertices, faces)


def cloth(vertices, faces):
    vertices, faces = np.asarray(vertices, dtype=float), np.asarray(faces, dtype=np.int32)
    edges = np.unique(np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]],
                                             faces[:, [2, 0]]]), axis=1), axis=0)
    return ClothAsset(vertices, faces, edges, np.empty((0, 4), dtype=np.int32),
                      np.empty((0, 4)), np.empty((0, 4), dtype=np.int32),
                      np.empty((0, 4)), np.empty(0, dtype=np.int32),
                      tuple("panel" for _ in vertices), "contact-test")


def config(**kwargs):
    return replace(ClothSettings(device="cpu", gravity_y=0, damping=0, stretch=0,
                                bend=0, self_collision=False, substeps=1,
                                iterations=1, collision_quality=2), **kwargs)


class BodyContactTests(unittest.TestCase):
    def test_exterior_point_is_not_captured_by_far_side_of_thin_finger(self):
        collider = box([-.04, -.004, -.04], [.04, .004, .04])
        old = np.array([[0., .0071, 0.]])
        current = old.copy()
        for _ in range(4):
            collider.project(current, old, np.ones(1), .003, .6)
            np.testing.assert_allclose(current, old, atol=1e-12)

    def test_inward_crossing_keeps_entry_side_even_when_opposite_face_is_nearer(self):
        collider = box([-.04, -.004, -.04], [.04, .004, .04])
        old, current = np.array([[0., .008, 0.]]), np.array([[0., -.003, 0.]])
        for _ in range(4):
            collider.project(current, old, np.ones(1), .003, 0)
            np.testing.assert_allclose(current, [[0, .007, 0]], atol=1e-12)

    def test_overlap_repair_uses_nearest_exit_without_friction_or_launch_velocity(self):
        collider = box([-.04, -.004, -.04], [.04, .004, .04])
        collider.previous = collider.vertices - [.02, 0, 0]
        asset = cloth([[-.005, .003, -.005], [.005, .003, -.005], [0, .003, .005]], [[0, 1, 2]])
        solver = ClothSolver(asset, config())
        solver.step(colliders=[collider])
        np.testing.assert_allclose(solver.x[:, 1], .007, atol=1e-12)
        np.testing.assert_allclose(solver.x[:, [0, 2]], asset.positions[:, [0, 2]], atol=1e-12)
        np.testing.assert_allclose(solver.velocity, 0, atol=1e-12)
        for _ in range(4):
            solver.step(colliders=[MeshCollider(collider.vertices, collider.triangles)])
        np.testing.assert_allclose(solver.x[:, 1], .007, atol=1e-12)

    def test_hand_leg_gap_does_not_attract_cloth_to_departing_hand(self):
        leg = box([-.1, -.1, -.1], [.1, 0, .1])
        finger = box([-.04, .009, -.04], [.04, .017, .04])
        old = np.array([[0., .004, 0.]])
        current = old.copy()
        for frame in range(12):
            offset = np.array([frame * .01, 0, 0])
            collider = MeshCollider(np.r_[leg.vertices, finger.vertices + offset],
                                    np.r_[leg.triangles, finger.triangles + len(leg.vertices)],
                                    np.r_[leg.vertices, finger.vertices + offset - [.01, 0, 0]])
            collider.project(current, old, np.ones(1), .003, .6)
            np.testing.assert_allclose(current, old, atol=1e-12)


class SurfaceContactTests(unittest.TestCase):
    device = "cpu"

    def solver(self, asset, pins=(), **kwargs):
        kwargs.setdefault("friction", 0)
        solver = ClothSolver(asset, config(device=self.device, **kwargs), pins=pins)
        if self.device == "cuda":
            self.assertTrue(solver.backend.startswith("Warp CUDA"), solver.backend_detail)
        return solver

    def step(self, solver, collider):
        solver.step(colliders=[collider])
        if self.device == "cuda":
            self.assertTrue(solver.backend.startswith("Warp CUDA"), solver.backend_detail)

    def test_long_cloth_edge_stops_on_box_with_all_particles_outside(self):
        collider = box([-.05, -.05, -.05], [.05, .05, .05])
        asset = cloth([[-.1, .07, 0], [.1, .07, 0], [.2, .07, -.02]], [[0, 1, 2]])
        solver = self.solver(asset, collision_quality=8)
        solver.velocity[:, 1] = -.04 * solver.settings.fps
        self.step(solver, collider)
        # A narrow triangle excludes every cube corner. Only edge/edge contact
        # can hold its middle up; no cloth particle lies over the cube.
        midpoint = (solver.x[0] + solver.x[1]) * .5
        self.assertGreaterEqual(midpoint[1], .053 - 1e-5)
        self.assertTrue(np.isfinite(solver.x).all())

    def test_moving_small_box_catches_face_interior_with_distant_edges(self):
        asset = cloth([[-.2, 0, -.2], [.2, 0, -.2], [0, 0, .3]], [[0, 1, 2]])
        collider = box([.017, -.03, .011], [.037, -.01, .031])
        collider = MeshCollider(collider.vertices + [0, .04, 0], collider.triangles, collider.vertices)
        solver = self.solver(asset, collision_quality=8)
        self.step(solver, collider)
        top = collider.vertices[collider.vertices[:, 1] > .02]
        surface = np.repeat(solver.x[asset.triangles], len(top), axis=0)
        nearest, _, inside = closest_points(top, surface)
        self.assertTrue(inside.all())
        self.assertTrue(np.all(nearest[:, 1] > top[:, 1]))
        self.assertGreaterEqual(np.linalg.norm(top - nearest, axis=1).min(), .003 - 2e-5)

    def test_face_contact_distributes_to_free_particles_and_preserves_pins(self):
        asset = cloth([[-.2, .02, -.2], [.2, .02, -.2], [0, .02, .3]], [[0, 1, 2]])
        collider = box([-.01, -.04, -.01], [.01, 0, .01])
        solver = self.solver(asset, pins=[0], collision_quality=8)
        solver.velocity[1:, 1] = -.04 * solver.settings.fps
        self.step(solver, collider)
        np.testing.assert_allclose(solver.x[0], asset.positions[0], atol=1e-8)
        self.assertTrue(np.isfinite(solver.x).all())
        top = collider.vertices[collider.vertices[:, 1] == 0]
        nearest, _, _ = closest_points(top, np.repeat(solver.x[asset.triangles], len(top), axis=0))
        self.assertGreaterEqual(nearest[:, 1].min(), .003 - 2e-5)
        self.assertLess(np.max(np.linalg.norm(solver.x - asset.positions, axis=1)), .1)

    def test_thin_surface_corner_catches_face_from_either_side(self):
        collider = MeshCollider([[-.01, 0, -.01], [.01, 0, -.01], [0, 0, .01]], [[0, 1, 2]], two_sided=True)
        for sign in (-1, 1):
            with self.subTest(sign=sign):
                asset = cloth([[-.2, .02 * sign, -.2], [.2, .02 * sign, -.2], [0, .02 * sign, .3]], [[0, 1, 2]])
                solver = self.solver(asset, collision_quality=8)
                solver.velocity[:, 1] = -.04 * sign * solver.settings.fps
                self.step(solver, collider)
                nearest, _, _ = closest_points(collider.vertices, np.repeat(solver.x[asset.triangles], 3, axis=0))
                self.assertGreaterEqual(np.min(nearest[:, 1] * sign), .003 - 2e-5)

    def test_contact_near_pin_does_not_amplify_into_mesh_spike(self):
        asset = cloth([[0, 0, 0], [1, 0, 0], [0, 0, 1]], [[0, 1, 2]])
        collider = box([.0005, -.01, .0005], [.0015, -.005, .0015])
        collider = MeshCollider(collider.vertices + [0, .015, 0], collider.triangles, collider.vertices)
        solver = self.solver(asset, pins=[0])
        self.step(solver, collider)
        np.testing.assert_allclose(solver.x[0], asset.positions[0], atol=1e-8)
        # The obstacle moved 15 mm. An almost pinned contact previously moved
        # a free corner more than ten metres to satisfy its tiny contact weight.
        self.assertLess(np.linalg.norm(solver.x - asset.positions, axis=1).max(), .1)
        self.assertGreater(solver.x[:, 1].max(), 0)

    def test_repaired_finger_overlap_does_not_gain_velocity(self):
        collider = box([-.04, -.004, -.04], [.04, .004, .04])
        asset = cloth([[-.005, .003, -.005], [.005, .003, -.005], [0, .003, .005]], [[0, 1, 2]])
        solver = self.solver(asset)
        self.step(solver, collider)
        np.testing.assert_allclose(solver.x[:, 1], .007, atol=1e-7)
        np.testing.assert_allclose(solver.velocity, 0, atol=1e-6)

    def test_finger_crossing_does_not_switch_to_opposite_surface(self):
        collider = box([-.04, -.004, -.04], [.04, .004, .04])
        asset = cloth([[-.005, .008, -.005], [.005, .008, -.005], [0, .008, .005]], [[0, 1, 2]])
        solver = self.solver(asset, collision_quality=4)
        solver.velocity[:, 1] = -.011 * solver.settings.fps
        self.step(solver, collider)
        self.assertGreaterEqual(solver.x[:, 1].min(), .007 - 1e-7)

    def test_departing_hand_does_not_pull_pants_away_from_leg_at_24_fps(self):
        asset = cloth([[-.02, .004, -.02], [.02, .004, -.02], [0, .004, .02]], [[0, 1, 2]])
        leg = box([-.1, -.1, -.1], [.1, 0, .1])
        finger = box([-.04, .007, -.04], [.04, .015, .04])
        solver = self.solver(asset, substeps=4, iterations=8, stretch=10000, friction=.6, gravity_y=-9.81)
        previous = np.r_[leg.vertices, finger.vertices]
        for frame in range(12):
            current = np.r_[leg.vertices, finger.vertices + [frame * .006, frame * .002, 0]]
            collider = MeshCollider(current, np.r_[leg.triangles, finger.triangles + 8], previous)
            self.step(solver, collider)
            previous = current
            self.assertGreaterEqual(solver.x[:, 1].min(), .003 - 2e-6)
            self.assertLess(solver.x[:, 1].max(), .0041)
        # A small settling shift is allowed; the hand travels 66 mm sideways.
        np.testing.assert_allclose(solver.x[:, [0, 2]], asset.positions[:, [0, 2]], atol=1e-4)
        lengths = np.linalg.norm(solver.x[asset.edges[:, 0]] - solver.x[asset.edges[:, 1]], axis=1)
        rest = np.linalg.norm(asset.positions[asset.edges[:, 0]] - asset.positions[asset.edges[:, 1]], axis=1)
        self.assertLess(np.max(lengths / rest), 1.01)


class CudaSurfaceContactTests(SurfaceContactTests):
    device = "cuda"

    @classmethod
    def setUpClass(cls):
        try:
            import warp as wp
        except ImportError:
            raise unittest.SkipTest("warp-lang is not installed")
        wp.init()
        if not wp.is_cuda_available():
            raise unittest.SkipTest("Warp CUDA device unavailable")

    def test_feature_contact_positions_and_velocity_match_cpu(self):
        cases = [
            (cloth([[-.1, .07, 0], [.1, .07, 0], [.2, .07, -.02]], [[0, 1, 2]]),
             box([-.05, -.05, -.05], [.05, .05, .05]), -.04 * 24),
            (cloth([[-.2, .02, -.2], [.2, .02, -.2], [0, .02, .3]], [[0, 1, 2]]),
             box([.017, -.02, .011], [.037, 0, .031]), -.04 * 24),
        ]
        for asset, collider, velocity in cases:
            cpu = ClothSolver(asset, config(friction=0, collision_quality=8))
            gpu = self.solver(asset, collision_quality=8)
            for solver in (cpu, gpu):
                solver.velocity[:, 1] = velocity
                if solver is gpu:
                    self.step(solver, collider)
                else:
                    solver.step(colliders=[collider])
            np.testing.assert_allclose(gpu.x, cpu.x, atol=2e-5, rtol=1e-3)
            np.testing.assert_allclose(gpu.velocity, cpu.velocity, atol=5e-4, rtol=1e-3)


if __name__ == "__main__":
    unittest.main()
