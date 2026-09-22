from __future__ import annotations

from dataclasses import asdict
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation
from PySide6 import QtCore, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.motion.backends.handmdm import HandMDMBackend, HandMDMConfig, HandMotionRequest
from echograph.motion.backends.priormdm import PriorMDMBackend, PriorMDMConfig
from echograph.motion.combined import BodyHandBackend
from echograph.motion.conversion import reconstruct_animation
from echograph.motion.director import HandIntent, direct_hands
from echograph.motion.hands import (DIGITS, FINGER_NAMES, add_preview_hands, decode_hand_features,
                                    load_hand_archive, merge_hand_layer, export_preview_bvh)
from echograph.motion.qt_job import MotionJob
from echograph.motion.storage import load_animation
from echograph.motion.types import GenerationPlan, MotionRequest
from echograph.rigging.bvh_ingest import ingest_bvh_animation_data
from nodes.priormdm.hand_setup_ui import HandSetupDialog, HandSetupSession, show_hand_setup
from nodes.priormdm.spec import MotionControls, value
from test_priormdm import synthetic_motion, evaluated_positions

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def hand_features():
    data = np.zeros((14, 274))
    # Native rotations are inverse of the local rotations in the released SMPL-X converter.
    rotations = np.tile(np.eye(3), (14, 30, 1, 1))
    rotations[:, FINGER_NAMES.index("right_index1")] = Rotation.from_euler("z", np.linspace(0, -70, 14), degrees=True).as_matrix()
    data[:, 78:258] = rotations[..., :2, :].reshape(14, 180)
    return data


