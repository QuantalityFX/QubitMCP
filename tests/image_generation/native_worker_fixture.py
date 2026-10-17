"""Synthetic IPC fixture, never a model or image-quality validation."""
from pathlib import Path
import os
import sys
import time
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image
from echograph.image_generation.images import Cancelled
from echograph.image_generation.native_engine import save_native_output
from echograph.image_generation import native_worker


class SyntheticEngine:
    def __init__(self, config):
        self.numpy_initialized_before_reader = "numpy" in sys.modules

    def check(self):
        return {"backend": "native_diffusers", "variants": ["bf16"],
                "worker_pid": os.getpid(),
                "numpy_initialized_before_reader": self.numpy_initialized_before_reader,
                "heavy_modules_loaded": any(name in sys.modules for name in ("torch", "diffusers"))}

    def generate(self, request, output_dir, job_id, cancel, progress):
        if request["prompt"] == "__unicode_log__":
            sys.stderr.write("\u2588" * 8192 + "\n")
            sys.stderr.flush()
        if request["prompt"] == "__crash__":
            os._exit(19)
        if request["prompt"] == "__hang__":
            time.sleep(60)
        for step in range(10):
            if cancel.is_set():
                raise Cancelled("Fixture cancellation received.")
            time.sleep(0.01)
            progress({"message": f"Fixture step {step + 1}", "status": "running"})
        image = Image.new("RGBA", (64, 64), (100, 30, 20, 80))
        return save_native_output(image, Path(output_dir), job_id,
            {"seed_used": request["seed"], "job_id": job_id, "backend": "native_diffusers", "synthetic": True,
             "prompt": request["prompt"], "negative_prompt": request.get("negative_prompt", ""),
             "cfg": request.get("cfg", 1.0)})


if __name__ == "__main__":
    # Exercise the production entry point and stdout redirection as well as IPC.
    sys.argv = [__file__, "--runtime", str(Path.cwd())]
    with patch("echograph.image_generation.native_engine.NativeEngine", SyntheticEngine):
        native_worker.main()
