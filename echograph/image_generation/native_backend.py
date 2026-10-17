"""Thread-safe host adapter and private subprocess transport for native Qwen.

The host imports neither PyTorch nor Diffusers. One retained worker serializes
GPU jobs and retains the pipeline until stopped or reconfigured.
"""
from __future__ import annotations

from dataclasses import fields, replace
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import tempfile
import threading
import time
import uuid

from PIL import Image

from .config import PROJECT_ROOT
from .images import Cancelled, ImageResult, normalize_image
from .native_config import installed_manifest


class NativeService:
    def __init__(self, log=lambda text: None, stopped=lambda: None, command=None):
        self.process = None
        self.log = log
        self.stopped = stopped
        self.command = command  # Test seam; production always uses the isolated native worker.
        self.ready = threading.Event()
        self._state = threading.RLock()
        self._write = threading.Lock()
        self._job = threading.Lock()
        self._pending = {}

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self, config):
        with self._state:
            if self.running:
                return
            if self.command is None:
                installed_manifest(config)
            environment = os.environ.copy()
            for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
                environment.pop(key, None)
            environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1",
                               HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
            command = self.command or [str(config.python), "-u", "-m", "echograph.image_generation.native_worker",
                                       "--runtime", str(config.runtime), "--model-directory", str(config.model)]
            self.ready.clear()
            process = subprocess.Popen(command, cwd=PROJECT_ROOT, env=environment, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.process = process
            threading.Thread(target=self._read, args=(process,), daemon=True).start()
            threading.Thread(target=self._logs, args=(process,), daemon=True).start()

    def _read(self, process):
        try:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    self.log("Unexpected worker output: " + line[:500])
                    continue
                if event.get("id") == "worker" and event.get("event") == "ready":
                    with self._state:
                        if self.process is not process:
                            continue
                        self.ready.set()
                    self.log("Native worker connected. Backend checks and generation are available.\n")
                with self._state:
                    destination = self._pending.get(event.get("id"))
                if destination is not None:
                    destination.put(event)
        finally:
            process.wait()
            if process.stdin:
                process.stdin.close()
            with self._state:
                # Late readers must not affect a newly started process.
                if self.process is process:
                    self.ready.clear()
                    for destination in self._pending.values():
                        destination.put({"event": "error", "message": f"Native worker exited ({process.returncode}). Check Setup logs."})
                    self.stopped()
            process.stdout.close()

    def _logs(self, process):
        try:
            for line in process.stderr:
                try:
                    self.log(line)
                except UnicodeError:
                    # Legacy Windows consoles cannot print tqdm's Unicode bars.
                    # Keep draining stderr so a full pipe cannot stall the worker.
                    self.log(line.encode("ascii", errors="backslashreplace").decode("ascii"))
        finally:
            process.stderr.close()

    def _send(self, data):
        with self._write:
            if not self.running:
                raise RuntimeError("Start the native Local Backend before generating or checking it.")
            try:
                self.process.stdin.write(json.dumps(data, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise RuntimeError("Native worker connection closed. Restart Local Backend.") from exc

    def request(self, command, cancel, progress, timeout=7200, cancel_grace=30):
        waiting = False
        while not self._job.acquire(timeout=0.1):
            if cancel.is_set():
                raise Cancelled("Cancelled while waiting for the native GPU worker.")
            if not waiting:
                progress({"message": "Waiting for another native image job...", "status": "queued"})
                waiting = True
        job_id = command.get("id") or uuid.uuid4().hex
        command = dict(command, id=job_id)
        events = queue.Queue()
        try:
            if cancel.is_set():
                raise Cancelled("Native request cancelled before submission.")
            deadline = time.monotonic() + min(timeout, 60)
            while not self.ready.wait(0.1):
                if cancel.is_set():
                    raise Cancelled("Cancelled while starting the native worker.")
                if not self.running:
                    raise RuntimeError("Start the native Local Backend first. Check Setup logs if it exited.")
                if time.monotonic() >= deadline:
                    raise RuntimeError("Native worker startup timed out. Stop and restart Local Backend.")
            with self._state:
                self._pending[job_id] = events
            self._send(command)
            deadline = time.monotonic() + timeout
            cancelled_at = None
            while True:
                if cancel.is_set() and cancelled_at is None:
                    self._send({"op": "cancel", "id": job_id})
                    cancelled_at = time.monotonic()
                    progress({"message": "Cancelling native job; waiting for the current operation...", "status": "cancelling"})
                if cancelled_at is not None and time.monotonic() - cancelled_at > cancel_grace:
                    self.stop()
                    raise Cancelled("Cancelled. The native worker was stopped to release memory; start it before the next job.")
                if time.monotonic() >= deadline:
                    self.stop()
                    raise RuntimeError("Native job timed out. The owned worker was stopped to release memory.")
                try:
                    event = events.get(timeout=0.1)
                except queue.Empty:
                    if not self.running:
                        raise RuntimeError("Native worker stopped before returning an image. Check Setup logs.")
                    continue
                if event["event"] == "progress":
                    progress(event["data"])
                elif event["event"] in ("error", "result"):
                    if cancel.is_set() or event.get("cancelled"):
                        raise Cancelled(event.get("message", "Native generation cancelled."))
                    if event["event"] == "error":
                        raise RuntimeError(event["message"])
                    return event["data"]
        finally:
            with self._state:
                self._pending.pop(job_id, None)
            self._job.release()

    def stop(self):
        with self._state:
            process = self.process
            if process is None or process.poll() is not None:
                return
            # Windows venv launchers have a child interpreter. Stop our owned
            # tree so a blocked import or GPU operation cannot outlive it.
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True, timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass
            if process.poll() is None:
                process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.stdin:
            process.stdin.close()

    @property
    def busy(self):
        return self._job.locked()


class NativeBackend:
    def __init__(self, service):
        self.service = service

    def close(self):
        pass  # The app owns the retained service; closing a job cannot stop other nodes.

    def inventory(self, cancel=None, progress=None):
        self.service.log("Checking native model files, Python libraries and CUDA...\n")
        try:
            result = self.service.request({"op": "check"}, cancel or threading.Event(),
                                          progress or (lambda data: None), timeout=120)
        except Exception as exc:
            self.service.log(f"Native backend check failed: {exc}\n")
            raise
        self.service.log(f"Native backend ready: {result.get('gpu', 'CUDA device')}; official BF16.\n")
        return result

    def generate(self, request, output_dir, cancel, progress, resume_job_id=None):
        request.validate()
        if resume_job_id:
            return self.recover(output_dir, resume_job_id)
        seed = secrets.randbelow(2**53) if request.seed is None else request.seed
        request = replace(request, seed=seed)
        job_id = uuid.uuid4().hex
        progress({"message": "Preparing native image inputs...", "status": "queued", "job_id": job_id, "seed_used": seed})
        with tempfile.TemporaryDirectory(prefix="qubit-qwen-inputs-") as temporary:
            # Do not deepcopy Qt images through dataclasses.asdict.
            data = {field.name: getattr(request, field.name) for field in fields(request)}
            for key in ("image", "reference_image"):
                source = getattr(request, key)
                data[key] = self._input(source, Path(temporary) / f"{key}.png") if source is not None else None
            data["reference_images"] = [self._input(source, Path(temporary) / f"extra-{index}.png")
                                        for index, source in enumerate(request.reference_images)]
            result = self.service.request({"op": "generate", "id": job_id, "request": data,
                                           "output_dir": str(Path(output_dir).resolve())}, cancel, progress)
        return self._result(result)

    @staticmethod
    def _input(source, destination):
        normalize_image(source).save(destination, format="PNG")
        return str(destination)

    @staticmethod
    def _result(data):
        path = Path(data["image_path"])
        with Image.open(path) as image:
            image.load()
            return ImageResult(image.copy(), path, int(data["seed_used"]), data["job_id"], data["metadata"])

    @classmethod
    def recover(cls, output_dir, job_id):
        for path in Path(output_dir).resolve().glob("qwen-native-*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("job_id") == job_id:
                    return cls._result({"image_path": str(path.with_suffix(".png")), "seed_used": data["seed_used"],
                                        "job_id": job_id, "metadata": data})
            except (OSError, ValueError, KeyError):
                continue
        raise RuntimeError("No completed native image was found for this job. An interrupted native job cannot resume; generate again.")