class HandConversionTests(unittest.TestCase):
    def test_feature_order_and_inverse_rotation_match_known_pose(self):
        hands = decode_hand_features(hand_features())
        index = FINGER_NAMES.index("right_index1")
        result = Rotation.from_quat(hands.rotations[-1, index]).as_matrix()
        np.testing.assert_allclose(result, Rotation.from_euler("z", 70, degrees=True).as_matrix(), atol=1e-7)
        np.testing.assert_allclose(hands.rotations[:, :15], np.tile([0, 0, 0, 1], (14, 15, 1)))
        self.assertEqual(DIGITS, ("index", "middle", "pinky", "ring", "thumb"))

    def test_malformed_or_degenerate_features_are_rejected(self):
        for values in (np.zeros((14, 274)), np.zeros((14, 180)), np.full((14, 274), np.nan)):
            with self.assertRaises(ValueError):
                decode_hand_features(values)

    def test_merge_preserves_all_body_tracks_and_other_hand(self):
        skeleton, body, _, _ = reconstruct_animation(synthetic_motion(41), 20, {})
        original = body.to_dict()
        skeleton = add_preview_hands(skeleton)
        clip = merge_hand_layer(skeleton, body, decode_hand_features(hand_features()), HandIntent("point", "right", 0.0, 2.0, 0.1))
        self.assertEqual(len(skeleton.joints), 62)
        self.assertEqual(body.to_dict(), original)
        self.assertEqual([t.to_dict() for t in clip.tracks[:22]], original["tracks"])
        self.assertFalse(any(track.joint_name.startswith("left_index") for track in clip.tracks))
        for frame in (0, 12, 40):
            np.testing.assert_allclose(evaluated_positions(skeleton, clip, frame)[:22], synthetic_motion(41)[frame], atol=1e-6)

    def test_native_timing_hold_and_blend_return(self):
        skeleton, body, _, _ = reconstruct_animation(synthetic_motion(41), 20, {})
        skeleton = add_preview_hands(skeleton)
        clip = merge_hand_layer(skeleton, body, decode_hand_features(hand_features()), HandIntent("point", "right", 0, 2, 0.1))
        keys = next(t.rotation_keys for t in clip.tracks if t.joint_name == "right_index1")
        def angle(time):
            key = min(keys, key=lambda k: abs(k.time - time))
            return Rotation.from_quat(key.value).as_euler("xyz", degrees=True)[2]
        self.assertAlmostEqual(angle(0), 0)
        self.assertAlmostEqual(angle(0.6), 70)
        self.assertAlmostEqual(angle(1.5), 70)
        self.assertAlmostEqual(angle(2.0), 0)

    def test_bvh_with_fingers_loads_in_existing_animation_pipeline(self):
        skeleton, body, _, _ = reconstruct_animation(synthetic_motion(21), 20, {})
        skeleton = add_preview_hands(skeleton)
        clip = merge_hand_layer(skeleton, body, decode_hand_features(hand_features()), HandIntent("point", "right", 0, 1, 0.1))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hands.bvh"
            export_preview_bvh(path, skeleton, clip)
            loaded = ingest_bvh_animation_data(path)
            for frame in (0, 5, 15, 20):
                expected = evaluated_positions(skeleton, clip, frame)
                actual = evaluated_positions(loaded.skeleton, loaded.clips[0], frame)
                order = [skeleton.joint_names.index(name) for name in loaded.skeleton.joint_names]
                np.testing.assert_allclose(actual, expected[order], atol=1e-6)

    def test_director_requires_supported_or_explicit_description(self):
        intent = direct_hands("Raise the left hand and point forward.", 2)
        self.assertEqual(intent.side, "left")
        self.assertIn("index finger", intent.prompt)
        self.assertEqual(direct_hands("Give a thumbs-up with both hands.", 2).side, "both")
        self.assertEqual(direct_hands("Turn left and point with the right hand.", 2).side, "right")
        self.assertEqual(direct_hands("walk", 2, description="Extend the left index finger.").side, "left")
        with self.assertRaises(ValueError):
            direct_hands("Shuffle the cards", 2)
        self.assertEqual(direct_hands("Shuffle", 2, description="Pinch with thumb and index").prompt, "Pinch with thumb and index")
        with self.assertRaises(ValueError):
            direct_hands("point", 2, start=3)

    def test_combined_collection_retains_native_archives_and_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            np.savez(root / "motion.npz", positions=synthetic_motion(21), fps=20, metadata="{}")
            np.savez(root / "hands.npz", features=hand_features(), fps=25, metadata=json.dumps({"seed": 7}))
            manifest = {"hand_archive": "hands.npz", "intent": asdict(HandIntent("point", "right", 0, 1, 0.1))}
            (root / "body_hands.json").write_text(json.dumps(manifest))
            backend = BodyHandBackend(PriorMDMBackend(PriorMDMConfig()), HandMDMConfig())
            result = backend.collect(GenerationPlan((), root, root))
            skeleton, clip = load_animation(result.animation_path)
            self.assertEqual(len(skeleton.joints), 62)
            self.assertEqual(clip.metadata["hand_source"]["seed"], 7)
            self.assertEqual(result.bvh_path.name, "preview_hands.bvh")
            self.assertTrue((root / "animation.json").is_file())
            self.assertTrue((root / "hands.npz").is_file())


class HandBackendTests(unittest.TestCase):
    def test_missing_resources_never_installs_or_creates_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = HandMDMConfig(repository=root, python=root / "no_python", checkpoint=root / "no_model", output_root=root / "outputs")
            with patch("subprocess.run", side_effect=AssertionError("No installation allowed")):
                with self.assertRaisesRegex(ValueError, "setup button"):
                    HandMDMBackend(config).prepare(HandMotionRequest("point"))
            self.assertFalse(config.output_root.exists())

    def test_prepare_keeps_prompt_out_of_command_and_uses_unique_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            checkpoint = root / "model/checkpoints/last.ckpt"
            files = [root / "src/model/gaussian.py", checkpoint]
            for normalizer in ("motion", "text"):
                files.extend(root / normalizer / name for name in ("mean.pt", "std.pt"))
            for path in files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            config = {"diffusion": {"denoiser": {"nfeats": 274},
                                     "motion_normalizer": {"base_dir": "motion"}, "text_normalizer": {"base_dir": "text"}}}
            (checkpoint.parent.parent / "config.json").write_text(json.dumps(config))
            backend = HandMDMBackend(HandMDMConfig(root, Path(sys.executable), checkpoint, root / "out", "cpu"))
            prompt = "point & 'quoted' text"
            first = backend.prepare(HandMotionRequest(prompt))
            second = backend.prepare(HandMotionRequest(prompt))
            self.assertNotEqual(first.run_dir, second.run_dir)
            self.assertNotIn(prompt, first.command)
            self.assertEqual(json.loads((first.run_dir / "request.json").read_text())["request"]["prompt"], prompt)


