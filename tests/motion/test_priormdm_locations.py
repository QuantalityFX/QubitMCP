from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6 import QtWidgets

from echograph.model import GraphNode
from echograph.motion.backends.priormdm import preferences
from echograph.motion.backends.priormdm.backend import PriorMDMConfig
from nodes.priormdm.locations_ui import LocationsDialog
from nodes.priormdm.setup_ui import SetupDialog, SetupSession, session_for_node
from nodes.priormdm.spec import MotionControls, config_for_node, set_value, value

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class LocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for target, replacement in (("echograph.motion.backends.priormdm.preferences.SETTINGS_PATH", self.root / "settings.json"),
                                    ("nodes.priormdm.setup_ui._SESSIONS", {}),
                                    ("nodes.priormdm.setup_ui._INSTALLING", {})):
            context = patch(target, replacement)
            context.start()
            self.addCleanup(context.stop)
        self.node = self.make_node("Motion")
        self.session = session_for_node(self.node)
        self.dialog = SetupDialog(self.session, self.node)
        self.addCleanup(self.dialog.deleteLater)

    def make_node(self, name):
        node = GraphNode(name=name, kind="priormdm", params=[])
        for key, path in preferences.default_paths().items():
            set_value(node, None, key, path)
        return node

    def test_optional_paths_persist_for_new_nodes_and_keep_existing_nodes_unchanged(self):
        other = self.make_node("Existing")
        original = config_for_node(other)
        self.assertIs(session_for_node(other), self.session)
        controls = MotionControls(self.node)
        self.addCleanup(controls.deleteLater)
        self.dialog.locations_button.click()
        chooser = self.dialog.locations_dialog
        self.assertTrue(chooser.remember.isChecked())
        cache = self.root / "shared-cache"
        dataset = self.root / "humanml"
        chooser.edits["download_cache"].setText(str(cache))
        chooser.edits["dataset"].setText(str(dataset))
        with patch.object(SetupSession, "start") as start:
            chooser.apply_button.click()
            start.assert_called_once_with(install=False)
        self.assertEqual(chooser.result(), QtWidgets.QDialog.Accepted)
        saved = json.loads(preferences.SETTINGS_PATH.read_text())
        self.assertEqual(saved["download_cache"], str(cache))
        new_node = GraphNode(name="New", kind="priormdm", params=[])
        self.assertEqual(config_for_node(new_node).cache_directory, cache)
        self.assertEqual(config_for_node(new_node).dataset, dataset)
        # Node parameters survive a graph JSON round trip.
        restored = GraphNode(name="Restored", kind="priormdm", params=json.loads(json.dumps(self.node.params)))
        self.assertEqual(config_for_node(restored), config_for_node(self.node))
        self.assertEqual(config_for_node(other), original)
        self.assertEqual(controls.paths["dataset"].text(), str(dataset))
        self.assertIs(controls._setup_session, self.dialog.session)
        # Finishing an old shared check may update its other node, never this one.
        self.session.finished.emit({"dataset": str(original.dataset)})
        self.assertEqual(config_for_node(self.node).dataset, dataset)
        command = self.dialog.session.command(self.root / "report.json", True)
        self.assertEqual(command[command.index("--download-cache") + 1], str(cache))
        self.assertEqual(command[command.index("--dataset") + 1], str(dataset))

    def test_node_only_choice_does_not_change_app_preferences(self):
        chooser = LocationsDialog(self.dialog)
        chooser.remember.setChecked(False)
        chooser.edits["dataset"].setText(str(self.root / "node-data"))
        with patch.object(SetupSession, "start"):
            chooser.apply_button.click()
        self.assertEqual(config_for_node(self.node).dataset, self.root / "node-data")
        self.assertFalse(preferences.SETTINGS_PATH.exists())
        self.assertEqual(Path(preferences.default_paths()["dataset"]), PriorMDMConfig().dataset)

    def test_cancel_and_restore_are_non_destructive_and_repository_updates_managed_paths(self):
        original = config_for_node(self.node)
        chooser = LocationsDialog(self.dialog)
        chooser.edits["dataset"].setText(str(self.root / "temporary-choice"))
        chooser.reset_button.click()
        self.assertEqual(chooser.edits["dataset"].text(), str(PriorMDMConfig().dataset))
        chooser.reject()
        self.assertEqual(config_for_node(self.node), original)
        self.assertFalse(preferences.SETTINGS_PATH.exists())
        chooser = LocationsDialog(self.dialog)
        custom_cache = self.root / "separate-cache"
        chooser.edits["download_cache"].setText(str(custom_cache))
        new_repository = self.root / "prior"
        chooser.edits["repository"].setText(str(new_repository))
        with patch.object(SetupSession, "start"):
            chooser.apply_button.click()
        config = config_for_node(self.node)
        self.assertEqual(config.repository, new_repository)
        self.assertEqual(config.dataset, new_repository / "dataset/HumanML3D")
        self.assertEqual(config.cache_directory, custom_cache)
        self.assertTrue(config.python.is_relative_to(new_repository))
        self.assertFalse(new_repository.exists(), "Saving locations must not install or move files")

    def test_two_installations_cannot_write_the_same_custom_cache(self):
        shared = self.root / "cache"
        first = SetupSession(replace(PriorMDMConfig(), repository=self.root / "a", download_cache=shared))
        second = SetupSession(replace(PriorMDMConfig(), repository=self.root / "b", dataset=self.root / "b/data", download_cache=shared))
        with patch("nodes.priormdm.setup_ui._INSTALLING", {"first": first}):
            with self.assertRaisesRegex(ValueError, "already running"):
                second.start(install=True)

    def test_active_installation_does_not_adopt_another_nodes_custom_paths(self):
        from nodes.priormdm.setup_ui import repository_key
        other = self.make_node("Other")
        custom_data = self.root / "other-data"
        set_value(other, None, "dataset", str(custom_data))
        with patch("nodes.priormdm.setup_ui._INSTALLING", {repository_key(self.session.config.repository): self.session}):
            other_session = session_for_node(other)
            self.assertIsNot(other_session, self.session)
            self.session.finished.emit({"dataset": str(self.session.config.dataset)})
            self.assertEqual(config_for_node(other).dataset, custom_data)

    def test_installer_uses_custom_cache_and_reports_it(self):
        from echograph.motion.backends.priormdm import installer as module
        repository = self.root / "repo"
        (repository / "utils").mkdir(parents=True)
        (repository / "utils/model_util.py").touch()
        python = repository / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True)
        python.touch()
        cache, data = self.root / "external/cache", self.root / "external/data"
        report = self.root / "report.json"
        with patch.object(module, "ASSETS", ()), patch.object(module, "ensure_normalization") as ensure:
            result = module.main(["--repository", str(repository), "--download-cache", str(cache),
                                  "--dataset", str(data), "--download-assets", "--report", str(report)])
        self.assertEqual(result, 1)  # Deliberately missing model/runtime; no install performed.
        ensure.assert_called_once_with(data, cache / "humanml3d")
        self.assertEqual(json.loads(report.read_text())["download_cache"], str(cache))
        self.assertFalse((repository / "downloads").exists())


if __name__ == "__main__":
    unittest.main()
