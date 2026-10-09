from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from echograph.patterns.commands import rectangle
from echograph.patterns.document import PatternDocument
from echograph.patterns.sewing import add_sew_pair
from echograph.physics.cloth import ClothSettings, ClothSolver, build_cloth_asset
from echograph.physics.cloth.collisions import MeshCollider, closest_points, TriangleBroadphase
from echograph.physics.cloth.cache import FrameCache, read_bake, write_bake


def panel():
    return build_cloth_asset(PatternDocument(pieces=[rectangle("Panel", 100, 100)]), 25)


def settings(**kwargs):
    return replace(ClothSettings(device="cpu", self_collision=False, gravity_y=0,
                                 substeps=2, iterations=8), **kwargs)


class AssetTests(unittest.TestCase):
    def test_triangle_boundary_and_rest_units(self):
        asset = panel()
        self.assertAlmostEqual(np.max(asset.positions[:, 0]), 0.1)
        lengths = np.linalg.norm(asset.positions[asset.edges[:, 0]] - asset.positions[asset.edges[:, 1]], axis=1)
        self.assertLess(lengths.max(), .05)
        self.assertTrue(len(asset.bends))
        self.assertFalse(asset.positions.flags.writeable)

    def test_cache_ignores_editor_metadata_but_includes_sewing(self):
        a, b = rectangle("A", 100, 100), rectangle("B", 100, 100)
        b.layout[0] = 150
        doc = PatternDocument(pieces=[a, b])
        first = build_cloth_asset(doc, 25)
        doc.revision += 1
        doc.metadata["selection"] = "A"
        self.assertIs(first, build_cloth_asset(doc, 25))
        add_sew_pair(doc, a.id, a.segments[1].id, b.id, b.segments[3].id)
        second = build_cloth_asset(doc, 25)
        self.assertNotEqual(first.signature, second.signature)
        self.assertGreater(len(second.seam_indices), 0)
        np.testing.assert_array_equal(first.positions, second.positions)

    def test_unequal_edges_have_interpolated_samples_and_conserve_weights(self):
        a, b = rectangle("A", 100, 90), rectangle("B", 100, 140)
        b.layout[0] = 150
        doc = PatternDocument(pieces=[a, b])
        add_sew_pair(doc, a.id, a.segments[1].id, b.id, b.segments[3].id)
        asset = build_cloth_asset(doc, 25)
        self.assertEqual(len(asset.seam_indices), 7)
        self.assertTrue(np.any((abs(asset.seam_weights) > 0) & (abs(asset.seam_weights) < 1)))
        np.testing.assert_allclose(asset.seam_weights.sum(axis=1), 0, atol=1e-12)
        np.testing.assert_allclose(asset.seam_weights[:, :2].sum(axis=1), 1)
        with tempfile.TemporaryDirectory() as _:
            self.assertTrue(asset.rest_obj().is_file())

    def test_combined_panels_retain_sewn_edge_ownership(self):
        from echograph.patterns.relationships import combine_panels
        from echograph.patterns.commands import DocumentHistory
        a, b = rectangle("A", 100, 100), rectangle("B", 100, 100)
        b.layout[0] = 150
        doc = PatternDocument(pieces=[a, b])
        add_sew_pair(doc, a.id, a.segments[1].id, b.id, b.segments[3].id)
        history = DocumentHistory(doc)
        history.edit("Combine", lambda d: combine_panels(d, {a.id, b.id}))
        asset = build_cloth_asset(history.document, 25)
        self.assertGreater(len(asset.seam_indices), 0)
        self.assertEqual(len(set(asset.vertex_pieces)), 1)


