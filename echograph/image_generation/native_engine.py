"""Direct inference through the published Diffusers API (worker process only)."""
from __future__ import annotations

import gc
import importlib.metadata
import json
from pathlib import Path
import secrets
import time
import uuid

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from .images import Cancelled, normalize_image
from .native_config import MODEL_ID, MODEL_REVISION, installed_manifest, native_versions
from .schema import ImageRequest


class NativeEngine:
    def __init__(self, config):
        self.config = config
        self.pipe = None
        self.profile = None

    def check(self):
        installed_manifest(self.config)
        from .native_installer import preflight
        preflight()
        import torch
        packages = {name: importlib.metadata.version(name) for name in native_versions()["packages"]}
        for name, expected in native_versions()["packages"].items():
            if packages[name].split("+")[0] != expected:
                raise RuntimeError(f"Native {name} version is {packages[name]}; expected {expected}. Run Setup.")
        return {"backend": "native_diffusers", "variants": ["bf16"], "model_id": MODEL_ID,
                "model_revision": MODEL_REVISION, "model_directory": str(self.config.model),
                "gpu": torch.cuda.get_device_name(0), "packages": packages,
                "loaded": self.pipe is not None}

    def unload(self):
        pipe, self.pipe = self.pipe, None
        self.profile = None
        if pipe is not None:
            # A cancelled forward pass may leave CPU-offload hooks in flight.
            try:
                pipe.maybe_free_model_hooks()
            finally:
                del pipe
        gc.collect()
        import torch
        torch.cuda.empty_cache()

    def load(self, low_memory, cancel, progress):
        if self.pipe is not None and self.profile == low_memory:
            return
        if self.pipe is not None:
            self.unload()
        if cancel.is_set():
            raise Cancelled("Native generation cancelled before model loading.")
        installed_manifest(self.config)
        import torch
        from diffusers import QwenImage21Pipeline
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
            raise RuntimeError("Native BF16 requires a CUDA-capable NVIDIA GPU with BF16 support.")
        progress({"message": "Loading official BF16 model with CPU offloading...", "status": "loading"})
        try:
            pipe = QwenImage21Pipeline.from_pretrained(str(self.config.model), dtype=torch.bfloat16,
                local_files_only=True, use_safetensors=True, low_cpu_mem_usage=True)
            self.pipe = pipe
            if low_memory:
                pipe.enable_sequential_cpu_offload()
            else:
                pipe.enable_model_cpu_offload()
            # Default attention/scheduler are preserved. No uncompiled flex attention.
            pipe.set_progress_bar_config(disable=True)
            self.profile = low_memory
            if cancel.is_set():
                raise Cancelled("Native generation cancelled after model loading.")
        except Exception:
            self.unload()
            raise

    def generate(self, data, output_dir, job_id, cancel, progress):
        request = ImageRequest(**data).validate()
        if request.backend != "native_diffusers":
            raise ValueError("This worker accepts native requests only.")
        seed = secrets.randbelow(2**53) if request.seed is None else request.seed
        started = time.monotonic()
        progress({"message": "Preparing native image generation...", "status": "loading", "job_id": job_id, "seed_used": seed})
        import torch
        try:
            self.load(request.low_memory, cancel, progress)
        except torch.cuda.OutOfMemoryError as exc:
            self.unload()
            raise RuntimeError("Not enough GPU memory to load the native model. Enable Low memory or close another GPU workload. "
                               "The selected model was not changed automatically.") from exc
        load_seconds = time.monotonic() - started
        torch.cuda.reset_peak_memory_stats()
        prompt = request.prompt
        if request.transparent:
            prompt = ("This is an RGBA image with transparency. " + prompt +
                      " The image has alpha channel and the background is transparent.")
        steps_done = 0

        def step_end(pipe, step, timestep, callback_kwargs):
            nonlocal steps_done
            if cancel.is_set():
                raise Cancelled("Native generation cancelled. The model will reload for the next job.")
            steps_done = step + 1
            total = len(pipe.scheduler.timesteps)
            progress({"message": f"Generating image: step {steps_done}/{total}", "status": "running",
                      "step": steps_done, "steps": total})
            return callback_kwargs

        arguments = dict(prompt=prompt, num_inference_steps=request.steps, true_cfg_scale=request.cfg,
                         generator=torch.Generator("cpu").manual_seed(seed), use_kv_cache=True,
                         num_images_per_prompt=1, output_type="pil", callback_on_step_end=step_end)
        expected_size = (request.width, request.height)
        conditioning_sizes = []
        framing_image_index = None
        if request.negative_prompt:
            arguments["negative_prompt"] = request.negative_prompt
        if request.mode == "image_edit":
            sources = [request.image]
            if request.reference_image is not None:
                sources.append(request.reference_image)
            sources.extend(request.reference_images)
            images = [normalize_image(source) for source in sources]
            conditioning_sizes = [list(source.size) for source in images]
            arguments["image"] = images[0] if len(images) == 1 else images
            # Automatic preserves Qwen's existing last-condition framing. Other
            # modes set explicit dimensions before inference; no output is cropped.
            expected_size = None
            if request.edit_framing in ("input", "reference"):
                framing_image_index = 1 if request.edit_framing == "input" else 2
                source_width, source_height = images[framing_image_index - 1].size
                scale = request.edit_resolution / (source_width * source_height) ** 0.5
                expected_size = tuple(max(32, round(side * scale / 32) * 32) for side in (source_width, source_height))
            elif request.edit_framing == "custom":
                expected_size = (request.width, request.height)
            else:
                framing_image_index = len(images)
            if expected_size is not None:
                arguments.update(width=expected_size[0], height=expected_size[1])
            arguments["output_resolution"] = request.edit_resolution
            if request.edit_guidance != "flexible":
                guidance = ("Edit image 1 as the base image. Preserve its camera angle, framing, subject placement "
                            "and pose unless the editing instruction explicitly requests changing them."
                            if request.edit_guidance == "preserve_input" else
                            "Use image 1 as a pose and composition guide. Match its camera angle, framing, body pose "
                            "and subject placement. Create the finished appearance described in the editing instruction.")
                if len(images) > 1:
                    guidance += (" The other images are supporting references for the requested appearance, clothing, "
                                 "style or other details; use image 1 for the camera and pose.")
                prompt = guidance + "\n\nEditing instruction:\n" + prompt
                arguments["prompt"] = prompt
        else:
            arguments.update(width=request.width, height=request.height)
        if cancel.is_set():
            raise Cancelled("Native generation cancelled before inference.")
        try:
            with torch.inference_mode():
                image = self.pipe(**arguments).images[0]
            if cancel.is_set():
                raise Cancelled("Native generation cancelled before saving.")
        except Exception as exc:
            oom = isinstance(exc, torch.cuda.OutOfMemoryError)
            self.unload()
            if oom:
                raise RuntimeError("Native generation ran out of GPU memory. Memory was released. "
                                   "Enable Low memory or explicitly choose a smaller size and retry. "
                                   "Resolution, steps, and model were not changed automatically.") from exc
            raise
        if not isinstance(image, Image.Image):
            raise RuntimeError("Native pipeline returned an unsupported output instead of a PIL image.")
        if expected_size is not None and image.size != expected_size:
            raise RuntimeError(f"Native pipeline returned {image.size}, expected {expected_size}. "
                               "The image was not resized to conceal the mismatch.")
        alpha_range = image.getchannel("A").getextrema() if "A" in image.getbands() else None
        transparent = alpha_range is not None and alpha_range[0] < 255
        warnings = []
        if request.transparent and not transparent:
            warnings.append("The model did not produce transparent pixels. Try another seed or a more specific RGBA prompt.")
        if alpha_range and alpha_range[1] == 0:
            warnings.append("The generated image is fully transparent.")
        if steps_done and steps_done != request.steps:
            warnings.append(f"The checkpoint's sampling schedule ran {steps_done} steps instead of the requested {request.steps}.")
        packages = {name: importlib.metadata.version(name) for name in native_versions()["packages"]}
        metadata = {"backend": "native_diffusers", "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
                    "model_directory": str(self.config.model),
                    "model_variant": "bf16", "dtype": "bfloat16", "prompt": request.prompt,
                    "effective_prompt": prompt, "negative_prompt": request.negative_prompt, "mode": request.mode,
                    "seed_used": seed, "generator_device": "cpu", "steps_requested": request.steps,
                    "steps_completed": steps_done, "cfg": request.cfg, "width": image.width, "height": image.height,
                    "edit_resolution": request.edit_resolution, "use_kv_cache": True,
                    "conditioning_image_sizes": conditioning_sizes,
                    "requested_output_size": list(expected_size) if expected_size is not None else None,
                    "edit_guidance": request.edit_guidance, "edit_framing": request.edit_framing,
                    "framing_image_index": framing_image_index,
                    "base_image_index": 1 if request.mode == "image_edit" and request.edit_guidance == "preserve_input" else None,
                    "offload": "sequential_cpu" if request.low_memory else "model_cpu",
                    "vae_tiling": False, "reference_count": len(arguments.get("image", []))
                    if isinstance(arguments.get("image"), list) else int("image" in arguments),
                    "image_mode": image.mode, "has_alpha": alpha_range is not None,
                    "has_transparent_pixels": transparent, "alpha_range": alpha_range,
                    "elapsed_seconds": round(time.monotonic() - started, 3), "job_id": job_id,
                    "load_seconds": round(load_seconds, 3),
                    "gpu": torch.cuda.get_device_name(0), "cuda": torch.version.cuda,
                    "peak_vram_bytes": torch.cuda.max_memory_allocated(), "packages": packages,
                    "scheduler": dict(self.pipe.scheduler.config), "warning": " ".join(warnings)}
        return save_native_output(image, Path(output_dir), job_id, metadata)


def save_native_output(image, output_dir, job_id, metadata):
    output_dir.mkdir(parents=True, exist_ok=True)
    # A unique basename keeps different nodes, sessions and retries independent.
    basename = f"qwen-native-{uuid.uuid4().hex}"
    destination = output_dir / (basename + ".png")
    sidecar = destination.with_suffix(".json")
    encoded = json.dumps(metadata, ensure_ascii=False, indent=2, default=str)
    png_info = PngInfo()
    png_info.add_text("qubit_generation", encoded)
    temporary = destination.with_suffix(".png.tmp")
    temporary_metadata = sidecar.with_suffix(".json.tmp")
    try:
        image.save(temporary, format="PNG", pnginfo=png_info)
        temporary.replace(destination)
        temporary_metadata.write_text(encoded, encoding="utf-8")
        temporary_metadata.replace(sidecar)
    finally:
        temporary.unlink(missing_ok=True)
        temporary_metadata.unlink(missing_ok=True)
    return {"image_path": str(destination), "seed_used": metadata["seed_used"], "job_id": job_id, "metadata": metadata}
