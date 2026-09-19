from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6 import QtCore, QtTest, QtWidgets

from echograph.model import GraphNode
from echograph.motion.backends.priormdm.backend import PriorMDMConfig
from echograph.motion.backends.priormdm.setup_support import find_checkpoint, inspect_setup
from nodes.priormdm.spec import build_ports, config_for_node, MotionControls, set_value, value
from nodes.priormdm.setup_ui import SetupDialog, SetupSession, active_installation, schedule_setup_offer, session_for_node

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class SetupReportTests(unittest.TestCase):
    def test_checkpoint_discovery_prefers_base_50_steps(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, settings in (("legacy", {"dataset": "humanml", "diffusion_steps": 1000}),
                                   ("fast", {"dataset": "humanml", "diffusion_steps": 50}),
                                   ("control", {"dataset": "humanml", "diffusion_steps": 50, "inpainting_mask": "root"})):
                target = root / "save" / name
                target.mkdir(parents=True)
                (target / "args.json").write_text(json.dumps(settings))
                (target / "model001.pt").touch()
            self.assertEqual(find_checkpoint(root), root / "save/fast/model001.pt")

    def test_existing_python_with_missing_packages_is_not_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "utils").mkdir()
            (root / "utils/model_util.py").touch()
            config = PriorMDMConfig(repository=root, python=Path(sys.executable), dataset=root / "data")
            failure = SimpleNamespace(returncode=1, stdout="", stderr="ModuleNotFoundError: No module named 'torch'")
            with patch("echograph.motion.backends.priormdm.setup_support.subprocess.run", return_value=failure):
                report = inspect_setup(config, check_runtime=True)
            self.assertFalse(report["dependencies_ready"])
            self.assertFalse(report["ready"])
            self.assertIn("torch", report["runtime_error"])

    def test_successful_packages_with_missing_data_is_incomplete_not_failed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "utils").mkdir()
            (root / "utils/model_util.py").touch()
            config = PriorMDMConfig(repository=root, python=Path(sys.executable), dataset=root / "data")
            result = SimpleNamespace(returncode=0, stdout=json.dumps({"cuda_available": False}), stderr="")
            with patch("echograph.motion.backends.priormdm.setup_support.subprocess.run", return_value=result):
                report = inspect_setup(config, check_runtime=True)
            self.assertTrue(report["dependencies_ready"])
            self.assertFalse(report["ready"])
            self.assertFalse(report.get("failed", False))
            self.assertTrue(any("Mean.npy" in error for error in report["missing"]))

    def test_installer_writes_structured_failure_report(self):
        from echograph.motion.backends.priormdm import installer as module
        with tempfile.TemporaryDirectory() as folder:
            report_path = Path(folder) / "report.json"
            with patch.object(module, "perform_setup", side_effect=OSError("network unavailable")):
                self.assertEqual(module.main(["--report", str(report_path)]), 2)
            report = json.loads(report_path.read_text())
            self.assertTrue(report["failed"])
            self.assertFalse(report["ready"])
            self.assertIn("network unavailable", report["error"])

    def test_retry_reuses_complete_files_without_replacing_modified_files(self):
        import zipfile
        from echograph.motion.backends.priormdm import installer as module
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "assets.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("model.pt", "downloaded model")
                zipped.writestr("args.json", "{}")
            output = root / "out"
            output.mkdir()
            model = output / "model.pt"
            model.write_text("downloaded model")
            timestamp = model.stat().st_mtime_ns
            module.extract_new_files(archive, output, resume=True)
            self.assertEqual(model.stat().st_mtime_ns, timestamp)
            self.assertTrue((output / "args.json").is_file())
            model.write_text("user changed this")
            with self.assertRaises(FileExistsError):
                module.extract_new_files(archive, output, resume=True)
            self.assertEqual(model.read_text(), "user changed this")


class FakeSession(QtCore.QObject):
    finished = QtCore.Signal(object)

    def __init__(self, ready=False):
        super().__init__()
        self.running = False
        self.installing = False
        self.dialog = None
        self.calls = []
        self.ready = ready

    def start(self, **kwargs):
        self.calls.append(kwargs)
        self.finished.emit({"ready": self.ready})