class SolverTests(unittest.TestCase):
    def test_collision_quality_is_linear_instead_of_squared(self):
        class CountingCollider:
            calls = 0

            def interpolate(self, *args):
                return self

            def project(self, *args):
                self.calls += 1

        for iterations, quality in [(8, 2), (8, 8), (2, 8)]:
            collider = CountingCollider()
            solver = ClothSolver(panel(), settings(substeps=3, iterations=iterations, collision_quality=quality))
            solver.step(colliders=[collider])
            self.assertEqual(collider.calls, 3 * quality)

    def test_gravity_and_disable(self):
        asset = panel()
        solver = ClothSolver(asset, settings(gravity_y=-9.81))
        solver.step()
        self.assertLess(solver.x[:, 1].mean(), asset.positions[:, 1].mean())
        disabled = ClothSolver(asset, settings(enabled=False, gravity_y=-9.81))
        disabled.step()
        np.testing.assert_array_equal(disabled.x, asset.positions)

    def test_pins_remain_at_transformed_start_positions(self):
        asset = panel()
        rest = asset.positions + [1, 2, 3]
        solver = ClothSolver(asset, settings(gravity_y=-9.81), rest, pins=[0, 1])
        for _ in range(5):
            solver.step()
        np.testing.assert_array_equal(solver.x[[0, 1]], rest[[0, 1]])
        self.assertTrue(np.isfinite(solver.x).all())

    def test_stretch_control_changes_extension_under_gravity(self):
        asset = panel()
        pins = np.flatnonzero(asset.positions[:, 1] > .099)
        soft = ClothSolver(asset, settings(stretch=.1, gravity_y=-9.81), pins=pins)
        stiff = ClothSolver(asset, settings(stretch=100000, gravity_y=-9.81), pins=pins)
        for _ in range(12):
            soft.step()
            stiff.step()
        self.assertLess(soft.x[:, 1].min(), stiff.x[:, 1].min() - .005)

    def test_bend_rest_is_rigid_transform_invariant(self):
        asset = panel()
        angle = .7
        rotation = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]])
        rest = asset.positions @ rotation + [2, 1, -1]
        solver = ClothSolver(asset, settings(bend=100), rest)
        solver.step()
        np.testing.assert_allclose(solver.x, rest, atol=1e-9)

    def test_bending_reduces_curvature(self):
        asset = panel()
        soft = ClothSolver(asset, settings(stretch=0, bend=0))
        stiff = ClothSolver(asset, settings(stretch=0, bend=100))
        middle = np.argmin(np.linalg.norm(asset.positions - [.05, .05, 0], axis=1))
        soft.x[middle, 2] = stiff.x[middle, 2] = .03
        soft.step()
        stiff.step()
        measure = lambda s: np.linalg.norm(np.einsum("ni,nij->nj", asset.bend_weights, s.x[asset.bends]))
        self.assertLess(measure(stiff), measure(soft) * .7)

    def test_progressive_sewing_closes_gap_without_welding_topology(self):
        a, b = rectangle("A", 100, 100), rectangle("B", 100, 100)
        b.layout[0] = 150
        doc = PatternDocument(pieces=[a, b])
        add_sew_pair(doc, a.id, a.segments[1].id, b.id, b.segments[3].id)
        asset = build_cloth_asset(doc, 25)
        solver = ClothSolver(asset, settings(seam_seconds=.3, seam_gap=.001))
        distance = lambda: np.mean(np.linalg.norm(np.einsum("ni,nij->nj", asset.seam_weights, solver.x[asset.seam_indices]), axis=1))
        initial = distance()
        solver.step()
        self.assertGreater(distance(), initial * .5)
        for _ in range(15):
            solver.step()
        self.assertLess(distance(), .003)
        self.assertEqual(len(solver.x), len(asset.positions))

    def test_coloring_has_no_shared_active_vertices(self):
        solver = ClothSolver(panel(), settings())
        for group in solver.groups:
            for rows in group["colors"]:
                indices = group["indices"][rows][abs(group["weights"][rows]) > 1e-12]
                self.assertEqual(len(indices), len(set(indices)))

    def test_barycentric_grab_and_release(self):
        solver = ClothSolver(panel(), settings())
        ids = solver.asset.triangles[0]
        weights = np.array([.2, .3, .5])
        target = weights @ solver.x[ids] + [0, 0, .08]
        solver.set_grab(ids, weights, target)
        for _ in range(5):
            solver.step()
        self.assertLess(np.linalg.norm(weights @ solver.x[ids] - target), .01)
        solver.release_grab()
        self.assertIsNone(solver.grab)

    def test_ground_collision_and_friction(self):
        solver = ClothSolver(panel(), settings(gravity_y=-9.81, ground=True))
        for _ in range(10):
            solver.step()
        self.assertGreaterEqual(solver.x[:, 1].min(), solver.settings.thickness - 1e-10)

    def test_vertex_triangle_self_contact_and_seam_exclusions(self):
        a, b = rectangle("A", 100, 100), rectangle("B", 40, 40)
        b.layout = [30, 30, 0]
        asset = build_cloth_asset(PatternDocument(pieces=[a, b]), 25)
        solver = ClothSolver(asset, settings())
        boundary = next(i for i, p in enumerate(asset.vertex_pieces) if p == b.id)
        solver.x[boundary:, 2] = .001
        before = solver.x.copy()
        solver._self_contact(before)
        self.assertGreater(np.max(abs(solver.x[:, 2] - before[:, 2])), .0001)
        self.assertTrue(np.isfinite(solver.x).all())

    def test_bad_time_and_settings_cannot_poison_state(self):
        solver = ClothSolver(panel(), settings())
        for dt in [0, -1, float("nan"), 2]:
            with self.assertRaises(ValueError):
                solver.step(dt)
        parsed = ClothSettings.from_dict({"gravity_y": "nan", "substeps": 0, "bend": -1, "fps": 0})
        self.assertEqual(parsed.gravity_y, -9.81)
        self.assertEqual(parsed.substeps, 1)
        self.assertEqual(parsed.bend, 0)


