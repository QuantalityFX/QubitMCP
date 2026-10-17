from contextlib import nullcontext
from dataclasses import replace
import hashlib
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from echograph.image_generation.images import Cancelled
from echograph.image_generation.native_backend import NativeBackend, NativeService
from echograph.image_generation.native_config import (
    MODEL_ID, MODEL_REVISION, MODEL_FOLDER, NativeConfig, installed_manifest, model_directory_for_selection,
    native_versions, save_native_config, saved_native_config)
from echograph.image_generation.native_engine import NativeEngine
from echograph.image_generation.native_installer import (
    MODEL_BYTES, check_paths, check_space, download, install, model_entries, prepare_download_location,
    space_requirements, verify_file)
from echograph.image_generation.native_quality import quality_cases, write_report
from echograph.image_generation.schema import ImageRequest


class OOM(RuntimeError):
    pass


def synthetic_torch():
    return SimpleNamespace(bfloat16="bf16", Generator=lambda device: SimpleNamespace(
        manual_seed=lambda seed: (device, seed)), inference_mode=nullcontext,
        cuda=SimpleNamespace(is_available=lambda: True, is_bf16_supported=lambda: True,
            empty_cache=Mock(), reset_peak_memory_stats=Mock(), get_device_name=lambda index: "Synthetic CUDA",
            max_memory_allocated=lambda: 1234, OutOfMemoryError=OOM), version=SimpleNamespace(cuda="synthetic"))


class SyntheticPipeline:
    def __init__(self):
        self.scheduler = SimpleNamespace(config={"test": True}, timesteps=list(range(40)))
        self.enable_model_cpu_offload = Mock()
        self.enable_sequential_cpu_offload = Mock()
        self.set_progress_bar_config = Mock()
        self.maybe_free_model_hooks = Mock()
        self.return_rgb = False
        self.exception = None
        self.output_size = None

    def __call__(self, **kwargs):
        self.arguments = kwargs
        if self.exception:
            raise self.exception
        for step in range(40):
            kwargs["callback_on_step_end"](self, step, step, {})
        width, height = self.output_size or (kwargs.get("width", 128), kwargs.get("height", 64))
        image = Image.new("RGB" if self.return_rgb else "RGBA", (width, height),
                          (20, 30, 40) if self.return_rgb else (20, 30, 40, 80))
        return SimpleNamespace(images=[image])


class NativeEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = NativeConfig(Path(self.temp.name), Path(self.temp.name) / "external-models")
        self.pipeline = SyntheticPipeline()
        self.loader = Mock(return_value=self.pipeline)
        self.torch = synthetic_torch()
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {"torch": self.torch,
                   "diffusers": SimpleNamespace(QwenImage21Pipeline=SimpleNamespace(from_pretrained=self.loader))}).start()
        patch("echograph.image_generation.native_engine.installed_manifest", return_value={}).start()
        patch("echograph.image_generation.native_engine.importlib.metadata.version",
              side_effect=lambda name: native_versions()["packages"][name]).start()
        self.engine = NativeEngine(self.config)
        self.cancel = threading.Event()

    def generate(self, **kwargs):
        request = ImageRequest(prompt="Detailed product photograph", backend="native_diffusers", model_variant="bf16", **kwargs)
        return self.engine.generate(request.__dict__, Path(self.temp.name) / "outputs", "test-job", self.cancel, lambda data: None)

    def test_quality_size_seed_schedule_and_bf16_are_preserved_with_offload(self):
        result = self.generate(width=2048, height=2048, seed=42)
        self.loader.assert_called_once_with(str(self.config.model), dtype="bf16", local_files_only=True,
                                           use_safetensors=True, low_cpu_mem_usage=True)
        self.pipeline.enable_model_cpu_offload.assert_called_once()
        arguments = self.pipeline.arguments
        self.assertEqual((arguments["width"], arguments["height"], arguments["num_inference_steps"]), (2048, 2048, 40))
        self.assertEqual(arguments["generator"], ("cpu", 42))
        self.assertTrue(arguments["use_kv_cache"])
        self.assertNotIn("sigmas", arguments)
        self.assertNotIn("strength", arguments)
        with Image.open(result["image_path"]) as image:
            self.assertEqual(image.size, (2048, 2048))
            self.assertEqual(json.loads(image.info["qubit_generation"])["steps_completed"], 40)

    def test_edit_reference_order_and_native_output_resolution(self):
        red = Image.new("RGB", (160, 90), "red")
        blue = Image.new("RGB", (90, 160), "blue")
        green = Image.new("RGB", (64, 64), "green")
        result = self.generate(mode="image_edit", image=red, reference_image=blue,
                               reference_images=(green,), edit_resolution=1536,
                               edit_framing="input", edit_guidance="preserve_input")
        arguments = self.pipeline.arguments
        self.assertEqual([image.getpixel((0, 0)) for image in arguments["image"]], [(255, 0, 0), (0, 0, 255), (0, 128, 0)])
        self.assertEqual([image.size for image in arguments["image"]], [(160, 90), (90, 160), (64, 64)])
        self.assertEqual(arguments["output_resolution"], 1536)
        self.assertEqual((arguments["width"], arguments["height"]), (2048, 1152))
        self.assertIn("Edit image 1 as the base image", arguments["prompt"])
        self.assertIn("Detailed product photograph", arguments["prompt"])
        self.assertEqual(result["metadata"]["effective_prompt"], arguments["prompt"])
        self.assertEqual(result["metadata"]["conditioning_image_sizes"], [[160, 90], [90, 160], [64, 64]])
        self.assertEqual(result["metadata"]["requested_output_size"], [2048, 1152])
        self.assertEqual(result["metadata"]["base_image_index"], 1)
        with Image.open(result["image_path"]) as output:
            self.assertEqual(output.size, (2048, 1152))

    def test_adding_different_references_keeps_the_base_canvas(self):
        base = Image.new("RGB", (160, 90), "red")
        for reference in (None, Image.new("RGB", (90, 160), "blue"), Image.new("RGB", (64, 64), "green")):
            with self.subTest(reference_size=reference.size if reference else None):
                result = self.generate(mode="image_edit", image=base, reference_image=reference,
                                       edit_resolution=384, edit_framing="input")
                self.assertEqual(result["metadata"]["requested_output_size"], [512, 288])
                if reference is None:
                    self.assertIsInstance(self.pipeline.arguments["image"], Image.Image)
                    self.assertEqual(self.pipeline.arguments["prompt"], "Detailed product photograph")

    def test_portrait_base_keeps_its_orientation_with_landscape_reference(self):
        result = self.generate(mode="image_edit", image=Image.new("RGB", (90, 160), "red"),
                               reference_image=Image.new("RGB", (160, 90), "blue"),
                               edit_resolution=384, edit_framing="input")
        self.assertEqual(result["metadata"]["requested_output_size"], [288, 512])

    def test_edit_rejects_reference_sized_output_without_cropping_or_stretching(self):
        self.pipeline.output_size = (288, 512)
        with self.assertRaisesRegex(RuntimeError, "expected.*512, 288"):
            self.generate(mode="image_edit", image=Image.new("RGB", (160, 90)),
                          reference_image=Image.new("RGB", (90, 160)), edit_resolution=384, edit_framing="input")
        self.assertEqual(list((Path(self.temp.name) / "outputs").glob("*.png")), [])

    def test_default_edit_keeps_original_prompt_and_automatic_last_image_framing(self):
        result = self.generate(mode="image_edit", image=Image.new("RGB", (160, 90)),
                               reference_image=Image.new("RGB", (90, 160)),
                               reference_images=(Image.new("RGB", (64, 64)),))
        self.assertEqual(self.pipeline.arguments["prompt"], "Detailed product photograph")
        self.assertNotIn("width", self.pipeline.arguments)
        self.assertNotIn("height", self.pipeline.arguments)
        self.assertEqual(result["metadata"]["edit_guidance"], "flexible")
        self.assertEqual(result["metadata"]["edit_framing"], "auto")
        self.assertEqual(result["metadata"]["framing_image_index"], 3)
        self.assertIsNone(result["metadata"]["requested_output_size"])

    def test_reference_and_custom_framing_do_not_change_the_original_prompt(self):
        options = dict(mode="image_edit", image=Image.new("RGB", (160, 90)),
                       reference_image=Image.new("RGB", (90, 160)),
                       reference_images=(Image.new("RGB", (64, 64)),), edit_resolution=384)
        result = self.generate(**options, edit_framing="reference")
        self.assertEqual(result["metadata"]["requested_output_size"], [288, 512])
        self.assertEqual(result["metadata"]["framing_image_index"], 2)
        self.assertEqual(self.pipeline.arguments["prompt"], "Detailed product photograph")
        result = self.generate(**options, edit_framing="custom", width=640, height=352)
        self.assertEqual(result["metadata"]["requested_output_size"], [640, 352])
        self.assertEqual(self.pipeline.arguments["output_resolution"], 384)
        self.assertEqual(self.pipeline.arguments["prompt"], "Detailed product photograph")

    def test_pose_guidance_is_opt_in_and_retains_original_instruction_in_metadata(self):
        result = self.generate(mode="image_edit", image=Image.new("RGB", (160, 90)),
                               reference_image=Image.new("RGB", (90, 160)), edit_guidance="pose_guide")
        self.assertIn("pose and composition guide", self.pipeline.arguments["prompt"])
        self.assertIn("Detailed product photograph", self.pipeline.arguments["prompt"])
        self.assertEqual(result["metadata"]["prompt"], "Detailed product photograph")
        self.assertEqual(result["metadata"]["effective_prompt"], self.pipeline.arguments["prompt"])
        self.assertNotIn("width", self.pipeline.arguments)

    def test_edit_options_validate_missing_reference_and_custom_dimensions(self):
        request = ImageRequest(prompt="Edit", backend="native_diffusers", model_variant="bf16", mode="image_edit", image="base")
        self.assertEqual(request.validate().edit_guidance, "flexible")
        with self.assertRaisesRegex(ValueError, "requires a reference"):
            replace(request, edit_framing="reference").validate()
        replace(request, edit_framing="reference", reference_images=("extra",)).validate()
        with self.assertRaisesRegex(ValueError, "width"):
            replace(request, edit_framing="custom", width=513).validate()
        with self.assertRaisesRegex(ValueError, "guidance"):
            replace(request, edit_guidance="unsupported").validate()
        with self.assertRaisesRegex(ValueError, "framing"):
            replace(request, edit_framing="unsupported").validate()

    def test_actual_alpha_is_preserved_and_missing_alpha_is_reported(self):
        result = self.generate(transparent=True)
        self.assertTrue(result["metadata"]["has_transparent_pixels"])
        with Image.open(result["image_path"]) as image:
            self.assertEqual(image.getchannel("A").getextrema(), (80, 80))
        self.pipeline.return_rgb = True
        result = self.generate(transparent=True)
        self.assertFalse(result["metadata"]["has_alpha"])
        self.assertIn("did not produce transparent pixels", result["metadata"]["warning"])

    def test_low_memory_uses_sequential_offload_without_size_or_step_changes(self):
        self.generate(width=1536, height=1536, low_memory=True)
        self.pipeline.enable_sequential_cpu_offload.assert_called_once()
        self.pipeline.enable_model_cpu_offload.assert_not_called()
        self.assertEqual(self.pipeline.arguments["width"], 1536)
        self.assertEqual(self.pipeline.arguments["num_inference_steps"], 40)

    def test_cancellation_releases_loaded_pipeline_and_does_not_save(self):
        def progress(data):
            if data.get("step") == 1:
                self.cancel.set()
        request = ImageRequest(prompt="Cancel", backend="native_diffusers", model_variant="bf16")
        with self.assertRaises(Cancelled):
            self.engine.generate(request.__dict__, self.config.runtime, "cancel", self.cancel, progress)
        self.assertIsNone(self.engine.pipe)
        self.torch.cuda.empty_cache.assert_called()
        self.assertFalse(list(self.config.runtime.glob("*.png")))

    def test_oom_releases_memory_without_retrying_at_lower_quality(self):
        self.pipeline.exception = OOM("synthetic")
        with self.assertRaisesRegex(RuntimeError, "not changed automatically"):
            self.generate()
        self.assertIsNone(self.engine.pipe)
        self.assertEqual(self.loader.call_count, 1)
        self.torch.cuda.empty_cache.assert_called()

    def test_native_cfg_requires_negative_prompt_and_limits_references(self):
        request = ImageRequest(prompt="Image", backend="native_diffusers", model_variant="bf16", cfg=4)
        with self.assertRaisesRegex(ValueError, "negative prompt"):
            request.validate()
        replace(request, negative_prompt="blur").validate()
        with self.assertRaisesRegex(ValueError, "at most 10"):
            replace(request, cfg=1, mode="image_edit", image="one", reference_images=tuple(range(10))).validate()
        with self.assertRaisesRegex(ValueError, "INT8/GGUF"):
            replace(request, cfg=1, model_variant="int8").validate()