class PreviewProportionTests(unittest.TestCase):
    def test_tpose_is_static_grounded_and_preserves_generated_rig(self):
        from echograph.motion.conversion import create_tpose_bvh, create_apose_bvh
        for include_hands in (False, True):
            skeleton, clip, _, _ = reconstruct_animation(synthetic_motion(21), 20, {})
            if include_hands:
                skeleton = add_preview_hands(skeleton)
            with tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "preview.bvh"
                export_preview_bvh(source, skeleton, clip)
                original = source.read_bytes()
                target = create_tpose_bvh(source)
                self.assertEqual(source.read_bytes(), original)
                loaded = ingest_bvh_animation_data(target)
                self.assertEqual(loaded.clips[0].duration, 0)
                self.assertEqual(set(loaded.skeleton.joint_names), set(skeleton.joint_names))
                expected = {j.name: j.local_bind.translation for j in skeleton.joints}
                for joint in loaded.skeleton.joints:
                    np.testing.assert_allclose(joint.local_bind.translation, expected[joint.name], atol=1e-8)
                points = evaluated_positions(loaded.skeleton, loaded.clips[0], 0)
                self.assertAlmostEqual(points[:, 1].min(), 0, places=7)
                names = loaded.skeleton.joint_names
                self.assertAlmostEqual(points[names.index("left_wrist"), 1], points[names.index("left_shoulder"), 1], places=7)
                for track in loaded.clips[0].tracks:
                    for key in track.rotation_keys:
                        np.testing.assert_allclose(key.value, (0, 0, 0, 1), atol=1e-8)
                self.assertNotEqual(create_tpose_bvh(source), target)
                apose = ingest_bvh_animation_data(create_apose_bvh(source))
                self.assertEqual(apose.clips[0].duration, 0)
                self.assertEqual(apose.skeleton.joint_names, loaded.skeleton.joint_names)
                a_points = evaluated_positions(apose.skeleton, apose.clips[0], 0)
                for side, sign in (("left", 1), ("right", -1)):
                    shoulder = names.index(f"{side}_shoulder")
                    wrist = names.index(f"{side}_wrist")
                    delta = a_points[wrist] - a_points[shoulder]
                    self.assertLess(delta[1], 0)
                    self.assertAlmostEqual(sign * delta[0], -delta[1], places=7)
                    self.assertAlmostEqual(np.linalg.norm(delta), np.linalg.norm(points[wrist] - points[shoulder]), places=7)
                self.assertEqual(source.read_bytes(), original)

    def test_reference_lengths_and_native_input_are_preserved(self):
        from echograph.motion.conversion import apply_preview_proportions, PARENTS
        source = synthetic_motion(21)
        original = source.copy()
        resized, offsets, metadata = apply_preview_proportions(source, "standardman")
        np.testing.assert_array_equal(source, original)
        for i in range(1, 22):
            np.testing.assert_allclose(np.linalg.norm(resized[:, i] - resized[:, PARENTS[i]], axis=1),
                                       np.linalg.norm(offsets[i]), atol=1e-8)
        self.assertGreater(sum(np.linalg.norm(offsets[i]) for i in (3, 6, 9)), .44)
        skeleton, clip, _, _ = reconstruct_animation(resized, 20, metadata, rest_offsets=offsets)
        self.assertEqual(len(skeleton.joints), 22)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reference.bvh"
            export_preview_bvh(path, skeleton, clip)
            loaded = ingest_bvh_animation_data(path)
            self.assertEqual(len(loaded.skeleton.joints), 22)
            actual = {j.name: j.local_bind.translation for j in loaded.skeleton.joints}
            for joint in skeleton.joints:
                np.testing.assert_allclose(actual[joint.name], joint.local_bind.translation, atol=1e-8)


