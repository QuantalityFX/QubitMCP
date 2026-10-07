import math
import tempfile
import unittest
from pathlib import Path

from nodes.look_at import spec as look_at_spec
from nodes.scene import spec as scene_spec


def _scene_matrix_from_rot(rot):
    rx, ry, rz = [math.radians(-float(v)) for v in rot]

    def mm(a, b):
        return [
            [sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)]
            for r in range(3)
        ]

    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    mx = ((1, 0, 0), (0, cx, sx), (0, -sx, cx))
    my = ((cy, 0, -sy), (0, 1, 0), (sy, 0, cy))
    mz = ((cz, sz, 0), (-sz, cz, 0), (0, 0, 1))
    return mm(mm(mx, my), mz)


def _mul_row(v, m):
    return tuple(sum(float(v[k]) * float(m[k][c]) for k in range(3)) for c in range(3))


class LookAtNodeMathTests(unittest.TestCase):
    def assert_vec_close(self, got, expected):
        for a, b in zip(got, expected):
            self.assertAlmostEqual(float(a), float(b), places=5)

    def test_default_camera_axis_points_negative_z_at_target(self):
        rot = look_at_spec.look_at_rotation((0, 0, 0), (1, 0, 0), aim_axis="-z", up_axis="y")
        forward = _mul_row((0, 0, -1), _scene_matrix_from_rot(rot))
        self.assert_vec_close(forward, (1, 0, 0))

    def test_apply_constraint_preserves_scale_and_uses_quaternion_rotation(self):
        source = {"pos": [0, 0, 0], "rot": [0, 0, 0], "scl": [2, 3, 4]}
        target = {"pos": [0, 1, 0], "rot": [33, 44, 55], "scl": [1, 1, 1]}
        out = look_at_spec.apply_constraint(
            source,
            target,
            {"target_owner": "Target", "aim_axis": "-z", "up_axis": "y", "match_rotation": True},
        )
        forward = _mul_row((0, 0, -1), _scene_matrix_from_rot(out["rot"]))
        self.assert_vec_close(forward, (0, 1, 0))
        self.assertEqual(out["scl"], [2, 3, 4])

    def test_copy_rotation_mode_matches_target_rotation_exactly(self):
        out = look_at_spec.apply_constraint(
            {"pos": [0, 0, 0], "rot": [0, 0, 0], "scl": [1, 1, 1]},
            {"pos": [5, 0, 0], "rot": [10, 20, 30], "scl": [1, 1, 1]},
            {"rotation_mode": "copy_target", "match_rotation": True},
        )
        self.assertEqual(out["rot"], [10.0, 20.0, 30.0])

    def test_scene_collect_passes_camera_through_look_at_node(self):
        class Model:
            def __init__(self, name, kind, params=None):
                self.name = name
                self.kind = kind
                self.params = params or []

        class Item:
            def __init__(self, model, scene):
                self.model = model
                self._scene = scene

            def scene(self):
                return self._scene

        class Edge:
            def __init__(self, src, dst_port_name=""):
                self.src = src
                self.dst_port_name = dst_port_name

        class Scene:
            _scene_asset_revision = 0
            _edge_index_version = 0

            def __init__(self):
                self.edges = {}

            def _ordered_in_edges(self, item):
                return list(self.edges.get(item, []))

            def _in_edges(self, item):
                return self._ordered_in_edges(item)

        graph = Scene()
        camera = Item(Model("ShotCam", "camera", [{"name": "fov", "value": "60"}]), graph)
        look_at = Item(Model("LookAt", "look_at", [{"name": "target_owner", "value": ""}]), graph)
        scene = Item(Model("Scene", "scene"), graph)
        graph.edges[scene] = [Edge(look_at)]
        graph.edges[look_at] = [Edge(camera)]

        assets = scene_spec._collect_assets(scene)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["node"], "ShotCam")
        self.assertEqual(assets[0]["kind"], "camera")
        self.assertEqual(assets[0]["look_at_constraint"]["node_name"], "LookAt")

    def test_scene_look_at_targets_mesh_bounds_center(self):
        class Model:
            def __init__(self, name, kind, params=None):
                self.name = name
                self.kind = kind
                self.params = params or []

        class Item:
            def __init__(self, model, scene):
                self.model = model
                self._scene = scene

            def scene(self):
                return self._scene

        class Edge:
            def __init__(self, src, dst_port_name=""):
                self.src = src
                self.dst_port_name = dst_port_name

        class Scene:
            _scene_asset_revision = 0
            _edge_index_version = 0

            def __init__(self):
                self.edges = {}

            def _ordered_in_edges(self, item):
                return list(self.edges.get(item, []))

            def _in_edges(self, item):
                return self._ordered_in_edges(item)

        with tempfile.TemporaryDirectory() as td:
            mesh_path = Path(td) / "character.obj"
            mesh_path.write_text("v -1 1 -1\nv 1 3 1\nf 1 2 2\n", encoding="utf-8")

            graph = Scene()
            camera = Item(Model("ShotCam", "camera", [{"name": "fov", "value": "60"}]), graph)
            target = Item(Model("Character", "mesh", [{"name": "path", "value": str(mesh_path)}]), graph)
            look_at = Item(
                Model(
                    "LookAt",
                    "look_at",
                    [{"name": "target_owner", "value": "Character"}],
                ),
                graph,
            )
            scene = Item(Model("Scene", "scene"), graph)
            graph.edges[scene] = [Edge(look_at), Edge(target)]
            graph.edges[look_at] = [Edge(camera)]

            assets = scene_spec._collect_assets(scene)

        camera_asset = next(asset for asset in assets if asset.get("node") == "ShotCam")
        forward = _mul_row((0, 0, -1), _scene_matrix_from_rot(camera_asset["xform"]["rot"]))
        self.assert_vec_close(forward, (0, 1, 0))

    def test_scene_look_at_overrides_direct_duplicate_camera(self):
        class Model:
            def __init__(self, name, kind, params=None):
                self.name = name
                self.kind = kind
                self.params = params or []

        class Item:
            def __init__(self, model, scene):
                self.model = model
                self._scene = scene

            def scene(self):
                return self._scene

        class Edge:
            def __init__(self, src, dst_port_name=""):
                self.src = src
                self.dst_port_name = dst_port_name

        class Scene:
            _scene_asset_revision = 0
            _edge_index_version = 0

            def __init__(self):
                self.edges = {}

            def _ordered_in_edges(self, item):
                return list(self.edges.get(item, []))

            def _in_edges(self, item):
                return self._ordered_in_edges(item)

        graph = Scene()
        camera = Item(Model("ShotCam", "camera", [{"name": "fov", "value": "60"}]), graph)
        target = Item(Model("Target", "camera", [{"name": "fov", "value": "60"}]), graph)
        look_at = Item(
            Model(
                "LookAt",
                "look_at",
                [{"name": "target_owner", "value": "Target"}],
            ),
            graph,
        )
        scene = Item(Model("Scene", "scene"), graph)
        graph.edges[scene] = [Edge(camera), Edge(look_at), Edge(target)]
        graph.edges[look_at] = [Edge(camera)]

        assets = scene_spec._collect_assets(scene)

        self.assertEqual(len([asset for asset in assets if asset.get("node") == "ShotCam"]), 1)
        camera_asset = next(asset for asset in assets if asset.get("node") == "ShotCam")
        self.assertIn("look_at_constraint", camera_asset)


if __name__ == "__main__":
    unittest.main()
