"""Device parity check; run again after setup.bat installs the main requirements."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch
import numpy as np

from echograph.patterns.commands import rectangle
from echograph.patterns.document import PatternDocument
from echograph.patterns.sewing import add_sew_pair
from echograph.patterns.elastic import set_elastic_edges
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

    def test_cuda_elastic_edge_constraints_and_live_strength_edit_match_cpu(self):
        piece = rectangle("Elastic panel", 100, 100)
        doc = PatternDocument(pieces=[piece])
        set_elastic_edges(doc, [(piece.id, piece.segments[2].id)], True, 1000, .8)
        asset = build_cloth_asset(doc, 25)
        config = ClothSettings(gravity_y=0, stretch=0, bend=0, self_collision=False, substeps=4, iterations=8)
        cpu, gpu = self.solvers(config, asset=asset)
        for _ in range(6):
            cpu.step()
            gpu.step()
        self.assert_cuda_matches(cpu, gpu)
        ids = asset.elastic_indices
        self.assertLess(np.linalg.norm(gpu.x[ids[:, 0]] - gpu.x[ids[:, 1]], axis=1).sum(), .085)
        before, clock = gpu.x.copy(), gpu.time
        set_elastic_edges(doc, [(piece.id, piece.segments[2].id)], True, 4000, .7)
        tuned = build_cloth_asset(doc, 25)
        for solver in (cpu, gpu):
            solver.update_elastic(tuned)
        np.testing.assert_array_equal(gpu.x, before)
        self.assertEqual(gpu.time, clock)
        for _ in range(4):
            cpu.step()
            gpu.step()
        self.assert_cuda_matches(cpu, gpu, atol=2e-5)

    def test_cuda_pin_add_remove_updates_captured_mass_and_current_anchors(self):
        config = ClothSettings(gravity_y=-9.81, stretch=0, bend=0, self_collision=False)
        cpu, gpu = self.solvers(config)
        for _ in range(2):
            cpu.step()
            gpu.step()
        current = gpu.x.copy()
        for solver in (cpu, gpu):
            solver.update_pins([0])
        graph = gpu._accelerator.graph
        for _ in range(4):
            cpu.step()
            gpu.step()
        self.assert_cuda_matches(cpu, gpu)
        self.assertIs(gpu._accelerator.graph, graph)
        np.testing.assert_allclose(gpu.x[0], current[0], atol=1e-7)
        self.assertLess(gpu.x[1:, 1].mean(), current[1:, 1].mean())
        for solver in (cpu, gpu):
            solver.update_pins([])
            solver.step()
        self.assertLess(gpu.x[0, 1], current[0, 1])
        self.assert_cuda_matches(cpu, gpu)

    def test_cuda_sewn_elastic_band_and_pin_edits_match_cpu(self):
        from tests.physics.test_cloth_elastic import waist_fixture
        ordinary, elastic, rest, collider, config = waist_fixture()
        cpu, gpu = self.solvers(config, rest, ordinary)
        for _ in range(12):
            cpu.step(colliders=[collider])
        # Start from the same sewn snapshot. The separate support regressions
        # exercise collider contact; this test isolates the band projection.
        gpu.x[:] = cpu.x
        gpu.velocity[:] = cpu.velocity
        gpu.time = cpu.time
        for solver in (cpu, gpu):
            before, clock = solver.x.copy(), solver.time
            solver.update_elastic(elastic)
            np.testing.assert_array_equal(solver.x, before)
            self.assertEqual(solver.time, clock)
        for _ in range(4):
            cpu.step()
            gpu.step()
        self.assert_cuda_matches(cpu, gpu, atol=5e-5)
        pin = int(elastic.elastic_welds[0][0])
        for solver in (cpu, gpu):
            solver.update_pins([pin])
        for _ in range(2):
            cpu.step()
            gpu.step()
        self.assert_cuda_matches(cpu, gpu, atol=5e-5)
        for solver in (cpu, gpu):
            solver.update_pins([])
            solver.update_settings(replace(solver.settings, density=.5))
        for _ in range(2):
            cpu.step()
            gpu.step()
        self.assert_cuda_matches(cpu, gpu, atol=1e-4)

    def test_cuda_independent_elastic_bands_keep_distinct_material_settings(self):
        front, back = rectangle("Front", 100, 100), rectangle("Back", 100, 100)
        back.layout[0] = 200
        document = PatternDocument(pieces=[front, back])
        set_elastic_edges(document, [(front.id, front.segments[2].id)], True, 1000, .8)
        set_elastic_edges(document, [(back.id, back.segments[2].id)], True, 450, .65)
        asset = build_cloth_asset(document, 25)
        config = ClothSettings(gravity_y=0, stretch=0, bend=0, self_collision=False)
        cpu, gpu = self.solvers(config, asset=asset)
        for _ in range(6):
            cpu.step()
            gpu.step()
        self.assert_cuda_matches(cpu, gpu)
        self.assertEqual(len(asset.elastic_bands), 2)
        for rows in asset.elastic_bands:
            ids = asset.elastic_indices[rows]
            length = np.linalg.norm(gpu.x[ids[:, 0]] - gpu.x[ids[:, 1]], axis=1).sum()
            self.assertAlmostEqual(length, .1 * asset.elastic_ratios[rows[0]], delta=.003)

    def test_thin_cloth_colliders_stop_crossings_from_both_sides(self):
        asset = build_cloth_asset(PatternDocument(pieces=[rectangle("Panel", 100, 100)]), 25)
        settings = ClothSettings(gravity_y=0, stretch=0, bend=0, self_collision=False, substeps=1,
                                 iterations=2, collision_quality=2, friction=0, damping=0)
        surface = np.array([[-1, -1, 0], [1, -1, 0], [0, 1, 0]], dtype=float)
        collider = MeshCollider(surface, [[0, 1, 2]], two_sided=True, surface_thickness=.004)
        for side in (-1, 1):
            positions = asset.positions.copy()
            positions[:, 2] = side * .01
            cpu, gpu = self.solvers(settings, positions, asset)
            cpu.velocity[:, 2] = gpu.velocity[:, 2] = -side * .72
            cpu.step(colliders=[collider])
            gpu.step(colliders=[collider])
            self.assert_cuda_matches(cpu, gpu, atol=2e-6)
            np.testing.assert_allclose(gpu.x[:, 2], side * (settings.thickness + collider.surface_thickness), atol=2e-6)

    def test_two_workers_initialize_and_step_independent_cuda_solvers_safely(self):
        from echograph.physics.gpu.warp_cloth import WarpProjector
        asset = build_cloth_asset(PatternDocument(pieces=[rectangle("Panel", 100, 100)]), 25)
        settings = ClothSettings(device="cuda", self_collision=False, substeps=2, iterations=3)
        solvers = [ClothSolver(asset, settings, asset.positions + [0, 0, offset], defer_backend=True)
                   for offset in (0, .5)]
        self.assertTrue(all(solver._accelerator is None for solver in solvers))
        original = WarpProjector.__init__
        threads = []

        def initialize(projector, *args, **kwargs):
            threads.append(threading.get_ident())
            original(projector, *args, **kwargs)

        def solve(solver):
            for _ in range(24):
                solver.step()

        with patch.object(WarpProjector, "__init__", initialize), ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(solve, solver) for solver in solvers]
            for future in futures:
                future.result(timeout=30)
        self.assertEqual(len(threads), 2)
        self.assertNotIn(threading.get_ident(), threads)
        for offset, gpu in zip((0, .5), solvers):
            cpu = ClothSolver(asset, replace(settings, device="cpu"), asset.positions + [0, 0, offset])
            solve(cpu)
            self.assert_cuda_matches(cpu, gpu, atol=1e-4)
            self.assertAlmostEqual(gpu.time, 1)

    def test_cloth_collider_thickness_change_updates_captured_cuda_graph(self):
        asset = build_cloth_asset(PatternDocument(pieces=[rectangle("Panel", 100, 100)]), 25)
        settings = ClothSettings(gravity_y=0, stretch=0, bend=0, self_collision=False, substeps=1,
                                 iterations=2, collision_quality=2, friction=0, damping=0)
        positions = asset.positions.copy()
        positions[:, 2] = .001
        cpu, gpu = self.solvers(settings, positions, asset)
        surface = np.array([[-1, -1, 0], [1, -1, 0], [0, 1, 0]], dtype=float)
        collider = MeshCollider(surface, [[0, 1, 2]], two_sided=True, surface_thickness=.001)
        cpu.step(colliders=[collider])
        gpu.step(colliders=[collider])
        graph = gpu._accelerator.graph
        collider.surface_thickness = .005
        cpu.velocity.fill(0)
        gpu.velocity.fill(0)
        cpu.step(colliders=[collider])
        gpu.step(colliders=[collider])
        self.assert_cuda_matches(cpu, gpu, atol=2e-6)
        self.assertIsNot(gpu._accelerator.graph, graph)
        np.testing.assert_allclose(gpu.x[:, 2], .008, atol=2e-6)

    def test_cuda_static_friction_holds_slope_and_low_friction_allows_sliding(self):
        asset = build_cloth_asset(PatternDocument(pieces=[rectangle("Panel", 100, 100)]), 25)
        angle = np.deg2rad(20)
        normal = np.array([0, np.cos(angle), np.sin(angle)])
        tangent = np.array([0, -np.sin(angle), np.cos(angle)])
        surface = np.array([[-4, 0, -4], [0, 0, 4], [4, 0, -4]], dtype=float)
        surface = surface[:, :1] * [1, 0, 0] + surface[:, 2:] * tangent
        collider = MeshCollider(surface, [[0, 1, 2]])
        for friction in (.6, .1, 0):
            with self.subTest(friction=friction):
                config = ClothSettings(self_collision=False, stretch=0, bend=0, damping=0,
                                       substeps=4, iterations=4, friction=friction)
                rest = asset.positions[:, :1] * [1, 0, 0] + asset.positions[:, 1:2] * tangent + normal * config.thickness
                cpu, gpu = self.solvers(config, rest, asset)
                for _ in range(12):
                    for solver in (cpu, gpu):
                        solver.step(colliders=[collider])
                self.assert_cuda_matches(cpu, gpu, atol=2e-5)
                if friction == .6:
                    np.testing.assert_allclose(gpu.x, rest, atol=2e-6)
                    np.testing.assert_allclose(gpu.velocity, 0, atol=2e-5)
                else:
                    self.assertGreater(np.mean((gpu.x - rest) @ tangent), .1)

    def test_cuda_friction_follows_animated_body_surface_under_gravity(self):
        asset = build_cloth_asset(PatternDocument(pieces=[rectangle("Panel", 100, 100)]), 25)
        config = ClothSettings(self_collision=False, stretch=0, bend=0, damping=0,
                               substeps=4, iterations=4, friction=.6)
        rest = asset.positions[:, [0, 2, 1]].copy()
        rest[:, 1] = config.thickness
        cpu, gpu = self.solvers(config, rest, asset)
        previous = np.array([[-4, 0, -4], [0, 0, 4], [4, 0, -4]], dtype=float)
        for _ in range(12):
            current = previous + [.001, 0, 0]
            collider = MeshCollider(current, [[0, 1, 2]], previous)
            for solver in (cpu, gpu):
                solver.step(colliders=[collider])
            self.assert_cuda_matches(cpu, gpu)
            previous = current
        np.testing.assert_allclose(gpu.x, rest + [.012, 0, 0], atol=2e-6)

    def test_cuda_contact_friction_uses_local_body_deformation(self):
        config = ClothSettings(gravity_y=0, self_collision=False, stretch=0, bend=0, damping=0,
                               substeps=1, iterations=1, collision_quality=1, friction=.6)
        cpu, gpu = self.solvers(config)
        previous = np.array([[-2, 0, -2], [0, 0, 2], [2, 0, -2]], dtype=float)
        angle = .01
        rotation = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]])
        collider = MeshCollider(previous @ rotation.T, [[0, 1, 2]], previous)
        old = cpu.x.copy()
        old[:, 1] = config.thickness
        for solver in (cpu, gpu):
            solver.x[:] = old
            solver.velocity[:, 1] = -.048  # 2 mm inward displacement in one frame.
            solver.step(colliders=[collider])
        self.assert_cuda_matches(cpu, gpu)
        np.testing.assert_allclose(gpu.x, old @ rotation.T, atol=1e-5)

    def test_cuda_updates_gravity_material_and_mass_without_losing_state(self):
        settings = ClothSettings(gravity_y=-9.81, gravity_enabled=False, stretch=0, bend=0,
                                 seam_stiffness=0, self_collision=False, substeps=2, iterations=3)
        cpu, gpu = self.solvers(settings, pins=[0])
        for solver in (cpu, gpu):
            solver.x[:, 2] += np.sin(solver.asset.positions[:, 0] * 30) * .01
            solver.time = 4
            solver.step()
            held, motion, clock = solver.x.copy(), solver.velocity.copy(), solver.time
            solver.update_settings(replace(solver.settings, gravity_enabled=True, stretch=2000, bend=.1,
                                           density=.8, substeps=3, iterations=5, collision_quality=4))
            np.testing.assert_array_equal(solver.x, held)
            np.testing.assert_array_equal(solver.velocity, motion)
            self.assertEqual(solver.time, clock)
        for _ in range(3):
            cpu.step()
            gpu.step()
        self.assert_cuda_matches(cpu, gpu, atol=3e-5)
        np.testing.assert_allclose(gpu.x[0], gpu.rest[0], atol=1e-8)

    def test_switching_cpu_cuda_backends_keeps_pose_velocity_and_clock(self):
        _, solver = self.solvers(ClothSettings(self_collision=False, substeps=2, iterations=3))
        for device in ("cpu", "cuda"):
            solver.step()
            held, motion, clock = solver.x.copy(), solver.velocity.copy(), solver.time
            solver.update_settings(replace(solver.settings, device=device))
            np.testing.assert_array_equal(solver.x, held)
            np.testing.assert_array_equal(solver.velocity, motion)
            self.assertEqual(solver.time, clock)
            solver.step()
            self.assertAlmostEqual(solver.time, clock + 1 / 24)
            self.assertTrue(solver.backend.startswith("Warp CUDA" if device == "cuda" else "CPU XPBD"),
                            solver.backend_detail)

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
        settings = ClothSettings(gravity_y=0, stretch=0, bend=0, substeps=1, iterations=1, collision_quality=1)
        rest = asset.positions[:, [0, 2, 1]].copy()
        rest[:, 1] = settings.thickness + .0001
        rest[np.asarray(asset.vertex_pieces) == b.id, 1] += .0001
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