class NativeSetupTests(unittest.TestCase):
    def test_native_pins_satisfy_published_diffusers_and_transformers_dependencies(self):
        from packaging.requirements import Requirement
        from packaging.specifiers import SpecifierSet
        from packaging.utils import canonicalize_name
        metadata = json.loads((Path(__file__).with_name("fixtures") / "native-package-metadata.json").read_text(encoding="utf-8"))
        pins = {canonicalize_name(name): version for name, version in native_versions()["packages"].items()}
        for package in metadata["packages"]:
            self.assertEqual(pins[canonicalize_name(package["name"])], package["version"],
                             "Refresh published dependency metadata when the upstream package version changes.")
            self.assertIn("3.12", SpecifierSet(package["requires_python"]))
            for text in package["requires_dist"]:
                requirement = Requirement(text)
                dependency = canonicalize_name(requirement.name)
                if dependency in pins:
                    with self.subTest(package=package["name"], dependency=dependency):
                        self.assertIn(pins[dependency], requirement.specifier,
                                      f"{package['name']} {package['version']} requires {text}; pinned {pins[dependency]}")

    def test_install_creates_named_subfolder_under_shared_models_root_and_preserves_other_models(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
            parent = Path(directory) / "MLModels"
            other = parent / "OtherModel" / "weights.bin"
            other.parent.mkdir(parents=True)
            other.write_bytes(b"user model")
            selected = model_directory_for_selection(parent)
            self.assertEqual(selected, parent.resolve() / MODEL_FOLDER)
            self.assertFalse(selected.exists())
            config = check_paths(NativeConfig(Path(directory) / "runtime", selected))
            prepare_download_location(config)
            self.assertTrue((selected / ".qubit-native-model.json").is_file())
            self.assertEqual(other.read_bytes(), b"user model")
            self.assertEqual(model_directory_for_selection(parent), selected)
            self.assertFalse((selected / MODEL_FOLDER).exists())

    def test_selecting_named_checkpoint_directly_does_not_nest_another_model_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "MLModels" / MODEL_FOLDER
            self.assertEqual(model_directory_for_selection(selected), selected.resolve())
            self.assertEqual(NativeConfig(Path(directory), selected).models_root, selected.parent)

    def test_existing_custom_checkpoint_paths_are_reused_and_not_nested(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, marker, data in (("old-checkpoint", "model_index.json", {"_class_name": "QwenImage21Pipeline"}),
                    ("interrupted-download", ".qubit-native-model.json", {"owner": "qubit-qwen-model", "model_id": MODEL_ID})):
                folder = Path(directory) / name
                folder.mkdir()
                (folder / marker).write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(model_directory_for_selection(folder), folder.resolve())
                self.assertEqual(NativeConfig(Path(directory) / "runtime", folder).models_root, folder)

    def test_model_location_persists_independently_and_old_settings_keep_their_default(self):
        with tempfile.TemporaryDirectory() as directory, patch(
                "echograph.image_generation.native_config.storage_root", return_value=Path(directory)):
            runtime = Path(directory) / "runtime"
            model = Path(directory) / "separate-model-drive"
            config = NativeConfig(runtime, model)
            save_native_config(config)
            restored = saved_native_config()
            self.assertEqual(restored.runtime, runtime)
            self.assertEqual(restored.model, model)
            self.assertEqual(restored.python, NativeConfig(runtime).python)
            settings = Path(directory) / "echograph/data/qwen_image/native-settings.json"
            settings.write_text(json.dumps({"runtime": str(runtime)}), encoding="utf-8")
            self.assertEqual(saved_native_config().model, runtime / "models/Qwen-Image-2.1")

    def test_separate_model_folder_rejects_unrelated_files_without_moving_or_deleting_them(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            model = Path(directory) / "pictures"
            model.mkdir()
            original = model / "keep.txt"
            original.write_text("My files")
            with self.assertRaisesRegex(ValueError, "unrelated files"):
                check_paths(NativeConfig(runtime, model))
            self.assertEqual(original.read_text(), "My files")
            self.assertFalse(runtime.exists())

    def test_old_install_markers_work_and_changing_model_location_requires_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            config = NativeConfig(Path(directory) / "runtime")
            config.python.parent.mkdir(parents=True)
            config.python.touch()
            config.model.mkdir(parents=True)
            payload = b'{"_class_name":"QwenImage21Pipeline"}'
            (config.model / "model_index.json").write_bytes(payload)
            marker = config.runtime / ".qubit-native-installed.json"
            data = {"model_id": MODEL_ID, "model_revision": MODEL_REVISION,
                    "packages": native_versions()["packages"], "files": [{"path": "model_index.json", "bytes": len(payload)}]}
            marker.write_text(json.dumps(data), encoding="utf-8")
            installed_manifest(config)
            changed = NativeConfig(config.runtime, Path(directory) / "external-models")
            changed.model.mkdir()
            (changed.model / "model_index.json").write_bytes(payload)
            with self.assertRaisesRegex(ValueError, "Model folder changed"):
                installed_manifest(changed)
            data["model_directory"] = str(changed.model)
            marker.write_text(json.dumps(data), encoding="utf-8")
            installed_manifest(changed)

    def test_worker_cli_receives_the_separate_model_location(self):
        from echograph.image_generation import native_worker
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            models = Path(directory) / "models with spaces"
            with patch.object(sys, "argv", ["native_worker", "--runtime", str(runtime), "--model-directory", str(models)]), \
                    patch.dict(os.environ), patch("echograph.image_generation.native_engine.NativeEngine") as engine, \
                    patch.object(native_worker, "serve"):
                native_worker.main()
            config = engine.call_args.args[0]
            self.assertEqual(config.runtime, runtime)
            self.assertEqual(config.model, models)

    def test_disk_check_combines_space_when_model_and_runtime_share_a_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            config = NativeConfig(Path(directory) / "runtime", Path(directory) / "models")
            groups = space_requirements(config)
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["required"], MODEL_BYTES + 20 * 1024**3)
            self.assertEqual([label for label, path in groups[0]["locations"]], ["runtime", "model"])

    def test_disk_check_uses_each_drive_and_identifies_the_drive_that_is_short(self):
        with tempfile.TemporaryDirectory() as directory:
            config = NativeConfig(Path(directory) / "runtime", Path(directory) / "models")
            runtime_volume = SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=1))
            model_volume = SimpleNamespace(stat=lambda: SimpleNamespace(st_dev=2))
            def usage(ancestor):
                return SimpleNamespace(free=50_300_000_000 if ancestor is runtime_volume else model_free[0])
            model_free = [40_000_000_000]
            with patch("echograph.image_generation.native_installer.nearest_existing",
                       side_effect=lambda path: runtime_volume if path == config.runtime else model_volume), \
                    patch("echograph.image_generation.native_installer.shutil.disk_usage", side_effect=usage):
                self.assertEqual(len(space_requirements(config)), 2)
                check_space(config)
                model_free[0] = 30_000_000_000
                with self.assertRaisesRegex(RuntimeError, "model:"):
                    check_space(config)

    def test_download_and_verification_use_only_the_selected_model_directory_and_reuse_valid_files(self):
        with tempfile.TemporaryDirectory() as directory:
            config = NativeConfig(Path(directory) / "runtime", Path(directory) / "selected-models")
            payloads = {"model_index.json": b'{"_class_name":"QwenImage21Pipeline"}', "LICENSE": b"notice",
                "processor/tokenizer.json": b"{}", "scheduler/scheduler_config.json": b"{}",
                "text_encoder/model.safetensors": b"encoder", "transformer/model.safetensors": b"transformer",
                "vae/model.safetensors": b"vae"}
            info = SimpleNamespace(sha=MODEL_REVISION, siblings=[SimpleNamespace(rfilename=name, size=len(data),
                lfs={"sha256": hashlib.sha256(data).hexdigest()}, blob_id="unused") for name, data in payloads.items()])
            api = Mock()
            api.model_info.return_value = info
            def fetch(repo, name, revision, local_dir):
                self.assertEqual((repo, revision, Path(local_dir)), (MODEL_ID, MODEL_REVISION, config.model))
                path = Path(local_dir) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payloads[name])
                return str(path)
            fetcher = Mock(side_effect=fetch)
            with patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(HfApi=lambda: api, hf_hub_download=fetcher)}):
                self.assertEqual(len(download(config)), len(payloads))
                self.assertEqual(fetcher.call_count, len(payloads))
                fetcher.reset_mock()
                download(config)
                fetcher.assert_not_called()
            self.assertTrue((config.model / "model_index.json").is_file())
            self.assertFalse((config.runtime / "models").exists())

    def test_setup_checks_license_before_creating_or_downloading_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "never-created"
            with self.assertRaisesRegex(ValueError, "license"):
                install(SimpleNamespace(accept_model_license=False, license_use="research", runtime=runtime))
            self.assertFalse(runtime.exists())

    def test_setup_preserves_unrelated_directories_and_existing_comfy_environment(self):
        from echograph.image_generation.config import saved_config
        with self.assertRaises(ValueError):
            check_paths(NativeConfig(saved_config().runtime))
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "keep.txt"
            marker.write_text("user file")
            with self.assertRaisesRegex(ValueError, "preserved"):
                check_paths(NativeConfig(Path(directory)))
            self.assertEqual(marker.read_text(), "user file")

    def test_setup_checks_available_disk_before_install(self):
        with tempfile.TemporaryDirectory() as directory, patch(
                "echograph.image_generation.native_installer.shutil.disk_usage", return_value=SimpleNamespace(free=1024)):
            with self.assertRaisesRegex(RuntimeError, "free space"):
                check_space(NativeConfig(Path(directory)))

    def test_model_verification_checks_content_including_same_size_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weight.safetensors"
            path.write_bytes(b"good")
            entry = {"bytes": 4, "sha256": hashlib.sha256(b"good").hexdigest()}
            self.assertTrue(verify_file(path, entry))
            path.write_bytes(b"evil")
            self.assertFalse(verify_file(path, entry))

    def test_hub_manifest_uses_pinned_components_and_ignores_unneeded_assets(self):
        names = ["model_index.json", "LICENSE", "processor/tokenizer.json", "scheduler/scheduler_config.json",
                 "text_encoder/model.safetensors", "transformer/diffusion_pytorch_model.safetensors",
                 "vae/diffusion_pytorch_model.safetensors", "assets/picture.png"]
        info = SimpleNamespace(siblings=[SimpleNamespace(rfilename=name, size=10, lfs=None, blob_id="blob") for name in names])
        entries = model_entries(info)
        self.assertNotIn("assets/picture.png", [entry["path"] for entry in entries])
        self.assertEqual(len(entries), 7)

    def test_quality_plan_requires_manual_review_and_has_full_resolution_and_edit_cases(self):
        cases = quality_cases(edit_image="input.png", reference_image="reference.png")
        self.assertEqual(len(cases), 7)
        self.assertTrue(all(case["request"]["steps"] == 40 for case in cases))
        self.assertEqual((cases[0]["request"]["width"], cases[0]["request"]["height"]), (2048, 2048))
        self.assertTrue(next(case for case in cases if case["name"] == "rgba")["request"]["transparent"])
        with tempfile.TemporaryDirectory() as directory:
            report = {"visual_review": "pending", "quality_approved": False,
                      "cases": [dict(case, status="planned; not generated") for case in cases]}
            write_report(Path(directory), report)
            saved = json.loads((Path(directory) / "report.json").read_text())
            self.assertFalse(saved["quality_approved"])
            self.assertIn("Manual review pending", (Path(directory) / "review.html").read_text(encoding="utf-8"))


class NativeTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = NativeConfig(Path(self.temp.name))
        self.service = NativeService(command=[sys.executable, "-u", str(Path(__file__).with_name("native_worker_fixture.py"))])
        self.service.start(self.config)
        self.addCleanup(self.service.stop)
        self.backend = NativeBackend(self.service)
        self.request = ImageRequest(prompt="Synthetic transport test", backend="native_diffusers", model_variant="bf16", seed=42)

    def test_real_subprocess_protocol_saves_alpha_and_recovers_completed_job_without_resubmission(self):
        check_progress = []
        inventory = self.backend.inventory(progress=check_progress.append)
        self.assertFalse(inventory["heavy_modules_loaded"])
        self.assertTrue(inventory["numpy_initialized_before_reader"])
        self.assertTrue(any("Checking native" in entry["message"] for entry in check_progress))
        result = self.backend.generate(self.request, self.config.runtime, threading.Event(), lambda data: None)
        self.assertEqual(result.image.getchannel("A").getextrema(), (80, 80))
        self.service.stop()
        recovered = self.backend.generate(self.request, self.config.runtime, threading.Event(), lambda data: None,
                                          resume_job_id=result.job_id)
        self.assertEqual(recovered.image_path, result.image_path)
        self.assertEqual(recovered.seed_used, 42)

    def test_two_nodes_serialize_jobs_and_cancelling_one_does_not_cancel_the_other(self):
        cancel = threading.Event()
        started = threading.Event()
        errors = []
        def first():
            try:
                self.backend.generate(self.request, self.config.runtime, cancel,
                                      lambda data: started.set() if data.get("status") == "running" else None)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(started.wait(5))
        cancel.set()
        second = self.backend.generate(replace(self.request, seed=123), self.config.runtime,
                                       threading.Event(), lambda data: None)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(second.seed_used, 123)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Cancelled)

    def test_worker_crash_surfaces_and_service_can_restart(self):
        with self.assertRaisesRegex(RuntimeError, "worker (exited|stopped)"):
            self.backend.generate(replace(self.request, prompt="__crash__"), self.config.runtime,
                                  threading.Event(), lambda data: None)
        self.service.start(self.config)
        self.assertEqual(self.backend.inventory()["variants"], ["bf16"])

    def test_legacy_windows_console_logging_does_not_block_worker_output(self):
        captured = []
        def legacy_console(line):
            line.encode("cp1252")
            captured.append(line)
        self.service.log = legacy_console
        result = self.service.request({"op": "generate", "request": {"prompt": "__unicode_log__", "seed": 42},
            "output_dir": str(self.config.runtime)}, threading.Event(), lambda data: None, timeout=5)
        self.assertTrue(Path(result["image_path"]).is_file())
        self.assertTrue(any("\\u2588" in line for line in captured))

    def test_unresponsive_cancel_terminates_only_owned_worker(self):
        inventory = self.backend.inventory()
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            # The worker PID can differ from Popen.pid for a Windows venv launcher.
            worker_handle = kernel.OpenProcess(0x00100000, False, inventory["worker_pid"])
            self.assertTrue(worker_handle)
            self.addCleanup(kernel.CloseHandle, worker_handle)
        unrelated = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.addCleanup(unrelated.stdin.close)
        self.addCleanup(unrelated.wait, timeout=5)
        self.addCleanup(unrelated.terminate)
        cancel = threading.Event()
        timer = threading.Timer(0.3, cancel.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with self.assertRaises(Cancelled):
            self.service.request({"op": "generate", "request": {"prompt": "__hang__"},
                                  "output_dir": str(self.config.runtime)}, cancel, lambda data: None, cancel_grace=0.2)
        self.assertFalse(self.service.running)
        self.assertIsNone(unrelated.poll())
        if os.name == "nt":
            self.assertEqual(kernel.WaitForSingleObject(worker_handle, 2000), 0,
                             "The owned interpreter survived its Windows venv launcher.")


if __name__ == "__main__":
    unittest.main()
