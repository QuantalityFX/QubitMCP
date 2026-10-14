"""Primitive topology and the exported surfaces consumed by cloth collision."""
from collections import defaultdict
from pathlib import Path
import tempfile
import unittest

import numpy as np

from echograph.physics.cloth.collisions import MeshCollider
from echograph.ui.gl_loaders import load_obj_mesh_arrays
from nodes.primitive.spec import PRIMITIVE_SHAPES, _build_primitive_mesh, _radial_normals, _write_obj


def triangles_for(faces):
    return np.asarray([[face[0], face[i], face[i + 1]]
                       for face in faces for i in range(1, len(face) - 1)])


def outward_directions(shape, centers):
    if shape == "plane":
        return np.tile([0., 1., 0.], (len(centers), 1))
    directions = centers.copy()
    if shape == "torus":
        # Outward is away from the tube's centerline, including inside the hole.
        radial = centers[:, [0, 2]]
        directions[:, [0, 2]] -= .6 * radial / np.linalg.norm(radial, axis=1)[:, None]
    return directions


class PrimitiveCollisionTests(unittest.TestCase):
    def test_solid_meshes_are_closed_and_consistently_oriented(self):
        for _, shape in PRIMITIVE_SHAPES:
            with self.subTest(shape=shape):
                vertices, faces = _build_primitive_mesh(shape)
                triangles = triangles_for(faces)
                self.assertTrue(np.isfinite(vertices).all())
                self.assertTrue(np.all((triangles >= 0) & (triangles < len(vertices))))
                surface = np.asarray(vertices)[triangles]
                areas = np.linalg.norm(np.cross(surface[:, 1] - surface[:, 0],
                                               surface[:, 2] - surface[:, 0]), axis=1)
                self.assertTrue(np.all(areas > 1e-12))
                edges = defaultdict(list)
                for triangle in triangles:
                    for a, b in zip(triangle, np.roll(triangle, -1)):
                        edges[tuple(sorted((a, b)))].append((a, b))
                boundary = [edge for edge, uses in edges.items() if len(uses) == 1]
                self.assertEqual(len(boundary), 4 if shape == "plane" else 0)
                for edge, uses in edges.items():
                    if len(uses) == 1 and shape == "plane":
                        continue
                    self.assertEqual(len(uses), 2, edge)
                    self.assertEqual(uses[0], uses[1][::-1], edge)

    def test_all_faces_point_outward(self):
        for _, shape in PRIMITIVE_SHAPES:
            with self.subTest(shape=shape):
                vertices, faces = _build_primitive_mesh(shape)
                surface = np.asarray(vertices)[triangles_for(faces)]
                normals = np.cross(surface[:, 1] - surface[:, 0], surface[:, 2] - surface[:, 0])
                expected = outward_directions(shape, surface.mean(axis=1))
                self.assertTrue(np.all(np.sum(normals * expected, axis=1) > 0))

    def exported_mesh(self, shape, directory):
        vertices, faces = _build_primitive_mesh(shape)
        normals = None
        if shape == "sphere":
            normals = _radial_normals(vertices)
        elif shape == "plane":
            normals = [(0., 1., 0.) for _ in vertices]
        path = Path(directory) / f"{shape}.obj"
        _write_obj(path, vertices, faces, normals=normals)
        return load_obj_mesh_arrays(path)

    def test_obj_export_keeps_outward_geometry_and_shading_normals(self):
        with tempfile.TemporaryDirectory() as directory:
            for _, shape in PRIMITIVE_SHAPES:
                with self.subTest(shape=shape):
                    points, shading, _ = self.exported_mesh(shape, directory)
                    vertices, faces = _build_primitive_mesh(shape)
                    np.testing.assert_allclose(points, np.asarray(vertices)[triangles_for(faces)].reshape(-1, 3),
                                               atol=6e-7)
                    surface = points.reshape(-1, 3, 3)
                    normals = np.cross(surface[:, 1] - surface[:, 0], surface[:, 2] - surface[:, 0])
                    expected = outward_directions(shape, surface.mean(axis=1))
                    self.assertTrue(np.all(np.sum(normals * expected, axis=1) > 0))
                    self.assertTrue(np.all(np.sum(shading.reshape(-1, 3, 3) * normals[:, None], axis=2) > 0))

    def test_exported_colliders_stop_inward_crossings_on_every_triangle(self):
        with tempfile.TemporaryDirectory() as directory:
            for _, shape in PRIMITIVE_SHAPES:
                with self.subTest(shape=shape):
                    points, _, _ = self.exported_mesh(shape, directory)
                    surface = points.astype(float).reshape(-1, 3, 3)
                    centers = surface.mean(axis=1)
                    normals = np.cross(surface[:, 1] - surface[:, 0], surface[:, 2] - surface[:, 0])
                    normals /= np.linalg.norm(normals, axis=1)[:, None]
                    # Orient the probes independently of any incorrect authored winding.
                    expected = outward_directions(shape, centers)
                    normals *= np.where(np.sum(normals * expected, axis=1) > 0, 1., -1.)[:, None]
                    old = centers + normals * .005
                    current = centers - normals * .005
                    collider = MeshCollider(points, np.arange(len(points)).reshape(-1, 3))
                    thickness = .0001
                    contact = collider.project(current, old, np.ones(len(current)), thickness, 0)
                    self.assertTrue(contact.all())
                    if shape in {"plane", "torus"}:
                        distance = np.sum((current - centers) * normals, axis=1)
                    else:
                        # Near a rim the adjacent side may resolve the contact.
                        # A point is outside a convex solid if it is outside any
                        # of its face planes, not necessarily its original face.
                        distance = np.max(np.sum((current[:, None] - centers[None]) * normals[None], axis=2), axis=1)
                    self.assertTrue(np.all(distance >= thickness - 1e-9), distance.min())


if __name__ == "__main__":
    unittest.main()
