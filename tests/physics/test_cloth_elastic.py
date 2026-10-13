"""Elastic waist coverage across sewn front/back panels, rather than just seam ends."""
from dataclasses import replace
import unittest

import numpy as np

from echograph.patterns.commands import rectangle
from echograph.patterns.document import PatternDocument
from echograph.patterns.elastic import set_elastic_edges
from echograph.patterns.sewing import add_sew_pair
from echograph.physics.cloth import ClothSettings, ClothSolver, build_cloth_asset
from echograph.physics.cloth.collisions import MeshCollider


def waist_fixture():
    front, back = rectangle("Front", 400, 200), rectangle("Back", 400, 200)
    back.layout[0] = 500
    document = PatternDocument(pieces=[front, back])
    add_sew_pair(document, front.id, front.segments[1].id, back.id, back.segments[3].id)
    add_sew_pair(document, front.id, front.segments[3].id, back.id, back.segments[1].id)
    ordinary = build_cloth_asset(document, 25)
    is_front = np.asarray(ordinary.vertex_pieces) == front.id
    local = ordinary.positions[:, 0] - np.where(is_front, 0, .5)
    rest = np.c_[np.where(is_front, local - .2, .2 - local), ordinary.positions[:, 1],
                 np.where(is_front, .12, -.12)]
    set_elastic_edges(document, [(front.id, front.segments[2].id), (back.id, back.segments[2].id)],
                      True, 200, .4)
    elastic = build_cloth_asset(document, 25)
    sides = 48
    angle = np.arange(sides) * 2 * np.pi / sides
    vertices = np.r_[np.c_[.1 * np.cos(angle), np.full(sides, -3), .085 * np.sin(angle)],
                     np.c_[.1 * np.cos(angle), np.full(sides, 3), .085 * np.sin(angle)]]
    faces = []
    for i in range(sides):
        j = (i + 1) % sides
        faces.extend([[i, sides + i, j], [sides + i, sides + j, j]])
    collider = MeshCollider(vertices, faces)
    settings = ClothSettings(device="cpu", gravity_y=0, self_collision=False, friction=1)
    return ordinary, elastic, rest, collider, settings


class ElasticWaistTests(unittest.TestCase):
    def test_sewn_panel_edges_form_one_continuous_band_with_two_combined_seam_corners(self):
        _, asset, _, _, _ = waist_fixture()
        self.assertEqual(len(asset.elastic_bands), 1)
        np.testing.assert_array_equal(np.sort(asset.elastic_bands[0]), np.arange(len(asset.elastic_indices)))
        self.assertEqual(len(asset.elastic_welds), 2)
        self.assertTrue(all(len(weld) == 2 for weld in asset.elastic_welds))
        self.assertTrue(all(not array.flags.writeable for array in (*asset.elastic_bands, *asset.elastic_welds)))

    def test_unsewn_panel_edges_remain_independent_bands(self):
        a, b = rectangle("A", 100, 100), rectangle("B", 100, 100)
        b.layout[0] = 200
        document = PatternDocument(pieces=[a, b])
        set_elastic_edges(document, [(a.id, a.segments[2].id), (b.id, b.segments[2].id)], True, 200, .4)
        asset = build_cloth_asset(document, 25)
        self.assertEqual(len(asset.elastic_bands), 2)
        self.assertEqual(asset.elastic_welds, ())

    def check_entire_waist_support(self, device):
        ordinary, elastic, rest, collider, config = waist_fixture()
        solver = ClothSolver(ordinary, replace(config, device=device), rest)
        if device == "cuda":
            self.assertTrue(solver.backend.startswith("Warp CUDA"), solver.backend_detail)
        # Sew flat front/back panels first, as when loading a sewn saved state.
        for _ in range(24):
            solver.step(colliders=[collider])
        before, clock = solver.x.copy(), solver.time
        solver.update_elastic(elastic)
        np.testing.assert_array_equal(solver.x, before)
        self.assertEqual(solver.time, clock)
        for _ in range(24):
            solver.step(colliders=[collider])
        waist = np.unique(elastic.elastic_indices)
        fitted = solver.x[waist].copy()
        solver.update_settings(replace(solver.settings, gravity_y=-9.81))
        for _ in range(24):
            solver.step(colliders=[collider])
        # Test every waist point, including front/back middles, not just a mean
        # that could conceal supported seams and a falling centre.
        self.assertEqual(len(solver.pins), 0)
        self.assertLess(np.max(fitted[:, 1] - solver.x[waist, 1]), .04)
        radius = np.sqrt((solver.x[waist, 0] / .1) ** 2 + (solver.x[waist, 2] / .085) ** 2)
        self.assertLess(np.max(radius), 1.12)
        supported = solver.x[waist].copy()
        for _ in range(24):
            solver.step(colliders=[collider])
        # Some settling under load is physical. Continued falling is not.
        self.assertLess(np.max(supported[:, 1] - solver.x[waist, 1]), .005)
        self.assertTrue(np.isfinite(solver.x).all())
        if device == "cuda":
            self.assertTrue(solver.backend.startswith("Warp CUDA"), solver.backend_detail)
            self.assertGreater(solver.last_gpu_ms, 0)

    def test_cpu_supports_front_back_and_side_seams_at_200_newtons_and_40_percent_rest(self):
        self.check_entire_waist_support("cpu")

    def test_cuda_supports_front_back_and_side_seams_at_200_newtons_and_40_percent_rest(self):
        try:
            import warp as wp
        except ImportError:
            self.skipTest("warp-lang is not installed")
        wp.init()
        if not wp.is_cuda_available():
            self.skipTest("CUDA is unavailable")
        self.check_entire_waist_support("cuda")