class CollisionTests(unittest.TestCase):
    def test_large_outlier_triangle_does_not_pull_in_distant_faces(self):
        centers = np.c_[10 + np.arange(2000) * .01, np.zeros(2000), np.zeros(2000)]
        small = centers[:, None] + np.array([[-.003, 0, -.003], [0, 0, .003], [.003, 0, -.003]])
        near = np.array([[[-.01, 0, -.01], [0, 0, .01], [.01, 0, -.01]]])
        large = np.array([[[-100, 20, -100], [0, 20, 100], [100, 20, -100]]])
        broadphase = TriangleBroadphase(np.concatenate([small, near, large]))
        rows, faces = broadphase.query_pairs(np.array([[0, .001, 0]]), .002)
        np.testing.assert_array_equal(rows, [0])
        np.testing.assert_array_equal(faces, [2000])

    def test_small_fast_moving_triangle_is_included_in_swept_bounds(self):
        previous = np.array([[[-.01, -.1, -.01], [0, -.1, .01], [.01, -.1, -.01]]])
        current = previous.copy()
        current[:, :, 1] = .1
        broadphase = TriangleBroadphase(current, previous)
        rows, faces = broadphase.query_pairs(np.array([[0, 0, 0.0]]), .002)
        np.testing.assert_array_equal(rows, [0])
        np.testing.assert_array_equal(faces, [0])

    def collider(self, height=0, previous=None):
        vertices = np.array([[-1, height, -1], [1, height, -1], [0, height, 1]])
        return MeshCollider(vertices, [[0, 2, 1]], previous)

    def test_fast_vertex_cannot_cross_triangle(self):
        collider = self.collider()
        old, x = np.array([[0, .1, 0.0]]), np.array([[0, -.3, 0.0]])
        contact = collider.project(x, old, np.ones(1), .002, 0)
        self.assertTrue(contact[0])
        self.assertAlmostEqual(x[0, 1], .002)

    def test_moving_triangle_does_not_pass_through_stationary_cloth(self):
        previous = self.collider(-.1).vertices
        collider = self.collider(.1, previous)
        x = np.array([[0, 0, 0.0]])
        collider.project(x, x.copy(), np.ones(1), .002, 0)
        self.assertAlmostEqual(x[0, 1], .102)
        sampled = collider.interpolate(.5, .25)
        np.testing.assert_allclose(sampled.vertices[:, 1], 0)
        np.testing.assert_allclose(sampled.previous[:, 1], -.05)

    def test_large_triangle_edges_are_considered(self):
        x = np.array([[.98, .001, -.99]])
        contact = self.collider().project(x, x.copy(), np.ones(1), .002, .2)
        self.assertTrue(contact[0])
        self.assertTrue(np.isfinite(x).all())

    def test_pinned_vertex_is_never_projected(self):
        x = np.array([[0, .001, 0]])
        before = x.copy()
        self.collider().project(x, x.copy(), np.zeros(1), .002, 0)
        np.testing.assert_array_equal(x, before)

    def test_degenerate_triangle_closest_point_is_finite(self):
        nearest, normal, _ = closest_points(np.array([[1, 1, 0]]), np.zeros((1, 3, 3)))
        self.assertTrue(np.isfinite(nearest).all())
        self.assertTrue(np.isfinite(normal).all())


class CacheTests(unittest.TestCase):
    def test_preview_cache_bounds_memory_and_preserves_velocity(self):
        cache = FrameCache(.001)
        for frame in range(10):
            cache.put(frame, np.ones((20, 3)) * frame, np.ones((20, 3)))
        self.assertLess(cache.bytes, cache.budget)
        self.assertIsNone(cache.get(0))
        np.testing.assert_allclose(cache.get(9)[1], 1)

    def test_disk_cache_rejects_stale_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cloth.npz"
            write_bake(path, {"signature": "first"}, [0, 1], np.zeros((2, 3, 3)))
            _, frames = read_bake(path, "first")
            self.assertEqual(set(frames), {0, 1})
            with self.assertRaises(ValueError):
                read_bake(path, "second")


if __name__ == "__main__":
    unittest.main()
