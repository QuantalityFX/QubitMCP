"""Normal skinning must preserve the authored corners and affine transforms."""
import unittest

import numpy as np

from echograph.ui.gl_mgl_renderer import MGLRendererMixin


class NormalDeformationTests(unittest.TestCase):
    def test_blended_normals_match_individual_influences_with_seams_and_scale(self):
        rng = np.random.default_rng(23)
        bind = rng.normal(size=(60, 3)).astype("f4")
        indices = rng.integers(12, size=60)
        joints = rng.integers(-1, 6, size=(12, 8))
        weights = rng.random((12, 8)).astype("f4")
        weights[:, 4:] = 0
        weights[0] = 0  # Unweighted vertices retain their authored normals.
        matrices = np.tile(np.eye(4, dtype="f4"), (5, 1, 1))
        matrices[:, :3, :3] += rng.normal(0, .2, (5, 3, 3))
        affine = np.diag([1.5, .5, 2, 1]).astype("f4")
        mesh = dict(bind_tri_normals=bind, triangle_indices=indices,
                    joint_indices=joints, joint_weights=weights, affine=affine)
        expected = []
        for normal, vertex in zip(bind, indices):
            out = np.zeros(3)
            weighted = False
            for joint, weight in zip(joints[vertex], weights[vertex]):
                if 0 <= joint < len(matrices) and weight > 1e-8:
                    out += np.linalg.inv(matrices[joint, :3, :3]).T @ normal * weight
                    weighted = True
            if not weighted:
                out = normal
            out = np.linalg.inv(affine[:3, :3]).T @ out
            expected.append(out / np.linalg.norm(out))
        actual = MGLRendererMixin()._mgl_fbx_deform_triangle_normals(mesh, bind, matrices)
        np.testing.assert_allclose(actual, expected, atol=2e-6)
        self.assertEqual(actual.dtype, np.float32)
        slots, unweighted = MGLRendererMixin._mgl_fbx_runtime_influence_slots(joints, weights, len(matrices))
        mesh.update(influence_slots=slots, unweighted_indices=unweighted, influence_joint_count=len(matrices))
        actual = MGLRendererMixin()._mgl_fbx_deform_triangle_normals(mesh, bind, matrices)
        np.testing.assert_allclose(actual, expected, atol=2e-6)

    def test_normal_palette_handles_reflections_shear_and_very_small_scales(self):
        rng = np.random.default_rng(12)
        linear = rng.normal(size=(100, 3, 3))
        linear[0] = np.diag([-2, 3, .5])
        linear[1] = np.diag([1e-12, 1e-12, 1e-12])
        linear[2] = np.diag([1e-12, 1, 1])
        result = MGLRendererMixin._mgl_fbx_normal_matrices(linear)
        np.testing.assert_allclose(result, np.linalg.inv(linear).transpose(0, 2, 1), rtol=1e-6, atol=1e-6)

    def test_affine_cache_observes_in_place_edits_and_removal(self):
        renderer = MGLRendererMixin()
        bind = np.tile([1, 1, 1], (3, 1)).astype("f4")
        affine = np.eye(4, dtype="f4")
        mesh = dict(bind_tri_normals=bind, triangle_indices=[0, 1, 2], affine=affine)
        renderer._mgl_fbx_deform_triangle_normals(mesh, bind)
        affine[0, 0] = 2
        expected = np.array([.5, 1, 1]); expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(renderer._mgl_fbx_deform_triangle_normals(mesh, bind), np.tile(expected, (3, 1)), atol=1e-6)
        mesh.pop("affine")
        np.testing.assert_allclose(renderer._mgl_fbx_deform_triangle_normals(mesh, bind), bind / np.sqrt(3), atol=1e-6)

    def test_singular_transforms_and_missing_authored_normals(self):
        renderer = MGLRendererMixin()
        points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype="f4")
        expected = np.tile([0, 0, 1], (3, 1))
        np.testing.assert_allclose(renderer._mgl_fbx_deform_triangle_normals({}, points), expected)
        mesh = dict(bind_tri_normals=expected, triangle_indices=[0, 1, 2],
                    joint_indices=[[0], [0], [0]], joint_weights=[[1], [1], [1]])
        singular = np.array([np.diag([1, 1, 0, 1])], dtype="f4")
        np.testing.assert_allclose(renderer._mgl_fbx_deform_triangle_normals(mesh, points, singular), expected)
