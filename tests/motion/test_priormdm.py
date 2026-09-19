from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from scipy.spatial.transform import Rotation

from echograph.motion import MotionGenerationService, MotionRequest
from echograph.motion.backends.priormdm.backend import PriorMDMBackend, PriorMDMConfig, checkpoint_settings
from echograph.motion.conversion import (
    JOINT_NAMES, PARENTS, REST_DIRECTIONS, load_motion_archive, reconstruct_animation,
    save_generated_motion,
)
from echograph.rigging.bvh_ingest import ingest_bvh_animation_data
from echograph.rigging.fbx_canonical import AnimationClip, SkeletonAsset
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
from echograph.motion.storage import load_animation

ROOT = Path(__file__).resolve().parents[2]


def script_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_motion(frames=6):
    """Known rigidly turning/translating skeleton; independent of inference."""
    rest = np.zeros((22, 3))
    for j in range(1, 22):
        rest[j] = rest[PARENTS[j]] + REST_DIRECTIONS[j] * (0.18 if j < 16 else 0.25)
    positions = []
    for f in range(frames):
        rotation = Rotation.from_euler("Y", 25 * f, degrees=True).as_matrix()
        positions.append(rest @ rotation.T + [f * 0.12, 1.0, f * -0.08])
    return np.array(positions)


def evaluated_positions(skeleton, clip, frame):
    result = evaluate_rig_at_time(skeleton, clip, frame / clip.sample_rate_hz)
    return np.array(result.global_matrices).reshape(-1, 4, 4)[:, :3, 3]


