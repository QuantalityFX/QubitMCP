from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from PIL import Image

from echograph.image_generation.backend import Cancelled, ComfyUIBackend, normalize_image
from echograph.image_generation.schema import ImageRequest, RESOURCES, loopback_url


def inventory():
    result = {}
    for path in RESOURCES.glob("*/*.json"):
        graph = json.loads(path.read_text())
        for node in graph.values():
            info = result.setdefault(node["class_type"], {"input": {"required": {}}})
            for field in ("unet_name", "clip_name", "vae_name"):
                if field in node["inputs"]:
                    info["input"]["required"].setdefault(field, [[]])[0].append(node["inputs"][field])
    return result


class FakeComfy:
    def __enter__(self):
        self.prompts = {}
        self.uploads = []
        self.deletions = []
        self.routes = []
        self.complete = True
        self.error = False
        self.running = False
        self.missing_model = False
        self.output = io.BytesIO()
        Image.new("RGBA", (32, 24), (40, 80, 120, 90)).save(self.output, "PNG")
        fake = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, result, content_type="application/json"):
                data = json.dumps(result).encode() if content_type == "application/json" else result
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urlsplit(self.path).path
                fake.routes.append(path)
                if path == "/system_stats":
                    return self.respond({"devices": [{"type": "cuda", "name": "test GPU"}]})
                if path == "/object_info":
                    info = inventory()
                    if fake.missing_model:
                        info["CLIPLoader"]["input"]["required"]["clip_name"] = [[]]
                    return self.respond(info)
                if path.startswith("/history/"):
                    job_id = path.split("/")[-1]
                    if not fake.complete or job_id not in fake.prompts:
                        return self.respond({})
                    return self.respond({job_id: {"status": {"status_str": "error" if fake.error else "success",
                        "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]] if fake.error else []},
                        "outputs": {"8": {"images": [{"filename": job_id + ".png", "subfolder": "QubitField", "type": "output"}]}}}})
                if path == "/queue":
                    queue = [[i, key] for i, key in enumerate(fake.prompts)]
                    return self.respond({"queue_running": queue if fake.running else [[0, "someone-else"]],
                                         "queue_pending": [] if fake.running else queue})
                if path == "/view":
                    return self.respond(fake.output.getvalue(), "image/png")
                self.send_error(404)

            def do_POST(self):
                path = urlsplit(self.path).path
                fake.routes.append(path)
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if path == "/upload/image":
                    fake.uploads.append(body)
                    return self.respond({"name": f"input{len(fake.uploads)}.png", "subfolder": "owned-inputs"})
                data = json.loads(body)
                if path == "/prompt":
                    job_id = "job-" + str(len(fake.prompts) + 1)
                    fake.prompts[job_id] = data
                    return self.respond({"prompt_id": job_id})
                if path == "/queue":
                    fake.deletions.extend(data["delete"])
                    return self.respond({})
                self.send_error(404)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = ComfyUIBackend(f"http://127.0.0.1:{self.server.server_port}")
        return self

    def __exit__(self, *args):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class QwenBackendTests(unittest.TestCase):
    def test_all_supported_templates_keep_edit_latents_and_variant_loaders(self):
        client = ComfyUIBackend()
        self.addCleanup(client.close)
        for variant in ("int8", "gguf_q4"):
            for mode, transparent, reference in (("text_to_image", False, None), ("text_to_image", True, None),
                                                 ("image_edit", False, None), ("image_edit", False, "reference")):
                for low_memory in (False, True):
                    request = ImageRequest(prompt="Change color", model_variant=variant, mode=mode,
                        image="source" if mode == "image_edit" else None, reference_image=reference,
                        transparent=transparent, low_memory=low_memory, width=768, height=512, steps=25)
                    with self.subTest(variant=variant, mode=mode, low_memory=low_memory, reference=reference):
                        graph, _ = client.build_graph(request, 123)
                        self.assertEqual(graph["1"]["class_type"], "UNETLoader" if variant == "int8" else "UnetLoaderGGUF")
                        self.assertEqual(graph["6"]["inputs"]["seed"], 123)
                        self.assertEqual(graph["6"]["inputs"]["denoise"], 1.0)
                        if mode == "image_edit":
                            self.assertNotIn("5", graph)
                            self.assertEqual(graph["6"]["inputs"]["latent_image"], ["4", 2])
                        else:
                            self.assertEqual(graph["5"]["inputs"]["width"], 768)
                        self.assertEqual(graph["7"]["class_type"], "VAEDecodeTiled" if low_memory else "VAEDecode")
                        client.check_graph(graph, inventory())

    def test_http_generation_preserves_alpha_and_saves_metadata_for_both_variants(self):
        with FakeComfy() as fake, tempfile.TemporaryDirectory() as folder:
            results = []
            for variant in ("int8", "gguf_q4"):
                events = []
                result = fake.client.generate(ImageRequest(prompt="Transparent glass", transparent=True,
                    model_variant=variant, seed=42), Path(folder), threading.Event(), events.append)
                self.assertEqual(result.image.mode, "RGBA")
                self.assertEqual(result.image.getpixel((0, 0))[3], 90)
                self.assertEqual(result.seed_used, 42)
                self.assertEqual(events[1]["status"], "queued")
                saved = json.loads(result.image_path.with_suffix(".json").read_text())
                self.assertEqual(saved["job_id"], result.job_id)
                self.assertEqual(saved["model_variant"], variant)
                results.append(result)
            self.assertNotEqual(results[0].job_id, results[1].job_id)
            self.assertNotEqual(results[0].image_path, results[1].image_path)

    def test_two_reference_edit_uploads_both_inputs_using_server_subfolder(self):
        with FakeComfy() as fake, tempfile.TemporaryDirectory() as folder:
            result = fake.client.generate(ImageRequest(prompt="Apply this style", mode="image_edit",
                image=Image.new("RGBA", (16, 16)), reference_image=Image.new("RGB", (16, 16)), seed=7),
                folder, threading.Event(), lambda event: None)
            graph = fake.prompts[result.job_id]["prompt"]
            self.assertEqual(len(fake.uploads), 2)
            self.assertEqual(graph["9"]["inputs"]["image"], "owned-inputs/input1.png")
            self.assertEqual(graph["10"]["inputs"]["image"], "owned-inputs/input2.png")

    def test_cancel_only_deletes_this_pending_id_and_never_interrupts(self):
        for running in (False, True):
            with self.subTest(running=running), FakeComfy() as fake, tempfile.TemporaryDirectory() as folder:
                fake.complete = False
                fake.running = running
                cancelled = threading.Event()
                def progress(event):
                    if "job_id" in event:
                        cancelled.set()
                with self.assertRaises(Cancelled):
                    fake.client.generate(ImageRequest(prompt="test"), folder, cancelled, progress, poll_seconds=0)
                self.assertEqual(fake.deletions, [] if running else ["job-1"])
                self.assertNotIn("/interrupt", fake.routes)

    def test_resume_fetches_existing_job_without_submit_or_upload(self):
        with FakeComfy() as fake, tempfile.TemporaryDirectory() as folder:
            fake.prompts["old-job"] = {"prompt": {}}
            result = fake.client.generate(ImageRequest(prompt="saved", seed=34, mode="image_edit", image="uploaded"),
                folder, threading.Event(), lambda event: None, resume_job_id="old-job")
            self.assertEqual(result.job_id, "old-job")
            self.assertEqual(result.seed_used, 34)
            self.assertNotIn("/prompt", fake.routes)
            self.assertNotIn("/upload/image", fake.routes)

    def test_missing_models_fail_before_submission(self):
        with FakeComfy() as fake, tempfile.TemporaryDirectory() as folder:
            fake.missing_model = True
            with self.assertRaisesRegex(RuntimeError, "Missing backend model"):
                fake.client.generate(ImageRequest(prompt="test"), folder, threading.Event(), lambda event: None)
            self.assertFalse(fake.prompts)

    def test_failed_history_surfaces_oom(self):
        with FakeComfy() as fake, tempfile.TemporaryDirectory() as folder:
            fake.error = True
            with self.assertRaisesRegex(RuntimeError, "CUDA out of memory"):
                fake.client.generate(ImageRequest(prompt="test"), folder, threading.Event(), lambda event: None)

    def test_rejects_invalid_inputs_and_nonlocal_urls(self):
        for request in (ImageRequest(prompt=""), ImageRequest(prompt="x", width=1000), ImageRequest(prompt="x", steps=0),
                        ImageRequest(prompt="x", mode="image_edit"), ImageRequest(prompt="x", cfg=float("nan")),
                        ImageRequest(prompt="x", seed=-1)):
            with self.subTest(request=request), self.assertRaises(ValueError):
                request.validate()
        # T2I dimensions are not used to resize instruction editing latents.
        replace(ImageRequest(prompt="x"), mode="image_edit", image="x", width=123).validate()
        for url in ("http://example.com:8188", "http://0.0.0.0:8188", "https://127.0.0.1:8188", "http://user@localhost:8188", "http://localhost:8188/path"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                loopback_url(url)
        self.assertEqual(loopback_url("http://127.0.0.1:8188/"), "http://127.0.0.1:8188")

    def test_palette_transparency_survives_normalization(self):
        image = Image.new("P", (8, 8))
        image.info["transparency"] = 0
        normalized = normalize_image(image)
        self.assertEqual(normalized.mode, "RGBA")
        self.assertEqual(normalized.getpixel((0, 0))[3], 0)

    def test_two_live_qt_nodes_receive_their_own_results_and_recover_without_resubmission(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtTest, QtWidgets
        from echograph.image_generation.config import RuntimeConfig
        from echograph.image_generation.runtime import runtime_manager
        from echograph.model import GraphNode
        from echograph.ui.node_item import NodeItem
        from nodes.qwen_image.controller import put, session_for, value
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        graph = QtWidgets.QGraphicsScene()
        with FakeComfy() as fake, tempfile.TemporaryDirectory() as folder, \
                patch.object(runtime_manager(), "config", RuntimeConfig(port=fake.server.server_port)):
            nodes = []
            with patch("nodes.qwen_image.setup_ui.schedule_setup_offer"):
                for seed in (101, 202):
                    item = NodeItem(GraphNode("Qwen " + str(seed), kind="qwen_image"))
                    graph.addItem(item)
                    put(item.model, prompt="Image " + str(seed), seed=seed, random_seed="false",
                        license_accepted="true", output_directory=folder)
                    nodes.append(item)
            for item in nodes:
                session_for(item).generate()
            deadline = time.monotonic() + 10
            while any(session_for(item).running for item in nodes) and time.monotonic() < deadline:
                QtTest.QTest.qWait(10)
            app.processEvents()
            for item, seed in zip(nodes, (101, 202)):
                self.assertFalse(session_for(item).running)
                self.assertEqual(value(item.model, "status"), "success", session_for(item).message)
                self.assertEqual(value(item.model, "seed_used"), str(seed))
                job_id = value(item.model, "job_id")
                self.assertEqual(fake.prompts[job_id]["prompt"]["6"]["inputs"]["seed"], seed)
                self.assertFalse(item._plugin_proxies[0].widget().preview.pixmap.isNull())
            self.assertNotEqual(value(nodes[0].model, "job_id"), value(nodes[1].model, "job_id"))
            # Simulate a saved job after losing its result connection.
            item = nodes[0]
            put(item.model, pending_request=json.dumps({"prompt": "original", "seed": 101}), status="failed")
            session_for(item).generate(recover=True)
            deadline = time.monotonic() + 5
            while session_for(item).running and time.monotonic() < deadline:
                QtTest.QTest.qWait(10)
            self.assertEqual(value(item.model, "status"), "success", session_for(item).message)
            self.assertEqual(len(fake.prompts), 2)
            graph.clear()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
