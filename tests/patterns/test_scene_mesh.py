from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from echograph.patterns.commands import polygon, rectangle, set_edge_kind
from echograph.patterns.document import PatternDocument, PatternError
from echograph.patterns.geometry import signed_area
from echograph.patterns.mesh import cached_mesh, document_obj, panel_mesh, sample_boundary
from echograph.patterns.serialization import file_hash, save_document


class PatternMeshTests(unittest.TestCase):
    def test_concave_boundary_both_windings(self):
        points = [(0, 0), (300, 0), (300, 400), (150, 200), (0, 400)]
        for rows in (points, points[::-1]):
            vertices, faces = panel_mesh(polygon("Panel", rows))
            area = 0
            for ia, ib, ic in faces:
                a, b, c = vertices[ia], vertices[ib], vertices[ic]
                triangle_area = ((b[0] - a[0]) * (c[1] - a[1])
                                 - (b[1] - a[1]) * (c[0] - a[0])) / 2
                self.assertGreater(triangle_area, 0)
                area += triangle_area
            self.assertAlmostEqual(area, abs(signed_area(polygon("Panel", rows))))

    def test_front_back_layout_units_and_curves(self):
        front = rectangle("Shirt Front", 300, 400)
        back = rectangle("Shirt Back", 300, 400)
        back.layout = [700, 0, 90]
        set_edge_kind(front, front.segments[0].id, "cubic")
        front.segments[0].controls = [[100, -50], [200, -50]]
        text = document_obj(PatternDocument(pieces=[front, back]))
        vertices = [tuple(map(float, line.split()[1:])) for line in text.splitlines() if line.startswith("v ")]
        self.assertIn("o Shirt_Front", text)
        self.assertIn("o Shirt_Back", text)
        self.assertGreater(len(vertices), 8)
        self.assertIn((0.7, 0.0, 0.0), vertices)
        self.assertIn((0.7, 0.3, 0.0), vertices)
        self.assertIn((0.3, 0.3, 0.0), vertices)
        self.assertTrue(all(v[2] == 0 for v in vertices))
        points, faces = panel_mesh(front)
        self.assertGreater(len(faces), len(points))

    def test_invalid_draft_never_produces_mesh(self):
        for piece in (polygon("Open", [(0, 0), (1, 0), (1, 1)], closed=False),
                      polygon("Crossed", [(0, 0), (2, 2), (0, 2), (2, 0)])):
            with self.assertRaises(PatternError):
                document_obj(PatternDocument(pieces=[piece]))

    def test_combined_panels_load_in_viewport_without_bridging_gap(self):
        from echograph.patterns.relationships import combine_panels
        from echograph.ui.gl_loaders import load_obj_model

        front, back = rectangle("Front", 300, 400), rectangle("Back", 300, 400)
        back.layout[0] = 500
        doc = PatternDocument(pieces=[front, back])
        combine_panels(doc, {front.id, back.id})
        with tempfile.TemporaryDirectory() as temp:
            model = load_obj_model(cached_mesh(doc, Path(temp)))
        self.assertGreater(len(model.vertices), 1000 * 3 * 3)
        self.assertEqual(model.bounds, (0.0, 0.0, 0.0, 0.8, 0.4, 0.0))
        for start in range(0, len(model.vertices), 9):
            xs = model.vertices[start:start + 9:3]
            self.assertLessEqual(max(xs) - min(xs), 0.300001)

    def test_even_boundary_spacing_and_small_interior_triangles(self):
        import math

        panel = rectangle("Front", 300, 400)
        boundary = sample_boundary(panel)
        self.assertEqual(len(boundary), 140)
        for a, b in zip(boundary, boundary[1:] + boundary[:1]):
            self.assertAlmostEqual(math.dist(a, b), 10)
        points, faces = panel_mesh(panel)
        self.assertGreater(len(points), len(boundary) + 1000)
        self.assertGreater(len(faces), 2000)
        edges = {}
        area = 0
        for face in faces:
            a, b, c = (points[i] for i in face)
            area += ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
            for i in range(3):
                edge = tuple(sorted((face[i], face[(i + 1) % 3])))
                edges[edge] = edges.get(edge, 0) + 1
                self.assertLessEqual(math.dist(*(points[j] for j in edge)), 20)
        self.assertAlmostEqual(area, 120000)
        boundary_edges = {tuple(sorted((i, (i + 1) % len(boundary)))) for i in range(len(boundary))}
        self.assertEqual({edge for edge, count in edges.items() if count == 1}, boundary_edges)
        self.assertTrue(all(count in (1, 2) for count in edges.values()))

    def test_curved_edge_has_even_arc_length_sampling(self):
        import math

        panel = rectangle("Curved", 300, 400)
        set_edge_kind(panel, panel.segments[0].id, "cubic")
        panel.segments[0].controls = [[100, -100], [200, -100]]
        boundary = sample_boundary(panel)
        end = boundary.index((300.0, 0.0))
        distances = [math.dist(a, b) for a, b in zip(boundary[:end], boundary[1:end + 1])]
        self.assertLess(max(distances), 10.001)
        self.assertLess(max(distances) - min(distances), 0.02)

    def test_slanted_sampled_edges_have_no_degenerate_exterior_triangles(self):
        from collections import Counter

        panel = polygon("Slanted", [(161.3379372791487, 134.848986960646),
            (29.09980165494918, 24.81788207715083), (-19.2666336366379, -33.56006482483037),
            (-75.62963352933609, -188.3168777651761), (163.14823092174066, -88.4468639241985),
            (227.06455583835864, -78.19959528703657), (162.34394047933543, -31.352314493762947)])
        points, faces = panel_mesh(panel)
        boundary = sample_boundary(panel)
        counts = Counter(tuple(sorted((face[i], face[(i + 1) % 3])))
                         for face in faces for i in range(3))
        self.assertEqual({edge for edge, count in counts.items() if count == 1},
                         {tuple(sorted((i, (i + 1) % len(boundary)))) for i in range(len(boundary))})
        for ia, ib, ic in faces:
            a, b, c = points[ia], points[ib], points[ic]
            self.assertGreater((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]), 1e-6)

    def test_dense_concave_mesh_preserves_every_boundary_edge_and_area(self):
        import math

        # Narrow notch forces recovery of constraints across the Delaunay hull.
        panel = polygon("Notched", [(0, 0), (300, 0), (300, 400), (160, 400),
                                   (160, 80), (140, 80), (140, 400), (0, 400)])
        for panel in (panel, polygon("Reversed", [(p.x, p.y) for p in panel.points][::-1])):
            boundary = sample_boundary(panel)
            points, faces = panel_mesh(panel)
            edges = set()
            area = 0
            for face in faces:
                a, b, c = (points[i] for i in face)
                area += ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
                for i in range(3):
                    edge = tuple(sorted((face[i], face[(i + 1) % 3])))
                    edges.add(edge)
                    self.assertLess(math.dist(*(points[j] for j in edge)), 20.001)
            self.assertAlmostEqual(area, abs(signed_area(panel)))
            self.assertTrue(all(tuple(sorted((i, (i + 1) % len(boundary)))) in edges
                                for i in range(len(boundary))))

    def test_content_cache_changes_only_with_document_content(self):
        with tempfile.TemporaryDirectory() as temp:
            doc = PatternDocument(pieces=[rectangle("Front", 300, 400)])
            first = cached_mesh(doc, Path(temp))
            stamp = first.stat().st_mtime_ns
            doc.revision += 1
            self.assertEqual(cached_mesh(doc, Path(temp)), first)
            self.assertEqual(first.stat().st_mtime_ns, stamp)
            doc.pieces[0].layout[0] = 350
            self.assertNotEqual(cached_mesh(doc, Path(temp)), first)

    def test_sewing_only_save_reuses_mesh_but_shape_and_layout_edits_rebuild(self):
        from echograph.patterns.sewing import add_sew_pair

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            front, back = rectangle("Front", 300, 400), rectangle("Back", 300, 400)
            back.layout[0] = 500
            doc = PatternDocument(pieces=[front, back])
            first = cached_mesh(doc, root)
            add_sew_pair(doc, front.id, front.segments[1].id, back.id, back.segments[3].id)
            signature = save_document(root / "sewn.pattern.json", doc)
            from echograph.patterns.serialization import load_document
            loaded, _ = load_document(root / "sewn.pattern.json")
            with patch("echograph.patterns.mesh.panel_mesh", side_effect=AssertionError("Sewing must reuse geometry")):
                self.assertEqual(cached_mesh(loaded, root), first)
                loaded.metadata["sew_pairs"][0]["reverse"] = not loaded.metadata["sew_pairs"][0]["reverse"]
                self.assertEqual(cached_mesh(loaded, root), first)
            self.assertTrue(signature)
            loaded.pieces[0].points[0].x = -20
            second = cached_mesh(loaded, root)
            self.assertNotEqual(second, first)
            loaded.pieces[0].layout[0] = -500
            self.assertNotEqual(cached_mesh(loaded, root), second)

    def test_scene_connection_saved_updates_visibility_and_disconnect(self):
        from nodes.pattern_design.spec import build_pattern_scene_asset
        from nodes.scene.spec import _collect_assets

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "shirt.pattern.json"
            doc = PatternDocument(pieces=[rectangle("Front", 300, 400), rectangle("Back", 300, 400)])
            doc.pieces[1].layout[0] = 350
            save_document(path, doc)
            source = SimpleNamespace(model=SimpleNamespace(name="Shirt", kind="pattern_design",
                                     params=[{"name": "pattern_path", "value": str(path)}]))
            edges = [SimpleNamespace(src=source)]
            graph = SimpleNamespace(views=lambda: [], _ordered_in_edges=lambda item: edges,
                                    _scene_asset_revision=0)
            source.scene = lambda: graph
            transform = {"pos": [1, 2, 3], "rot": [0, 0, 0], "scl": [1, 1, 1]}
            scene = SimpleNamespace(scene=lambda: graph, model=SimpleNamespace(
                name="Scene", params=[], _scene_hidden={"Shirt"}, _scene_xforms={"Shirt": transform}))
            with patch("echograph.patterns.mesh.cached_mesh", side_effect=lambda doc: cached_mesh(doc, root)):
                assets = _collect_assets(scene)
                self.assertEqual(len(assets), 1)
                self.assertEqual(assets[0]["node"], "Shirt")
                self.assertFalse(assets[0]["visible"])
                self.assertEqual(assets[0]["xform"], transform)
                self.assertTrue(Path(assets[0]["path"]).is_file())
                old_path = assets[0]["path"]
                doc.pieces[0].layout[0] = -350
                save_document(path, doc, expected_hash=file_hash(path))
                graph._scene_asset_revision += 1
                self.assertNotEqual(_collect_assets(scene)[0]["path"], old_path)
                edges.clear()
                graph._scene_asset_revision += 1
                self.assertEqual(_collect_assets(scene), [])
                path.unlink()
                self.assertIsNone(build_pattern_scene_asset(source))
                self.assertTrue(source.model._pattern_scene_error)


if __name__ == "__main__":
    unittest.main()
