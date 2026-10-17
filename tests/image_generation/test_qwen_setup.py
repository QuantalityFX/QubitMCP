import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from echograph.image_generation import installer
from echograph.image_generation.schema import RESOURCES


class QwenSetupTests(unittest.TestCase):
    def test_q4_only_downloads_its_diffusion_and_official_shared_components(self):
        records = installer.selected_models("gguf_q4")
        self.assertEqual(len(records), 3)
        for source, spec in records:
            self.assertEqual(source["repository"], "abenzerps/Qwen-Image-2.1-Uncensored-GGUF"
                             if spec["path"].endswith(".gguf") else "Comfy-Org/Qwen-Image-2.1")
        self.assertFalse(any("qwen_image_2.1_int8_convrot" in spec["path"] for _, spec in records))

    def test_existing_valid_models_are_verified_without_downloading(self):
        data = b"shared model fixture"
        spec = {"path": "text_encoders/shared.safetensors", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        source = {"repository": "official", "revision": "pinned"}
        download = Mock()
        with tempfile.TemporaryDirectory() as folder, patch.object(installer, "selected_models", return_value=[(source, spec)]), \
                patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(hf_hub_download=download)}):
            runtime = Path(folder)
            target = runtime / "models" / spec["path"]
            target.parent.mkdir(parents=True)
            target.write_bytes(data)
            installer.download_models(runtime, "gguf_q4")
            download.assert_not_called()
            target.write_bytes(b"wrong model")
            with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
                installer.download_models(runtime, "gguf_q4")
            self.assertEqual(target.read_bytes(), b"wrong model")

    def test_checkout_refuses_to_overwrite_an_unrelated_folder(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(installer, "run") as run:
            target = Path(folder)
            (target / "user.txt").write_text("keep me")
            with self.assertRaisesRegex(RuntimeError, "existing files were preserved"):
                installer.ensure_checkout(target, "unused", "pinned")
            run.assert_not_called()
            self.assertEqual((target / "user.txt").read_text(), "keep me")

    def test_pinned_workflows_and_manifests_are_shipped_without_vendor_tree(self):
        manifest = json.loads((RESOURCES / "manifest.json").read_text())
        for relative, digest in manifest["workflows"].items():
            self.assertEqual(hashlib.sha256((RESOURCES / relative).read_bytes()).hexdigest(), digest)
        self.assertEqual(len(manifest["models"]["int8"]["files"]), 3)
        self.assertTrue((RESOURCES / "UPSTREAM_LICENSE.txt").is_file())

    def test_installer_requires_license_acknowledgement_before_any_changes(self):
        args = SimpleNamespace(accept_model_license=False)
        with patch.object(installer, "run") as run, self.assertRaisesRegex(RuntimeError, "model license"):
            installer.install(args)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