class HandUITests(unittest.TestCase):
    def test_popup_is_owned_by_graph_window_and_generate_disables_while_running(self):
        from nodes.priormdm.spec import open_controls
        scene = QtWidgets.QGraphicsScene()
        view = QtWidgets.QGraphicsView(scene)
        node = GraphNode(name="Test", kind="priormdm", params=[])
        item = SimpleNamespace(model=node, scene=lambda: scene)
        open_controls(item)
        dialog = node._motion_dialog
        self.assertIs(dialog.parentWidget(), view)
        self.assertEqual(dialog.windowModality(), QtCore.Qt.NonModal)
        self.assertFalse(dialog.testAttribute(QtCore.Qt.WA_QuitOnClose))
        controls = dialog.findChild(MotionControls)
        self.assertTrue(controls.generate_button.isEnabled())
        node._motion_job = SimpleNamespace(running=True)
        controls._refresh_buttons()
        self.assertFalse(controls.generate_button.isEnabled())
        self.assertFalse(controls.check_button.isEnabled())
        node._motion_job.running = False
        controls._refresh_buttons()
        self.assertTrue(controls.generate_button.isEnabled())
        dialog.close()
        view.deleteLater()

    def test_tpose_action_keeps_animation_selected(self):
        from nodes.priormdm.spec import set_value
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "preview.bvh"
            skeleton, clip, _, _ = reconstruct_animation(synthetic_motion(21), 20, {})
            export_preview_bvh(source, skeleton, clip)
            node = GraphNode(name="Test", kind="priormdm", params=[])
            set_value(node, None, "last_bvh", str(source))
            controls = MotionControls(node)
            self.assertTrue(controls.tpose_button.isEnabled())
            with patch("nodes.priormdm.spec.create_preview_node", return_value=True) as preview:
                controls.tpose_button.click()
                self.assertEqual(preview.call_count, 1)
                self.assertTrue(preview.call_args.args[2].is_file())
                controls.apose_button.click()
                self.assertEqual(preview.call_count, 2)
                self.assertIn("_apose_", preview.call_args.args[2].name)
            self.assertEqual(value(node, "last_bvh"), str(source))
            controls.deleteLater()

    def test_setup_controls_are_secondary_and_hand_setup_follows_toggle(self):
        controls = MotionControls(GraphNode(name="Hands", kind="priormdm", params=[]))
        self.assertEqual(controls.check_button.text(), "Setup PriorMDM")
        self.assertTrue(controls.hand_setup_button.isHidden())
        controls.hands_enabled.setChecked(True)
        self.assertFalse(controls.hand_setup_button.isHidden())
        for button in (controls.check_button, controls.hand_setup_button):
            self.assertFalse(button.autoDefault())
            self.assertFalse(button.isDefault())
        controls.proportions.setCurrentIndex(1)
        self.assertEqual(controls._service().backend.body.preview_profile, "standardman")
        controls.deleteLater()

    def test_setup_only_installs_after_explicit_click(self):
        session = HandSetupSession()
        node = GraphNode(name="Hands", kind="priormdm", params=[])
        with patch("nodes.priormdm.hand_setup_ui.hand_setup_session", return_value=session), patch.object(session, "start") as start:
            dialog = show_hand_setup(node)
            start.assert_not_called()
            dialog.check.click()
            start.assert_called_once_with(install=False)
            dialog.install.click()
            self.assertEqual(start.call_args.kwargs, {"install": True})
            dialog.close()
        dialog.deleteLater()
        session.deleteLater()

    def test_setup_commands_keep_installation_and_annotations_opt_in(self):
        session = HandSetupSession()
        check = session.command(Path("report.json"), False)
        install = session.command(Path("report.json"), True)
        self.assertIn("--application", check)
        self.assertNotIn("--install", check)
        self.assertIn("--install", install)
        self.assertIn("--download-models", install)
        self.assertNotIn("--download-annotations", install)
        session.deleteLater()

    def test_enabling_hands_persists_without_starting_setup(self):
        node = GraphNode(name="Hands", kind="priormdm", params=[])
        with patch.object(HandSetupSession, "start", side_effect=AssertionError("Unexpected setup")):
            controls = MotionControls(node)
            self.assertEqual(controls.generate_button.text(), "Generate body only")
            controls.hands_enabled.setChecked(True)
            self.assertEqual(controls.generate_button.text(), "Generate body + hands")
            controls.hand_side.setCurrentText("left")
            self.assertEqual(value(node, "animate_hands"), "true")
            self.assertEqual(value(node, "hand_side"), "left")
            self.assertIsInstance(controls._service().backend, BodyHandBackend)
            controls.hands_enabled.setChecked(False)
            self.assertIsInstance(controls._service().backend, PriorMDMBackend)
            controls.deleteLater()

    def test_enabling_hands_does_not_relabel_previous_body_result(self):
        from nodes.priormdm.spec import set_value
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "preview.bvh"
            path.touch()
            node = GraphNode(name="Hands", kind="priormdm", params=[])
            set_value(node, None, "last_bvh", str(path))
            controls = MotionControls(node)
            controls.hands_enabled.setChecked(True)
            self.assertEqual(controls.preview_button.text(), "Create MocapBVH (body only)")
            self.assertIn("generating again", controls.preview_button.toolTip())
            controls.deleteLater()

    def test_sequential_workers_finish_collect_once_with_their_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = GenerationPlan((sys.executable, "-c", "print('body stage')"), root, root)
            second = GenerationPlan((sys.executable, "-c", "import os; assert os.environ['HAND_STAGE']=='yes'; print('hand stage')"), root, root, {"HAND_STAGE": "yes"})
            plan = GenerationPlan(first.command, root, root, stages=(first, second))
            collect = unittest.mock.Mock(return_value="merged")
            job = MotionJob(SimpleNamespace(collect=collect), plan)
            done, failed = QtTest.QSignalSpy(job.completed), QtTest.QSignalSpy(job.failed)
            job.start()
            if not done.count():
                done.wait(7000)
            self.assertEqual((done.count(), failed.count()), (1, 0))
            collect.assert_called_once_with(plan)
            self.assertIn("hand stage", job.log_text)
            job.deleteLater()

    def test_failed_first_stage_does_not_launch_hand_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = GenerationPlan((sys.executable, "-c", "raise RuntimeError('body failed')"), root, root)
            second = GenerationPlan((sys.executable, "-c", "from pathlib import Path; Path('bad').touch()"), root, root)
            job = MotionJob(SimpleNamespace(collect=lambda _: self.fail("unexpected collect")),
                            GenerationPlan(first.command, root, root, stages=(first, second)))
            failed = QtTest.QSignalSpy(job.failed)
            job.start()
            if not failed.count():
                failed.wait(7000)
            self.assertEqual(failed.count(), 1)
            self.assertFalse((root / "bad").exists())
            job.deleteLater()

    def test_cancel_during_hand_stage_never_collects_partial_motion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = GenerationPlan((sys.executable, "-u", "-c", "print('body complete')"), root, root)
            second = GenerationPlan((sys.executable, "-u", "-c", "import time; print('HAND_RUNNING', flush=True); time.sleep(30)"), root, root)
            job = MotionJob(SimpleNamespace(collect=lambda _: self.fail("unexpected collect")),
                            GenerationPlan(first.command, root, root, stages=(first, second)))
            job.output.connect(lambda text: job.cancel() if "HAND_RUNNING" in text else None)
            failed, completed = QtTest.QSignalSpy(job.failed), QtTest.QSignalSpy(job.completed)
            job.start()
            if not failed.count():
                failed.wait(7000)
            self.assertEqual((failed.count(), completed.count()), (1, 0))
            self.assertTrue(job.cancelled)
            self.assertEqual(json.loads((root / "status.json").read_text())["status"], "cancelled")
            job.deleteLater()


if __name__ == "__main__":
    unittest.main()
