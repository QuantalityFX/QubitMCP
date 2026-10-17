from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import secrets
import threading
import time
import uuid

from PIL import Image
import requests

from .schema import ImageRequest, MAX_IMAGE_BYTES, RESOURCES, loopback_url


from .images import Cancelled, ImageResult, ImageGenerationBackend, normalize_image


class ComfyUIBackend:
    def __init__(self, base_url="http://127.0.0.1:8188", session=None):
        self.base_url = loopback_url(base_url)
        self.http = session or requests.Session()
        self.http.trust_env = False
        self.cancel_event = None
        self.manifest = json.loads((RESOURCES / "manifest.json").read_text(encoding="utf-8"))

    def close(self):
        self.http.close()

    def call(self, method, route, *, cancellable=True, **kwargs):
        if cancellable and self.cancel_event is not None and self.cancel_event.is_set():
            raise Cancelled("Cancelled before the next backend request.")
        response = self.http.request(method, self.base_url + route, timeout=(5, 30),
                                     allow_redirects=False, **kwargs)
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"ComfyUI {route}: HTTP {response.status_code}: {response.text[:1800]}")
        return response

    def inventory(self):
        stats = self.call("GET", "/system_stats").json()
        devices = stats.get("devices", [])
        if not any(device.get("type") == "cuda" for device in devices):
            raise RuntimeError("The local backend has no CUDA device. Check its NVIDIA driver and CUDA PyTorch install.")
        return self.call("GET", "/object_info").json()

    @staticmethod
    def check_graph(graph, inventory):
        for node in graph.values():
            kind = node["class_type"]
            if kind not in inventory:
                hint = " Run Setup for GGUF Q4 and restart the backend." if kind == "UnetLoaderGGUF" else " Update or repair the pinned local runtime."
                raise RuntimeError(f"Backend is missing {kind}.{hint}")
            fields = inventory[kind].get("input", {})
            choices = {**fields.get("required", {}), **fields.get("optional", {})}
            for field in ("unet_name", "clip_name", "vae_name"):
                if field in node["inputs"]:
                    spec = choices.get(field, [])
                    if not spec or node["inputs"][field] not in spec[0]:
                        raise RuntimeError(f"Missing backend model: {node['inputs'][field]}. Open Setup to download and verify it.")

    def build_graph(self, request, seed):
        request.validate()
        if request.backend != "comfyui":
            raise ValueError("Native jobs must use the native Diffusers provider.")
        relative = f"{request.model_variant}/{request.template}"
        data = (RESOURCES / relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != self.manifest["workflows"][relative]:
            raise RuntimeError(f"Pinned workflow checksum mismatch: {relative}")
        graph = json.loads(data)
        expected = {"1": "UNETLoader" if request.model_variant == "int8" else "UnetLoaderGGUF",
                    "2": "CLIPLoader", "3": "VAELoader", "4": "TextEncodeQwenImage21",
                    "6": "KSampler", "8": "SaveImage"}
        if request.mode == "image_edit":
            expected.update({"9": "LoadImage", "11": "QwenImage21Cache"})
            if request.reference_image is not None:
                expected["10"] = "LoadImage"
        else:
            expected["5"] = "EmptyLatentImage"
        for key, kind in expected.items():
            if graph.get(key, {}).get("class_type") != kind:
                raise RuntimeError(f"Unexpected node {key} in pinned workflow {relative}")
        prompt = request.prompt
        if request.transparent:
            prompt = "This is an RGBA image with transparency. " + prompt + " The image has an alpha channel and a transparent background."
        graph["4"]["inputs"]["prompt"] = prompt
        graph["4"]["inputs"]["resolution"] = request.edit_resolution
        graph["6"]["inputs"].update(seed=seed, steps=request.steps, cfg=request.cfg,
                                    sampler_name="euler", scheduler="simple", denoise=1.0)
        graph["8"]["inputs"]["filename_prefix"] = f"QubitField/Qwen21/{request.model_variant}"
        if request.mode != "image_edit":
            graph["5"]["inputs"].update(width=request.width, height=request.height, batch_size=1)
        if request.low_memory:
            graph["2"]["inputs"]["device"] = "cpu"
            graph["7"]["class_type"] = "VAEDecodeTiled"
            graph["7"]["inputs"].update(tile_size=256, overlap=64, temporal_size=64, temporal_overlap=8)
        return graph, hashlib.sha256(data).hexdigest()

    def upload(self, source):
        image = normalize_image(source)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        if stream.tell() > MAX_IMAGE_BYTES:
            raise ValueError("Encoded input image exceeds 64 MB.")
        result = self.call("POST", "/upload/image", files={"image": (
            f"qubit-{uuid.uuid4().hex}.png", stream.getvalue(), "image/png")},
            data={"type": "input", "overwrite": "false"}).json()
        return "/".join(filter(None, [result.get("subfolder", ""), result["name"]]))

    def cancel_pending(self, job_id):
        # ComfyUI's /interrupt is global. Only remove this exact pending job.
        queue = self.call("GET", "/queue", cancellable=False).json()
        if any(str(item[1]) == job_id for item in queue.get("queue_pending", [])):
            self.call("POST", "/queue", cancellable=False, json={"delete": [job_id]})
            return "Removed this pending job from the queue."
        return "Detached. A running server job may finish; its job ID is retained."

    def generate(self, request, output_dir, cancel, progress, timeout=3600, poll_seconds=1,
                 resume_job_id=None):
        request.validate()
        self.cancel_event = cancel
        seed = secrets.randbelow(2**53) if request.seed is None else request.seed
        progress({"status": "running", "message": "Checking local backend", "seed_used": seed})
        graph, workflow_hash = self.build_graph(request, seed)
        submitted = time.monotonic()
        job_id = resume_job_id
        if not job_id:
            inventory = self.inventory()
            self.check_graph(graph, inventory)
            if cancel.is_set():
                raise Cancelled("Cancelled before submission.")
            if request.mode == "image_edit":
                graph["9"]["inputs"]["image"] = self.upload(request.image)
                if request.reference_image is not None:
                    graph["10"]["inputs"]["image"] = self.upload(request.reference_image)
            if cancel.is_set():
                raise Cancelled("Cancelled before submission.")
            client_id = "qubitfield-" + uuid.uuid4().hex
            try:
                result = self.call("POST", "/prompt", json={"prompt": graph, "client_id": client_id}).json()
            except requests.RequestException as exc:
                raise RuntimeError(f"Submission connection lost; the backend may have queued the request (client {client_id}). Inspect ComfyUI before retrying.") from exc
            job_id = str(result.get("prompt_id", ""))
            if not job_id:
                raise RuntimeError(f"Backend rejected the workflow: {result}")
        progress({"status": "queued", "job_id": job_id, "seed_used": seed,
                  "message": f"Queued job {job_id}"})
        missing_polls = 0
        while True:
            if cancel.is_set():
                try:
                    message = self.cancel_pending(job_id)
                except Exception:
                    message = "Detached; server cancellation could not be confirmed."
                raise Cancelled(message)
            if time.monotonic() - submitted >= timeout:
                raise TimeoutError(f"Timed out waiting for {job_id}; it may still be running. Check history before retrying.")
            job = self.call("GET", "/history/" + job_id).json().get(job_id)
            if job:
                status = job.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError("ComfyUI generation failed: " + json.dumps(status.get("messages", []))[-3500:]
                                       + " Lower the resolution or use GGUF Q4 with Low memory if CUDA ran out of memory.")
                if status.get("status_str") == "success":
                    break
            queue = self.call("GET", "/queue").json()
            running = any(str(item[1]) == job_id for item in queue.get("queue_running", []))
            pending = [str(item[1]) for item in queue.get("queue_pending", [])]
            missing_polls = missing_polls + 1 if not job and not running and job_id not in pending else 0
            if missing_polls >= 10:
                raise RuntimeError(f"Job {job_id} is absent from the backend history and queue. The server may have restarted or cleared its history.")
            progress({"status": "running" if running else "queued", "job_id": job_id,
                      "message": "Generating image" if running else (
                          f"Queued (position {pending.index(job_id) + 1})" if job_id in pending else "Waiting for job history")})
            cancel.wait(poll_seconds)
        outputs = job.get("outputs", {}).get("8", {}).get("images", [])
        if not outputs:
            raise RuntimeError("Successful job has no SaveImage output at node 8.")
        item = outputs[0]
        response = self.call("GET", "/view", params={key: item.get(key, "output" if key == "type" else "")
                                                      for key in ("filename", "subfolder", "type")}, stream=True)
        payload = bytearray()
        try:
            for chunk in response.iter_content(1024 * 1024):
                if cancel.is_set():
                    raise Cancelled("Cancelled while fetching the completed image.")
                payload.extend(chunk)
                if len(payload) > MAX_IMAGE_BYTES:
                    raise ValueError("Backend output exceeds 64 MB.")
        finally:
            response.close()
        image = normalize_image(payload)
        if cancel.is_set():
            raise Cancelled("Cancelled before publishing the result.")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / ("qwen21_" + uuid.uuid4().hex + ".png")
        image.save(path, "PNG")
        metadata = {"prompt": request.prompt, "model_variant": request.model_variant, "mode": request.mode,
                    "seed_used": seed, "job_id": job_id, "width": request.width, "height": request.height,
                    "output_size": list(image.size), "steps": request.steps, "cfg": request.cfg,
                    "edit_resolution": request.edit_resolution, "transparent": request.transparent,
                    "low_memory": request.low_memory, "sampler": "euler", "scheduler": "simple", "denoise": 1.0,
                    "workflow": request.template, "workflow_sha256": workflow_hash,
                    "source_revision": self.manifest["source_revision"], "models": self.manifest["models"],
                    "elapsed_seconds": round(time.monotonic() - submitted, 2), "image_path": str(path)}
        if request.transparent and ("A" not in image.getbands() or image.getchannel("A").getextrema()[0] == 255):
            metadata["warning"] = "The generated image is opaque. Try a more explicit transparent-background prompt."
        path.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return ImageResult(image, path, seed, job_id, metadata)
