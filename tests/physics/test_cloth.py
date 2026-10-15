from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

from echograph.patterns.commands import rectangle
from echograph.patterns.document import PatternDocument
from echograph.patterns.sewing import add_sew_pair
from echograph.patterns.elastic import set_elastic_edges
from echograph.physics.cloth import ClothSettings, ClothSolver, build_cloth_asset
from echograph.physics.cloth.collisions import MeshCollider, closest_points, TriangleBroadphase
from echograph.physics.cloth.cache import FrameCache, read_bake, write_bake


def panel():
    return build_cloth_asset(PatternDocument(pieces=[rectangle("Panel", 100, 100)]), 25)


def settings(**kwargs):
    return replace(ClothSettings(device="cpu", self_collision=False, gravity_y=0,
                                 substeps=2, iterations=8), **kwargs)


class AssetTests(unittest.TestCase):
    def test_elastic_boundary_mapping_and_material_cache_keep_topology(self):
        piece = rectangle("Fabric", 100, 100)
        doc = PatternDocument(pieces=[piece])
        ordinary = build_cloth_asset(doc, 25)
        set_elastic_edges(doc, [(piece.id, piece.segments[2].id)], True, 1000, .8)
        elastic = build_cloth_asset(doc, 25)
        self.assertNotEqual(elastic.signature, ordinary.signature)
        self.assertEqual(elastic.topology_signature, ordinary.topology_signature)
        self.assertEqual(elastic.rest_obj(), ordinary.rest_obj())
        np.testing.assert_array_equal(elastic.positions, ordinary.positions)
        np.testing.assert_array_equal(elastic.triangles, ordinary.triangles)
        self.assertEqual(len(elastic.elastic_indices), 4)
        np.testing.assert_allclose(elastic.positions[elastic.elastic_indices, 1], .1)
        np.testing.assert_allclose(elastic.elastic_ratios, .8)
        np.testing.assert_allclose(elastic.elastic_strengths, 4000)
        self.assertFalse(elastic.elastic_indices.flags.writeable)
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
    def test_elastic_cuff_holds_on_vertical_body_where_friction_alone_slides(self):
        sides = 32
        angles = np.arange(sides) * 2 * np.pi / sides
        body = np.r_[np.c_[.1 * np.cos(angles), np.full(sides, -3), .1 * np.sin(angles)],
                     np.c_[.1 * np.cos(angles), np.full(sides, 3), .1 * np.sin(angles)]]
        faces = []
        for i in range(sides):
            j = (i + 1) % sides
            faces.extend([[i, sides + i, j], [sides + i, sides + j, j]])
        collider = MeshCollider(body, faces)
        piece = rectangle("Cuff", 700, 100)
        doc = PatternDocument(pieces=[piece])
        add_sew_pair(doc, piece.id, piece.segments[1].id, piece.id, piece.segments[3].id)
        drops = []
        for elastic in (False, True):
            if elastic:
                set_elastic_edges(doc, [(piece.id, piece.segments[2].id)], True, 1000, .8)
            asset = build_cloth_asset(doc, 50)
            angles = asset.positions[:, 0] / .7 * 2 * np.pi
            # The loose cuff must clear the body along its coarse polygon edges,
            # too. At radius .104 its vertices cleared but its chords pressed
            # into the 3 mm margin, producing real pressure with edge contacts.
            rest = np.c_[.108 * np.cos(angles), asset.positions[:, 1], .108 * np.sin(angles)]
            solver = ClothSolver(asset, settings(gravity_y=-9.81, bend=0, friction=1,
                                                 substeps=4, iterations=12, collision_quality=4), rest)
            for _ in range(12):
                solver.step(colliders=[collider])
            top = asset.positions[:, 1] > .099
            drops.append(np.mean(rest[top, 1] - solver.x[top, 1]))
        self.assertGreater(drops[0], .5)
        self.assertLess(abs(drops[1]), .005)

    def test_elastic_strength_resists_load_and_replaces_boundary_stretch(self):
        lengths = []
        for strength in (1, 1000):
            piece = rectangle("Fabric", 100, 100)
            doc = PatternDocument(pieces=[piece])
            set_elastic_edges(doc, [(piece.id, piece.segments[1].id)], True, strength, .8)
            asset = build_cloth_asset(doc, 25)
            ids = asset.elastic_indices
            top = int(ids.ravel()[np.argmax(asset.positions[ids.ravel(), 1])])
            solver = ClothSolver(asset, settings(gravity_y=-9.81, stretch=0, bend=0,
                                                 density=20, damping=3, iterations=12), pins=[top])
            ordinary_edges = {tuple(sorted(edge)) for group in solver.groups if group["kind"] == "stretch"
                              for edge in group["indices"]}
            self.assertFalse(ordinary_edges & {tuple(sorted(edge)) for edge in ids})
            for _ in range(48):
                solver.step()
            lengths.append(np.linalg.norm(solver.x[ids[:, 0]] - solver.x[ids[:, 1]], axis=1).sum())
        self.assertGreater(lengths[0], .15)
        self.assertLess(lengths[1], .083)

    def test_elastic_edit_and_pin_edit_preserve_pose_rest_shape_and_clock(self):
        piece = rectangle("Fabric", 100, 100)
        doc = PatternDocument(pieces=[piece])
        asset = build_cloth_asset(doc, 25)
        solver = ClothSolver(asset, settings(gravity_y=-9.81, stretch=0, bend=0))
        solver.x[:] += [1, 2, 3]
        solver.velocity[:] = [.1, .2, .3]
        solver.time = 6
        pose, rest, velocity = solver.x.copy(), solver.rest.copy(), solver.velocity.copy()
        set_elastic_edges(doc, [(piece.id, piece.segments[2].id)], True, 3000, .85)
        self.assertTrue(solver.update_elastic(build_cloth_asset(doc, 25)))
        np.testing.assert_array_equal(solver.x, pose)
        np.testing.assert_array_equal(solver.velocity, velocity)
        solver.update_pins([0])
        np.testing.assert_array_equal(solver.x, pose)
        np.testing.assert_array_equal(solver.rest, rest)
        np.testing.assert_array_equal(solver.velocity[1:], velocity[1:])
        self.assertEqual(solver.time, 6)
        solver.step()
        np.testing.assert_array_equal(solver.x[0], pose[0])
        self.assertEqual(solver.inverse_mass[0], 0)
        solver.update_settings(replace(solver.settings, density=.5, stretch=100000))
        self.assertEqual(next(g for g in solver.groups if g["kind"] == "elastic")["stiffness"], 3000)
        solver.update_pins([])
        solver.step()
        self.assertGreater(np.linalg.norm(solver.x[0] - pose[0]), 1e-6)
    def test_meter_scale_shirt_density_sets_area_mass_without_changing_gravity(self):
        asset = build_cloth_asset(PatternDocument(pieces=[rectangle("Fabric", 1000, 1000)]), 100)
        for density in (.2, .5):
            solver = ClothSolver(asset, settings(density=density, gravity_y=-9.81, stretch=0, bend=0, damping=0))
            np.testing.assert_allclose(np.ptp(solver.rest, axis=0)[:2], [1, 1])
            self.assertAlmostEqual(np.sum(1 / solver.inverse_mass), density, places=9)
            solver.step()
            np.testing.assert_allclose(solver.velocity[:, 1], -9.81 / solver.settings.fps, atol=1e-12)

    def test_static_friction_holds_resting_cloth_on_slope_at_different_solve_budgets(self):
        asset = panel()
        angle = np.deg2rad(20)
        normal = np.array([0, np.cos(angle), np.sin(angle)])
        tangent = np.array([0, -np.sin(angle), np.cos(angle)])
        surface = np.array([[-4, 0, -4], [0, 0, 4], [4, 0, -4]], dtype=float)
        surface = surface[:, :1] * [1, 0, 0] + surface[:, 2:] * tangent
        collider = MeshCollider(surface, [[0, 1, 2]])
        for substeps, quality, density in ((1, 1, .1), (4, 2, .2), (8, 4, .5)):
            with self.subTest(substeps=substeps, quality=quality, density=density):
                config = settings(gravity_y=-9.81, stretch=0, bend=0, damping=0,
                                  substeps=substeps, collision_quality=quality, density=density)
                rest = asset.positions[:, :1] * [1, 0, 0] + asset.positions[:, 1:2] * tangent + normal * config.thickness
                solver = ClothSolver(asset, config, rest)
                for _ in range(24):
                    solver.step(colliders=[collider])
                np.testing.assert_allclose(solver.x, rest, atol=1e-9)
                np.testing.assert_allclose(solver.velocity, 0, atol=1e-8)

    def test_dynamic_friction_slows_sliding_without_gluing_unloaded_cloth(self):
        asset = panel()
        config = settings(gravity_y=-9.81, stretch=0, bend=0, damping=0, friction=.6)
        rest = asset.positions[:, [0, 2, 1]].copy()
        rest[:, 1] = config.thickness
        collider = MeshCollider([[-4, 0, -4], [0, 0, 4], [4, 0, -4]], [[0, 1, 2]])
        for friction, gravity, should_stop in ((.6, -9.81, True), (0, -9.81, False), (.6, 0, False)):
            with self.subTest(friction=friction, gravity=gravity):
                solver = ClothSolver(asset, replace(config, friction=friction, gravity_y=gravity), rest)
                solver.velocity[:, 0] = .1
                for _ in range(5):
                    solver.step(colliders=[collider])
                self.assertGreaterEqual(solver.x[:, 1].min(), config.thickness - 1e-9)
                np.testing.assert_allclose(solver.velocity[:, 0], 0 if should_stop else .1, atol=1e-8)

    def test_collision_quality_is_linear_instead_of_squared(self):
        class CountingCollider:
            calls = 0

            def interpolate(self, *args):
                return self

            def project(self, *args):
                self.calls += 1

        for iterations, quality in [(8, 2), (8, 8), (2, 8)]:
            for self_collision in (False, True):
                collider = CountingCollider()
                solver = ClothSolver(panel(), settings(substeps=3, iterations=iterations,
                    collision_quality=quality, self_collision=self_collision))
                solver.step(colliders=[collider])
                self.assertEqual(collider.calls, 3 * (quality + int(self_collision)))

    def test_gravity_and_disable(self):
        asset = panel()
        solver = ClothSolver(asset, settings(gravity_y=-9.81))
        solver.step()
        self.assertLess(solver.x[:, 1].mean(), asset.positions[:, 1].mean())
        disabled = ClothSolver(asset, settings(enabled=False, gravity_y=-9.81))
        disabled.step()
        np.testing.assert_array_equal(disabled.x, asset.positions)

    def test_gravity_switch_keeps_strength_and_changes_acceleration(self):
        solver = ClothSolver(panel(), settings(gravity_y=-9.81, gravity_enabled=False, damping=0))
        initial = solver.x.copy()
        solver.step()
        np.testing.assert_allclose(solver.x, initial, atol=1e-12)
        solver.update_settings(replace(solver.settings, gravity_enabled=True))
        self.assertEqual(solver.settings.gravity_y, -9.81)
        solver.step()
        self.assertLess(solver.x[:, 1].mean(), initial[:, 1].mean())
        self.assertAlmostEqual(solver.time, 2 / 24)

    def test_updated_material_constraints_match_solver_built_with_new_settings(self):
        a, b = rectangle("A", 100, 100), rectangle("B", 100, 100)
        b.layout[0] = 150
        document = PatternDocument(pieces=[a, b])
        add_sew_pair(document, a.id, a.segments[1].id, b.id, b.segments[3].id)
        asset = build_cloth_asset(document, 25)
        solver = ClothSolver(asset, settings(stretch=0, bend=0, seam_stiffness=0), pins=[0])
        solver.x[:, 2] += np.sin(asset.positions[:, 0] * 30) * .02
        solver.velocity[:, 1] = -.1
        solver.time = 3
        positions, velocity = solver.x.copy(), solver.velocity.copy()
        tuned = replace(solver.settings, stretch=2000, bend=.2, seam_stiffness=5000,
                        density=.8, substeps=3, iterations=5, gravity_y=-9.81)
        reference = ClothSolver(asset, tuned, pins=[0])
        reference.x[:] = positions
        reference.velocity[:] = velocity
        reference.time = solver.time
        solver.update_settings(tuned)
        np.testing.assert_array_equal(solver.x, positions)
        np.testing.assert_array_equal(solver.velocity, velocity)
        self.assertEqual(solver.time, 3)
        for _ in range(3):
            solver.step()
            reference.step()
        np.testing.assert_allclose(solver.x, reference.x, atol=1e-10)
        np.testing.assert_allclose(solver.velocity, reference.velocity, atol=1e-10)

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

    def test_self_contact_cannot_leave_cloth_inside_body_thickness(self):
        a, b = rectangle("A", 100, 100), rectangle("B", 100, 100)
        asset = build_cloth_asset(PatternDocument(pieces=[a, b]), 25)
        rest = asset.positions[:, [0, 2, 1]].copy()
        rest[:, 1] = .0021
        rest[np.asarray(asset.vertex_pieces) == b.id, 1] += .0001
        solver = ClothSolver(asset, settings(self_collision=True, stretch=0, bend=0,
                                            substeps=1, iterations=1, collision_quality=1), rest)
        collider = MeshCollider(np.array([[-1, 0, -1], [0, 0, 1], [1, 0, -1]]), [[0, 1, 2]])
        solver.step(colliders=[collider])
        self.assertGreaterEqual(solver.x[:, 1].min(), solver.settings.thickness - 1e-8)

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
    def test_cloth_surface_clearance_includes_thickness_of_both_garments(self):
        surface = np.array([[-1, -1, 0], [1, -1, 0], [0, 1, 0]], dtype=float)
        collider = MeshCollider(surface, [[0, 1, 2]], surface.copy(), two_sided=True, surface_thickness=.004)
        sampled = collider.interpolate(.5)
        self.assertEqual(sampled.surface_thickness, .004)
        old = np.array([[0, 0, .01], [0, 0, -.01]])
        current = np.array([[0, 0, .001], [0, 0, -.001]])
        sampled.project(current, old, np.ones(2), .003, 0)
        np.testing.assert_allclose(current[:, 2], [.007, -.007], atol=1e-8)

    def test_thin_collider_preserves_previous_side_when_crossing_either_way(self):
        surface = np.array([[-1, -1, 0], [1, -1, 0], [0, 1, 0]], dtype=float)
        collider = MeshCollider(surface, [[0, 1, 2]], two_sided=True)
        old = np.array([[0, 0, .01], [0, 0, -.01]])
        current = -old
        collider.project(current, old, np.ones(2), .003, 0)
        np.testing.assert_allclose(current[:, 2], [.003, -.003], atol=1e-8)

    def test_friction_tracks_body_rotation_at_contact_instead_of_triangle_center(self):
        previous = np.array([[-2, 0, -2], [0, 0, 2], [2, 0, -2]], dtype=float)
        angle = .01
        rotation = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]])
        current = previous @ rotation.T
        old = np.array([[.05, .003, .04]])
        x = old.copy()
        x[:, 1] = .001
        collider = MeshCollider(current, [[0, 1, 2]], previous)
        collider.project(x, old, np.ones(1), .003, .6)
        # A material point follows the rotation. The triangle centroid is elsewhere.
        np.testing.assert_allclose(x, old @ rotation.T, atol=7e-6)

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

    def test_outward_escape_is_not_projected_back_inside(self):
        old, x = np.array([[0, -.001, 0.0]]), np.array([[0, .01, 0.0]])
        before = x.copy()
        self.collider().project(x, old, np.ones(1), .002, 0)
        np.testing.assert_allclose(x, before)

    def test_penetration_recovery_does_not_oscillate_between_passes(self):
        old = np.array([[0, -.001, 0.0]])
        x = old.copy()
        for _ in range(3):
            self.collider().project(x, old, np.ones(1), .002, 0)
            self.assertGreaterEqual(x[0, 1], .002 - 1e-10)

    def test_swept_triangle_is_checked_at_contact_time(self):
        previous = np.array([[-.26, -.1, -.06], [-.2, -.1, .06], [-.14, -.1, -.06]])
        collider = MeshCollider(previous + [.4, .2, 0], [[0, 1, 2]], previous)
        x = np.array([[0, 0, 0.0]])
        self.assertTrue(collider.project(x, x.copy(), np.ones(1), .002, 0)[0])
        self.assertGreaterEqual(x[0, 1], .102 - 1e-10)

    def test_rotating_triangle_uses_previous_normal_and_contact_pose(self):
        previous = np.array([[-1, -1, -.1], [1, -1, -.1], [0, 1, -.1]])
        angle = np.pi / 3
        rotation = np.array([[1, 0, 0], [0, np.cos(angle), -np.sin(angle)], [0, np.sin(angle), np.cos(angle)]])
        current = previous @ rotation.T + [0, 0, .2]
        collider = MeshCollider(current, [[0, 1, 2]], previous)
        x = np.array([[0, 0, 0.0]])
        self.assertTrue(collider.project(x, x.copy(), np.ones(1), .002, 0)[0])
        normal = np.cross(current[1] - current[0], current[2] - current[0])
        normal /= np.linalg.norm(normal)
        self.assertGreaterEqual(np.dot(x[0] - current[0], normal), .002 - 1e-10)

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
            cache.put(frame, np.ones((20, 3)) * frame, np.ones((20, 3)), simulation_time=frame / 24)
        self.assertLess(cache.bytes, cache.budget)
        self.assertIsNone(cache.get(0))
        np.testing.assert_allclose(cache.get(9)[1], 1)
        self.assertEqual(set(cache.times), set(cache.frames))
        self.assertEqual(cache.times[9], 9 / 24)
        cache.clear()
        self.assertFalse(cache.times)

    def test_disk_cache_rejects_wrong_signature(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cloth.npz"
            write_bake(path, {"signature": "first"}, [0, 1], np.zeros((2, 3, 3)))
            _, frames = read_bake(path, "first")
            self.assertEqual(set(frames), {0, 1})
            with self.assertRaises(ValueError):
                read_bake(path, "second")

    def test_cache_geometry_allows_new_settings_but_rejects_another_pattern(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.npz"
            write_bake(path, {"signature": "old settings", "geometry_signature": "shirt"},
                       [0], np.zeros((1, 3, 3)))
            _, frames = read_bake(path, "new settings", geometry_signature="shirt")
            self.assertEqual(set(frames), {0})
            with self.assertRaises(ValueError):
                read_bake(path, "old settings", geometry_signature="pants", allow_legacy_state=True)

    def test_legacy_snapshot_can_resume_with_new_settings_but_bakes_remain_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.npz"
            write_bake(path, {"signature": "gravity zero", "cache_type": "state"}, [0], np.zeros((1, 3, 3)))
            _, frames = read_bake(path, "gravity on", geometry_signature="shirt", allow_legacy_state=True)
            self.assertEqual(set(frames), {0})
            write_bake(path, {"signature": "gravity zero", "cache_type": "bake"}, [0], np.zeros((1, 3, 3)))
            with self.assertRaises(ValueError):
                read_bake(path, "gravity on", geometry_signature="shirt", allow_legacy_state=True)

    def test_disk_cache_preserves_velocities_and_reads_legacy_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.npz"
            positions = np.arange(9, dtype=float).reshape(1, 3, 3)
            velocities = positions * .1
            write_bake(path, {"signature": "cloth", "resume_frame": 7, "solver_time": .2},
                       [7], positions, velocities)
            metadata, frames, velocity = read_bake(path, "cloth", include_velocities=True)
            self.assertEqual(metadata["resume_frame"], 7)
            np.testing.assert_allclose(frames[7], positions[0])
            np.testing.assert_allclose(velocity[7], velocities[0])
            write_bake(path, {"signature": "cloth"}, [7], positions)
            _, _, legacy_velocity = read_bake(path, "cloth", include_velocities=True)
            np.testing.assert_array_equal(legacy_velocity[7], 0)


if __name__ == "__main__":
    unittest.main()
