"""Device parity check; run again after setup.bat installs the main requirements."""
from dataclasses import replace
import unittest
import numpy as np

from echograph.patterns.commands import rectangle
from echograph.patterns.document import PatternDocument
from echograph.patterns.sewing import add_sew_pair
from echograph.physics.cloth import ClothSettings, ClothSolver, build_cloth_asset
from echograph.physics.cloth.collisions import MeshCollider

try:
    import warp as wp
except ImportError:
    wp = None


@unittest.skipIf(wp is None, "warp-lang binary runtime is not installed; run setup.bat core")
class WarpParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        wp.init()
        if not wp.is_cuda_available():
            raise unittest.SkipTest("Warp CUDA device unavailable")

    def solvers(self, settings, world_positions=None, asset=None, pins=()):
        asset = asset or build_cloth_asset(PatternDocument(pieces=[rectangle("Panel", 100, 100)]), 25)
        cpu = ClothSolver(asset, replace(settings, device="cpu"), world_positions, pins)
        gpu = ClothSolver(asset, replace(settings, device="cuda"), world_positions, pins)
        self.assertTrue(gpu.backend.startswith("Warp CUDA"), gpu.backend_detail)
        return cpu, gpu

    def assert_cuda_matches(self, cpu, gpu, atol=1e-5):
        self.assertTrue(gpu.backend.startswith("Warp CUDA"), gpu.backend_detail)
        np.testing.assert_allclose(gpu.x, cpu.x, atol=atol, rtol=1e-3)
        np.testing.assert_allclose(gpu.velocity, cpu.velocity, atol=atol * 50, rtol=1e-3)
        self.assertGreater(gpu.last_gpu_ms, 0)

    def test_cuda_constraints_match_cpu_for_bend_and_unequal_sewing(self):
        wp.init()
        if not wp.is_cuda_available():
            self.skipTest("Warp CUDA device unavailable")
        a, b = rectangle("Front", 100, 90), rectangle("Back", 100, 140)
        b.layout[0] = 150
        document = PatternDocument(pieces=[a, b])
        add_sew_pair(document, a.id, a.segments[1].id, b.id, b.segments[3].id)
        asset = build_cloth_asset(document, 25)
        settings = ClothSettings(device="cpu", self_collision=False, bend=2, substeps=2, iterations=5)
        cpu = ClothSolver(asset, settings, pins=[0])
        gpu = ClothSolver(asset, replace(settings, device="cuda"), pins=[0])
        self.assertTrue(gpu.backend.startswith("Warp CUDA"), gpu.backend_detail)
        perturbation = np.sin(asset.positions[:, 0] * 30) * .02
        cpu.x[:, 2] += perturbation
        gpu.x[:, 2] += perturbation
        for _ in range(3):
            cpu.step()
            gpu.step()
        self.assertTrue(gpu.backend.startswith("Warp CUDA"), gpu.backend_detail)
        np.testing.assert_allclose(gpu.x, cpu.x, atol=1e-4, rtol=1e-3)
        np.testing.assert_allclose(gpu.x[0], gpu.rest[0], atol=1e-8)

    def test_cuda_swept_mesh_collision_and_pins(self):
        settings = ClothSettings(self_collision=False, gravity_y=0, stretch=0, bend=0, damping=0,
                                 substeps=1, iterations=1, friction=.3)
        cpu, gpu = self.solvers(settings, pins=[0])
        collider = MeshCollider(np.array([[-1, -.05, -1], [0, -.05, 1], [1, -.05, -1]]), [[0, 1, 2]])
        for solver in (cpu, gpu):
            solver.velocity[:, 1] = -12
            solver.velocity[:, 0] = .2
            solver.step(colliders=[collider])
            np.testing.assert_array_equal(solver.x[0], solver.rest[0])
            self.assertGreaterEqual(solver.x[1:, 1].min(), -.05 + settings.thickness - 1e-6)
        self.assert_cuda_matches(cpu, gpu)

    def test_cuda_moving_collider_reuses_graph_and_updates_motion(self):
        settings = ClothSettings(self_collision=False, gravity_y=0, stretch=0, bend=0,
                                 substeps=3, iterations=4, friction=.4)
        cpu, gpu = self.solvers(settings)
        previous = np.array([[-1, -.1, -1], [0, -.1, 1], [1, -.1, -1]])
        for height in [.15, .2, .3]:
            current = previous.copy()
            current[:, 1] = height
            collider = MeshCollider(current, [[0, 1, 2]], previous)
            for solver in (cpu, gpu):
                solver.step(colliders=[collider])
            self.assert_cuda_matches(cpu, gpu)
            self.assertGreaterEqual(gpu.x[:, 1].min(), height + settings.thickness - 1e-5)
            if height == .15:
                graph = gpu._accelerator.graph
            else:
                self.assertIs(gpu._accelerator.graph, graph)
            previous = current

    def test_cuda_self_collision_preserves_seam_exclusions(self):
        a, b = rectangle("A", 100, 100), rectangle("B", 40, 40)
        b.layout = [30, 30, 0]
        add_sew_pair(document := PatternDocument(pieces=[a, b]), a.id, a.segments[0].id, b.id, b.segments[0].id)
        asset = build_cloth_asset(document, 25)
        settings = ClothSettings(gravity_y=0, stretch=0, bend=0, seam_stiffness=0,
                                 substeps=1, iterations=2, collision_quality=1)
        cpu, gpu = self.solvers(settings, asset=asset, pins=[0])
        back = np.asarray(asset.vertex_pieces) == b.id
        before = cpu.x.copy()
        for solver in (cpu, gpu):
            solver.x[back, 2] = .001
            solver.step()
        self.assert_cuda_matches(cpu, gpu)
        self.assertGreater(np.max(np.abs(gpu.x[:, 2] - before[:, 2])), .0001)

    def test_cuda_grab_ground_and_cache_edits_use_one_graph(self):
        settings = ClothSettings(self_collision=False, ground=True, substeps=2, iterations=4)
        cpu, gpu = self.solvers(settings, pins=[0])
        ids = cpu.asset.triangles[0]
        weights = np.array([.2, .3, .5])
        for solver in (cpu, gpu):
            solver.step()
        graph = gpu._accelerator.graph
        for offset in [.03, .08]:
            target = weights @ cpu.x[ids] + [0, 0, offset]
            for solver in (cpu, gpu):
                solver.set_grab(ids, weights, target)
                solver.step(dt=.025)
            self.assert_cuda_matches(cpu, gpu)
            self.assertIs(gpu._accelerator.graph, graph)
        for solver in (cpu, gpu):
            solver.release_grab()
            # Simulate cache playback changing the public state outside CUDA.
            solver.x[:] = solver.rest + [0, .2, 0]
            solver.velocity.fill(0)
            solver.settings = replace(solver.settings, interactive=True)
            solver.step()
        self.assert_cuda_matches(cpu, gpu)
        self.assertIs(gpu._accelerator.graph, graph)

    def test_cuda_collider_topology_changes_invalidate_graph(self):
        cpu, gpu = self.solvers(ClothSettings(self_collision=False))
        collider = MeshCollider(np.array([[-1, -.1, -1], [0, -.1, 1], [1, -.1, -1]]), [[0, 1, 2]])
        for solver in (cpu, gpu):
            solver.step(colliders=[collider])
        graph = gpu._accelerator.graph
        # Removing/adding colliders must not keep stale BVH pointers in a graph.
        for solver in (cpu, gpu):
            solver.step()
        self.assert_cuda_matches(cpu, gpu)
        self.assertIsNot(gpu._accelerator.graph, graph)
        graph = gpu._accelerator.graph
        collider = MeshCollider(np.array([[-1, -.1, -1], [0, -.1, 1], [1, -.1, -1], [2, -.1, 1]]), [[0, 1, 2], [2, 1, 3]])
        for solver in (cpu, gpu):
            solver.step(colliders=[collider])
        self.assert_cuda_matches(cpu, gpu)
        self.assertIsNot(gpu._accelerator.graph, graph)

    def test_cuda_recovered_points_are_not_pulled_back_inside_body(self):
        asset = build_cloth_asset(PatternDocument(pieces=[rectangle("Panel", 100, 100)]), 25)
        rest = asset.positions[:, [0, 2, 1]].copy()
        rest[:, 1] = -.001
        settings = ClothSettings(gravity_y=0, damping=0, stretch=0, bend=0, self_collision=False,
                                 substeps=1, iterations=1, collision_quality=4)
        cpu, gpu = self.solvers(settings, rest, asset)
        collider = MeshCollider(np.array([[-1, 0, -1], [0, 0, 1], [1, 0, -1]]), [[0, 1, 2]])
        for solver in (cpu, gpu):
            solver.step(colliders=[collider])
            self.assertGreaterEqual(solver.x[:, 1].min(), settings.thickness - 1e-6)
        self.assert_cuda_matches(cpu, gpu)

    def test_cuda_allows_outward_escape_from_body(self):
        asset = build_cloth_asset(PatternDocument(pieces=[rectangle("Panel", 100, 100)]), 25)
        rest = asset.positions[:, [0, 2, 1]].copy()
        rest[:, 1] = -.001
        settings = ClothSettings(gravity_y=0, damping=0, stretch=0, bend=0, self_collision=False,
                                 substeps=1, iterations=1, collision_quality=1)
        cpu, gpu = self.solvers(settings, rest, asset)
        collider = MeshCollider(np.array([[-1, 0, -1], [0, 0, 1], [1, 0, -1]]), [[0, 1, 2]])
        for solver in (cpu, gpu):
            solver.velocity[:, 1] = .011 * settings.fps
            solver.step(colliders=[collider])
            np.testing.assert_allclose(solver.x[:, 1], .01, atol=1e-6)
        self.assert_cuda_matches(cpu, gpu)

    def test_cuda_body_clearance_is_preserved_after_self_contact(self):
        a, b = rectangle("A", 100, 100), rectangle("B", 100, 100)
        asset = build_cloth_asset(PatternDocument(pieces=[a, b]), 25)
        rest = asset.positions[:, [0, 2, 1]].copy()
        rest[:, 1] = .0021
        rest[np.asarray(asset.vertex_pieces) == b.id, 1] += .0001
        settings = ClothSettings(gravity_y=0, stretch=0, bend=0, substeps=1, iterations=1, collision_quality=1)
        cpu, gpu = self.solvers(settings, rest, asset)
        collider = MeshCollider(np.array([[-1, 0, -1], [0, 0, 1], [1, 0, -1]]), [[0, 1, 2]])
        for solver in (cpu, gpu):
            solver.step(colliders=[collider])
            self.assertGreaterEqual(solver.x[:, 1].min(), settings.thickness - 1e-6)
        self.assert_cuda_matches(cpu, gpu)

    def test_cuda_sideways_moving_triangle_is_tested_at_contact_pose(self):
        settings = ClothSettings(gravity_y=0, stretch=0, bend=0, self_collision=False, friction=0,
                                 substeps=1, iterations=1, collision_quality=1)
        cpu, gpu = self.solvers(settings)
        previous = np.array([[-.26, -.1, -.06], [-.2, -.1, .06], [-.14, -.1, -.06]])
        collider = MeshCollider(previous + [.4, .2, 0], [[0, 1, 2]], previous)
        for solver in (cpu, gpu):
            solver.step(colliders=[collider])
            self.assertGreaterEqual(solver.x[0, 1], .102 - 1e-6)
        self.assert_cuda_matches(cpu, gpu)

    def test_cuda_rotating_body_uses_contact_pose_and_previous_normal(self):
        settings = ClothSettings(gravity_y=0, stretch=0, bend=0, self_collision=False, friction=0,
                                 substeps=1, iterations=1, collision_quality=1)
        cpu, gpu = self.solvers(settings)
        previous = np.array([[-1, -1, -.1], [1, -1, -.1], [0, 1, -.1]])
        angle = np.pi / 3
        rotation = np.array([[1, 0, 0], [0, np.cos(angle), -np.sin(angle)], [0, np.sin(angle), np.cos(angle)]])
        current = previous @ rotation.T + [0, 0, .2]
        collider = MeshCollider(current, [[0, 1, 2]], previous)
        normal = np.cross(current[1] - current[0], current[2] - current[0])
        normal /= np.linalg.norm(normal)
        for solver in (cpu, gpu):
            solver.step(colliders=[collider])
            self.assertGreaterEqual(np.dot(solver.x[0] - current[0], normal), settings.thickness - 1e-6)
        self.assert_cuda_matches(cpu, gpu)


if __name__ == "__main__":
    unittest.main()
