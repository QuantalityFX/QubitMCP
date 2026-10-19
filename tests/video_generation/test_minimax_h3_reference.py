import base64
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
from PIL import Image

from nodes.minimax_h3_reference_video.client import Settings, build_payload, run_job
from nodes.minimax_h3_reference_video.media import inspect_media, inspect_references, parse_refs


MP4 = b"\x00\x00\x00\x18ftypisom" + bytes(16)


class Response:
    def __init__(self, body=None, status=200, content=MP4):
        self.body, self.status_code, self.content = body, status, content

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def json(self):
        return self.body

    def raise_for_status(self):
        pass

    def iter_content(self, *_):
        yield self.content


class API:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        files = kwargs.pop("files", None)
        if files:
            filename, stream = files["file"]
            kwargs["uploaded"] = (filename, stream.read())
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected network request")
        return self.responses.pop(0)


def task(status="succeeded", **kwargs):
    data = {"id": "123", "status": status}
    if status == "succeeded":
        data.update(content={"url": "https://cdn.example/output.mp4?token=private"}, usage={"output_seconds": 5})
    data.update(kwargs)
    return Response({"task": data})


class H3ReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def settings(self, **kwargs):
        return Settings(**dict({"prompt": "Follow previs motion; use image 1 for the character.",
                                "videos": ["https://assets.example/Previs.mp4"], "images": ["mm_file://character"],
                                "api_key": "test-secret", "output_dir": self.directory,
                                "base_dir": self.directory, "poll_interval": 0.001}, **kwargs))

    def test_previs_and_design_payload_has_reference_roles_for_both_models(self):
        for model in ("MiniMax-H3", "MiniMax-H3-Max"):
            with self.subTest(model=model):
                payload = build_payload(self.settings(model=model, audio=["mm_file://voice"]))
                self.assertEqual([c.get("role") for c in payload["content"]],
                                 [None, "reference_video", "reference_image", "reference_audio"])
                self.assertEqual(payload["content"][0]["type"], "text")
                self.assertEqual("extra" in payload, model.endswith("Max"))

    def test_model_prompt_ratio_and_count_errors_are_rejected(self):
        invalid = [{"prompt": " "}, {"prompt": "x" * 7001}, {"duration": 5.0}, {"duration": 16},
                   {"duration": 3}, {"model": "unknown"}, {"resolution": "480P"},
                   {"model": "MiniMax-H3-Max", "duration": 4},
                   {"model": "MiniMax-H3-Max", "resolution": "2K"}, {"ratio": "2:1"},
                   {"model": "MiniMax-H3-Max", "expansion": "balance"},
                   {"images": [f"mm_file://{i}" for i in range(10)]},
                   {"videos": [f"mm_file://{i}" for i in range(4)]},
                   {"audio": [f"mm_file://{i}" for i in range(4)]},
                   {"images": [f"mm_file://i{i}" for i in range(9)],
                    "videos": [f"mm_file://v{i}" for i in range(3)], "audio": ["mm_file://a"]},
                   {"videos": [], "images": [], "audio": []}]
        for changes in invalid:
            with self.subTest(changes=str(changes)[:100]), self.assertRaises(ValueError):
                build_payload(self.settings(**changes))

    def test_parser_preserves_uri_semicolons_order_and_case(self):
        refs = ["https://example/A.png", "https://example/a.png", "data:image/png;base64,AAAA"]
        self.assertEqual(parse_refs("\n".join(refs + refs[:1])), refs)
        self.assertEqual(parse_refs(json.dumps({"reference_videos": ["previs.mp4"]})), ["previs.mp4"])

    def test_local_image_and_data_uri_validate_dimensions_and_size(self):
        image = self.directory / "character.png"
        Image.new("RGB", (256, 256)).save(image)
        self.assertEqual(inspect_media("character.png", "image", self.directory).path, image)
        uri = "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode()
        self.assertIsNotNone(inspect_media(uri, "image", self.directory).data)
        Image.new("RGB", (255, 256)).save(image)
        with self.assertRaisesRegex(ValueError, "dimensions"):
            inspect_media(str(image), "image", self.directory)
        with self.assertRaisesRegex(ValueError, "does not exist"):
            inspect_media("missing.mp4", "video", self.directory)
        with self.assertRaisesRegex(ValueError, "Base64"):
            inspect_media("data:video/mp4;base64,!!!!", "video", self.directory)
        with image.open("wb") as stream:
            stream.truncate(30 * 1024**2 + 1)
        with self.assertRaisesRegex(ValueError, "30 MB"):
            inspect_media(str(image), "image", self.directory)

    def test_real_ffmpeg_video_codec_rate_dimensions_and_total_duration(self):
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        clip = self.directory / "previs.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=size=256x256:rate=24",
                        "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
                       check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertAlmostEqual(inspect_media(str(clip), "video", self.directory).duration, 2, places=2)
        for info, expected in (
            ("Duration: 00:00:08.00\nStream #0:0: Video: h264, yuv420p, 256x256, 24 fps", "total"),
            ("Duration: 00:00:02.00\nStream #0:0: Video: vp9, yuv420p, 256x256, 24 fps", "H.264"),
            ("Duration: 00:00:02.00\nStream #0:0: Video: h264, yuv420p, 256x256, 12 fps", "FPS"),
            ("Duration: 00:00:02.00\nStream #0:0: Video: h264, yuv420p, 128x256, 24 fps", "dimensions"),
            ("Duration: 00:00:01.00\nStream #0:0: Video: h264, yuv420p, 256x256, 24 fps", "2-15"),
        ):
            with self.subTest(expected=expected), patch("subprocess.run") as run:
                run.return_value.stderr = info
                with self.assertRaisesRegex(ValueError, expected):
                    inspect_references([], [str(clip)] * (2 if expected == "total" else 1), [], self.directory)

    def test_upload_submit_poll_download_and_metadata_exclude_secrets(self):
        image = self.directory / "character.png"
        Image.new("RGB", (256, 256)).save(image)
        api = API(Response({"file": {"file_id": "sheet"}}), Response({"task_id": "123"}),
                  task("queued"), task("running"), task())
        submitted = []
        with patch("requests.get", return_value=Response()) as download:
            result = run_job(self.settings(images=[str(image)]), session=api, submitted=submitted.append)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(submitted, ["123"])
        self.assertEqual(api.calls[0][1], "https://api.minimax.io/v1/files/upload")
        self.assertEqual(api.calls[0][2]["data"], {"purpose": "video_generation_input"})
        self.assertEqual(api.calls[1][2]["json"]["content"][2]["image_url"]["url"], "mm_file://sheet")
        self.assertTrue(all(call[0] == "GET" for call in api.calls[2:]))
        self.assertNotIn("headers", download.call_args.kwargs)
        self.assertEqual(Path(result.path).read_bytes(), MP4)
        manifest = Path(result.metadata_path).read_text(encoding="utf-8")
        self.assertNotIn("test-secret", manifest)
        self.assertNotIn("https://", manifest)
        self.assertEqual(json.loads(manifest)["usage"], {"output_seconds": 5})

    def test_invalid_later_media_prevents_all_uploads(self):
        api = API()
        image = self.directory / "image.png"
        Image.new("RGB", (256, 256)).save(image)
        result = run_job(self.settings(videos=[], images=[str(image), "missing.png"]), session=api)
        self.assertFalse(result.ok)
        self.assertEqual(api.calls, [])

    def test_resume_after_download_failure_uses_get_only_without_original_inputs(self):
        api = API(Response({"task_id": "123"}), task())
        with patch("requests.get", return_value=Response(content=b"not a video")):
            failed = run_job(self.settings(), session=api)
        self.assertFalse(failed.ok)
        self.assertEqual(failed.task_id, "123")
        self.assertFalse((self.directory / "h3_123.mp4.part").exists())
        resumed_api = API(task())
        with patch("requests.get", return_value=Response()):
            resumed = run_job(self.settings(prompt="", images=["missing.png"]), resume_id=failed.task_id, session=resumed_api)
        self.assertTrue(resumed.ok, resumed.error)
        self.assertEqual([call[0] for call in resumed_api.calls], ["GET"])

    def test_create_errors_are_never_retried_and_auth_errors_stop_polling(self):
        for status in (400, 401, 402, 422, 429, 500):
            api = API(Response({"error": {"message": "provider error"}}, status))
            result = run_job(self.settings(), session=api)
            self.assertFalse(result.ok)
            self.assertEqual(len(api.calls), 1)
            self.assertIn(str(status), result.error)
        api = API(Response({"task_id": "123"}), Response({"error": {"message": "expired key"}}, 401))
        result = run_job(self.settings(), session=api)
        self.assertEqual(result.task_id, "123")
        self.assertEqual(len(api.calls), 2)

    def test_query_rate_limit_retries_same_task_and_provider_failure_is_reported(self):
        api = API(Response({"task_id": "123"}), Response({"error": {"message": "slow down"}}, 429),
                  task("failed", error={"message": "Reference expired"}))
        result = run_job(self.settings(), session=api)
        self.assertEqual(result.task_id, "123")
        self.assertEqual(result.status, "failed")
        self.assertIn("Reference expired", result.error)
        self.assertEqual(len([call for call in api.calls if call[0] == "POST"]), 1)

    def test_stop_waiting_keeps_task_and_does_not_cancel_or_resubmit(self):
        stop = threading.Event()
        api = API(Response({"task_id": "123"}))
        result = run_job(self.settings(), session=api, stop=stop, submitted=lambda _: stop.set())
        self.assertEqual(result.status, "stopped")
        self.assertEqual(result.task_id, "123")
        self.assertEqual(len(api.calls), 1)
        self.assertTrue(Path(result.metadata_path).is_file())


if __name__ == "__main__":
    unittest.main()