class MotionConversionTests(unittest.TestCase):
    def test_canonical_round_trip_preserves_world_positions_and_root(self):
        positions = synthetic_motion()
        # Nonrigid bone length variation must still survive the canonical archive.
        positions[3, 20] += [0.025, -0.015, 0.01]
        skeleton, clip, rotations, offsets = reconstruct_animation(positions, 20, {"request": {"seed": 42}})
        restored_skeleton = SkeletonAsset.from_dict(json.loads(json.dumps(skeleton.to_dict())))
        restored_clip = AnimationClip.from_dict(json.loads(json.dumps(clip.to_dict())))
        for frame in range(len(positions)):
            np.testing.assert_allclose(evaluated_positions(restored_skeleton, restored_clip, frame), positions[frame], atol=1e-6)
        self.assertAlmostEqual(clip.end_time, 0.25)
        self.assertEqual(clip.metadata["units"], "metres")

    def test_saved_bvh_plays_through_existing_rig_evaluator(self):
        positions = synthetic_motion()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            np.savez(root / "motion.npz", positions=positions, fps=20.0, metadata=json.dumps({"prompt": "turn"}))
            result = save_generated_motion(root)
            imported = ingest_bvh_animation_data(result.bvh_path)
            mapping = [JOINT_NAMES.index(name) for name in imported.skeleton.joint_names]
            for frame in range(len(positions)):
                np.testing.assert_allclose(evaluated_positions(imported.skeleton, imported.clips[0], frame), positions[frame, mapping], atol=1e-6)
            with self.assertRaises(FileExistsError):
                save_generated_motion(root)
            self.assertTrue(result.animation_path.is_file())
            loaded_skeleton, loaded_clip = load_animation(result.animation_path)
            np.testing.assert_allclose(evaluated_positions(loaded_skeleton, loaded_clip, 4), positions[4], atol=1e-6)

    def test_invalid_archive_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "motion.npz"
            for data, fps in ((np.zeros((6, 21, 3)), 20), (np.full((6, 22, 3), np.nan), 20), (synthetic_motion(), 0)):
                np.savez(path, positions=data, fps=fps, metadata="{}")
                with self.assertRaises(ValueError):
                    load_motion_archive(path)

    def test_rotations_handle_opposite_direction_without_nan(self):
        motion = synthetic_motion(2)
        motion[1] = motion[0] @ Rotation.from_euler("Z", 180, degrees=True).as_matrix().T
        skeleton, clip, rotations, _ = reconstruct_animation(motion, 20, {})
        self.assertTrue(np.isfinite(rotations).all())
        np.testing.assert_allclose(evaluated_positions(skeleton, clip, 1), motion[1], atol=1e-6)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = PriorMDMConfig(repository=self.root / "repo", python=Path(sys.executable),
                                     checkpoint=self.root / "checkpoint" / "model.pt", dataset=self.root / "data",
                                     output_root=self.root / "outputs", device="cpu")
        for path in [self.config.repository / "utils/model_util.py", self.config.repository / "body_models/smpl/SMPL_NEUTRAL.pkl",
                     self.config.repository / "body_models/smpl/J_regressor_extra.npy", self.config.checkpoint,
                     self.config.dataset / "Mean.npy", self.config.dataset / "Std.npy"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        self.args = self.config.checkpoint.with_name("args.json")
        self.args.write_text(json.dumps({"dataset": "humanml", "diffusion_steps": 50}), encoding="utf-8")

    def test_prepare_is_unique_and_never_reuses_previous_output(self):
        service = MotionGenerationService(PriorMDMBackend(self.config))
        request = MotionRequest("walk 'quoted' & never run a shell", 2.5, 7)
        with patch("subprocess.check_output", return_value="revision\n"):
            first, second = service.prepare(request), service.prepare(request)
        self.assertNotEqual(first.run_dir, second.run_dir)
        data = json.loads((first.run_dir / "request.json").read_text(encoding="utf-8"))
        self.assertEqual(data["frames"], 50)
        self.assertEqual(data["request"]["prompt"], request.prompt)
        self.assertNotIn(request.prompt, first.command)
        self.assertEqual(data["diffusion_steps"], 50)
        self.assertEqual(data["revision"], "revision")

    def test_missing_setup_and_bad_inputs_leave_no_output(self):
        backend = PriorMDMBackend(replace(self.config, checkpoint=None))
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            backend.prepare(MotionRequest("walk"))
        with self.assertRaisesRegex(ValueError, "9.8"):
            PriorMDMBackend(self.config).prepare(MotionRequest("walk", 10))
        for request in (MotionRequest(""), MotionRequest("walk", float("nan")), MotionRequest("walk", seed=-1)):
            with self.assertRaises(ValueError):
                request.validate()
        self.assertFalse(self.config.output_root.exists())

    def test_checkpoint_steps_and_modes_are_not_silently_overridden(self):
        for fields in ({"dataset": "babel", "diffusion_steps": 50},
                       {"dataset": "humanml"},
                       {"dataset": "humanml", "diffusion_steps": 50, "inpainting_mask": "root_horizontal"}):
            self.args.write_text(json.dumps(fields), encoding="utf-8")
            with self.assertRaises(ValueError):
                checkpoint_settings(self.config.checkpoint)
        self.args.write_text(json.dumps({"dataset": "humanml", "diffusion_steps": 1000}), encoding="utf-8")
        self.assertEqual(checkpoint_settings(self.config.checkpoint)["diffusion_steps"], 1000)


class ResearchAndSetupTests(unittest.TestCase):
    def test_pdf_keeps_empty_page_and_reports_it(self):
        from pypdf import PdfWriter
        extractor = script_module("extract_pdf_reference")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "blank.pdf"
            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(200, 200)
            with path.open("wb") as output:
                writer.write(output)
            document = extractor.extract_pdf(path)
            self.assertEqual(document["page_count"], 3)
            self.assertTrue(all(page["warnings"] for page in document["pages"]))
            self.assertIn("<!-- PAGE 3 -->", extractor.render_markdown(document))
        self.assertNotIn("\x00", extractor.normalize_text("equation \x00 x \x01"))
        self.assertEqual(extractor.normalize_text("diffu-\nsion"), "diffusion")

    def test_setup_extraction_refuses_overwrite_and_traversal(self):
        from echograph.motion.backends.priormdm import installer as setup
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "assets.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("../outside.txt", "bad")
            with self.assertRaises(ValueError):
                setup.extract_new_files(archive, root / "out")
            self.assertFalse((root / "outside.txt").exists())
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("model/args.json", "{}")
            setup.extract_new_files(archive, root / "out")
            with self.assertRaises(FileExistsError):
                setup.extract_new_files(archive, root / "out")


if __name__ == "__main__":
    unittest.main()
