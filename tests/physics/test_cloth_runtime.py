import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

import numpy as np

from echograph.physics.gpu import runtime


class RuntimeSetupTests(unittest.TestCase):
    def fake_warp(self, cuda):
        devices = [SimpleNamespace(name="Test GPU", alias="cuda:0")] if cuda else []
        return SimpleNamespace(__version__="1.18.0", float32=object(), init=lambda: None,
            get_cuda_devices=lambda: devices, get_device=lambda name: name,
            array=lambda values, **kwargs: SimpleNamespace(numpy=lambda: np.asarray(values)))

    def test_setup_reports_usable_cuda_device(self):
        output = io.StringIO()
        with patch.object(runtime.importlib, "import_module", return_value=self.fake_warp(True)), redirect_stdout(output):
            self.assertTrue(runtime.check_runtime())
        self.assertIn("CUDA available: Test GPU", output.getvalue())

    def test_cpu_only_machine_can_complete_setup(self):
        output = io.StringIO()
        with patch.object(runtime.importlib, "import_module", return_value=self.fake_warp(False)), redirect_stdout(output):
            self.assertEqual(runtime.main(), 0)
        self.assertIn("CUDA is unavailable", output.getvalue())

    def test_missing_binary_runtime_fails_setup_with_actionable_message(self):
        output = io.StringIO()
        with patch.object(runtime.importlib, "import_module", side_effect=ImportError("No module named warp")), redirect_stderr(output):
            self.assertEqual(runtime.main(), 1)
        self.assertIn("setup.bat core", output.getvalue())

    def test_main_requirements_install_warp_without_separate_file(self):
        root = Path(__file__).resolve().parents[2]
        requirements = (root / "requirements.txt").read_text(encoding="utf-8-sig")
        setup = (root / "setup.bat").read_text(encoding="utf-8-sig")
        self.assertIn("warp-lang==1.18.0", requirements.splitlines())
        self.assertFalse((root / "requirements-cloth.txt").exists())
        self.assertIn('call :check_cloth_runtime "%ROOT_PY%" || goto :fail', setup)


if __name__ == "__main__":
    unittest.main()