class SetupUITests(unittest.TestCase):
    def test_shared_defaults_and_custom_override_agree_for_setup_and_generation(self):
        node = GraphNode(name="Motion", kind="priormdm", params=[])
        set_value(node, None, "dataset", "")
        controls = MotionControls(node)
        self.assertTrue(controls.advanced_paths.isHidden())
        self.assertEqual(config_for_node(node).dataset, PriorMDMConfig().dataset.resolve())
        self.assertEqual(controls._service().backend.config, config_for_node(node))
        controls.advanced_button.click()
        self.assertFalse(controls.advanced_paths.isHidden())
        custom = str(Path(tempfile.gettempdir()) / "custom-humanml")
        controls.paths["dataset"].setText(custom)
        controls.paths["dataset"].editingFinished.emit()
        self.assertEqual(config_for_node(node).dataset, Path(custom).resolve())
        self.assertEqual(controls._service().backend.config, config_for_node(node))
        controls.deleteLater()

    def test_creation_offers_setup_once_without_installing(self):
        for ready in (False, True):
            with self.subTest(ready=ready):
                scene = QtWidgets.QGraphicsScene()
                item = QtWidgets.QGraphicsWidget()
                item.model = GraphNode(name="Motion", kind="priormdm", params=[])
                scene.addItem(item)
                session = FakeSession(ready)
                with patch("nodes.priormdm.setup_ui.session_for_node", return_value=session), \
                     patch("nodes.priormdm.setup_ui.SetupDialog") as dialog:
                    schedule_setup_offer(item)
                    schedule_setup_offer(item)
                    APP.processEvents()
                    self.assertEqual(session.calls, [{}])  # A read-only check, never install=True.
                    self.assertEqual(dialog.call_count, 0 if ready else 1)
                    if not ready:
                        dialog.return_value.show.assert_called_once()
                scene.clear()

    def test_detached_node_does_not_prompt(self):
        item = QtWidgets.QGraphicsWidget()
        item.model = GraphNode(name="Motion", kind="priormdm", params=[])
        with patch("nodes.priormdm.setup_ui.session_for_node") as session:
            schedule_setup_offer(item)
            APP.processEvents()
            session.assert_not_called()

    def test_install_button_starts_installer_and_disables_generation(self):
        node = GraphNode(name="Motion", kind="priormdm", params=[])
        item = QtWidgets.QGraphicsWidget()
        item.model = node
        build_ports(item)
        APP.processEvents()
        session = session_for_node(node)
        controls = MotionControls(node)
        dialog = SetupDialog(session, node)
        with patch.object(session, "start") as start:
            dialog.install_button.click()
            start.assert_called_once_with(install=True)
        session.running, session.installing = True, True
        session.changed.emit()
        self.assertFalse(dialog.install_button.isEnabled())
        self.assertFalse(controls.generate_button.isEnabled())
        self.assertTrue(dialog.cancel_button.isEnabled())
        session.running = False
        session.changed.emit()
        dialog.deleteLater()
        controls.deleteLater()

    def test_setup_result_configures_node_and_open_controls(self):
        node = GraphNode(name="Motion", kind="priormdm", params=[])
        session = session_for_node(node)
        controls = MotionControls(node)
        result = {"ready": True, "dependencies_ready": True, "checkpoint": "V:/models/prior/model.pt",
                  "python": "V:/prior/.venv/Scripts/python.exe", "dataset": "V:/motion/HumanML3D",
                  "runtime": {"cuda_available": False}}
        session.finished.emit(result)
        self.assertEqual(value(node, "checkpoint"), result["checkpoint"])
        self.assertEqual(controls.paths["checkpoint"].text(), result["checkpoint"])
        self.assertEqual(controls.device.currentText(), "cpu")
        controls.deleteLater()

    def test_process_report_exit_one_is_not_an_installation_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            session = SetupSession(PriorMDMConfig(repository=root))
            report = {"schema_version": 1, "ready": False, "dependencies_ready": True, "missing": ["Mean.npy missing"]}
            def command(path, install):
                script = "import json,sys; from pathlib import Path; Path(sys.argv[1]).write_text(sys.argv[2]); print('setup progress'); sys.exit(1)"
                return [sys.executable, "-u", "-c", script, str(path), json.dumps(report)]
            with patch("nodes.priormdm.setup_ui.PROJECT_ROOT", root), patch.object(session, "command", side_effect=command):
                spy = QtTest.QSignalSpy(session.finished)
                session.start(install=True)
                self.assertIs(active_installation(root), session)
                if spy.count() == 0:
                    spy.wait(7000)
                self.assertEqual(spy.count(), 1)
            self.assertFalse(session.report.get("failed", False))
            self.assertTrue(session.report["dependencies_ready"])
            self.assertIsNone(active_installation(root))
            self.assertIn("setup progress", session.log_path.read_text())

    def test_duplicate_installation_is_prevented(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = SetupSession(PriorMDMConfig(repository=root))
            second = SetupSession(PriorMDMConfig(repository=root))
            with patch("nodes.priormdm.setup_ui._INSTALLING", {str(root.resolve()).lower(): first}):
                # Use the same platform-normalized key as the application.
                from nodes.priormdm.setup_ui import _INSTALLING, repository_key
                _INSTALLING[repository_key(root)] = first
                with self.assertRaisesRegex(ValueError, "already running"):
                    second.start(install=True)

    def test_command_checks_read_only_then_installs_only_when_requested(self):
        config = PriorMDMConfig()
        session = SetupSession(config)
        check = session.command(Path("report.json"), False)
        self.assertEqual(check[check.index("-m") + 1], "echograph.motion.backends.priormdm.installer")
        self.assertNotIn("--install", check)
        self.assertNotIn("--download-assets", check)
        install = session.command(Path("report.json"), True)
        self.assertIn("--install", install)
        self.assertIn("--download-assets", install)

    def test_identical_new_nodes_share_one_setup_session(self):
        first = GraphNode(name="First", kind="priormdm", params=[])
        second = GraphNode(name="Second", kind="priormdm", params=[])
        self.assertIs(session_for_node(first), session_for_node(second))

    def test_failed_start_releases_install_lock_for_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            session = SetupSession(PriorMDMConfig(repository=root))
            with patch("nodes.priormdm.setup_ui.PROJECT_ROOT", root), \
                 patch.object(session, "command", return_value=[str(root / "missing.exe")]):
                spy = QtTest.QSignalSpy(session.finished)
                session.start(install=True)
                if spy.count() == 0:
                    spy.wait(7000)
                self.assertEqual(spy.count(), 1)
                self.assertTrue(session.report["failed"])
                self.assertFalse(session.running)
                self.assertIsNone(active_installation(root))


if __name__ == "__main__":
    unittest.main()
