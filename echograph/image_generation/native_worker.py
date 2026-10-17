"""Private NDJSON worker. Only this process loads PyTorch and Diffusers."""
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
from pathlib import Path
import queue
import sys
import threading
import traceback


def serve(engine, input_stream, output_stream):
    outgoing = threading.Lock()
    commands = queue.Queue()
    cancellations = {}
    cancellation_lock = threading.Lock()

    def emit(job_id, kind, **payload):
        with outgoing:
            output_stream.write(json.dumps({"id": job_id, "event": kind, **payload}, default=str) + "\n")
            output_stream.flush()

    def reader():
        try:
            for line in input_stream:
                if len(line) > 1024 * 1024:
                    raise ValueError("Worker command exceeds 1 MB.")
                command = json.loads(line)
                job_id = command.get("id")
                if not isinstance(job_id, str) or not job_id:
                    raise ValueError("Worker command has no request ID.")
                if command.get("op") == "cancel":
                    with cancellation_lock:
                        event = cancellations.get(job_id)
                        if event is not None:
                            event.set()
                    continue
                event = threading.Event()
                with cancellation_lock:
                    if job_id in cancellations:
                        raise ValueError("Duplicate worker request ID.")
                    cancellations[job_id] = event
                commands.put((command, event))
                if command.get("op") == "shutdown":
                    break
        except Exception:
            traceback.print_exc(file=sys.stderr)
        finally:
            with cancellation_lock:
                for event in cancellations.values():
                    event.set()
            commands.put((None, None))

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    emit("worker", "ready", protocol=1)
    while True:
        command, cancel = commands.get()
        if command is None:
            break
        job_id = command["id"]
        try:
            if command["op"] == "shutdown":
                break
            if cancel.is_set():
                raise RuntimeError("Native request cancelled before execution.")
            if command["op"] == "check":
                emit(job_id, "progress", data={"message": "Checking native Python libraries, CUDA and installed model files..."})
                result = engine.check()
            elif command["op"] == "generate":
                result = engine.generate(command["request"], command["output_dir"], job_id, cancel,
                    lambda data: emit(job_id, "progress", data=data))
            else:
                raise ValueError("Unsupported native worker operation.")
            emit(job_id, "result", data=result)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            emit(job_id, "error", message=str(exc), cancelled=cancel.is_set())
        finally:
            with cancellation_lock:
                cancellations.pop(job_id, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--model-directory", type=Path)
    args = parser.parse_args()
    # No download, telemetry, user-supplied custom pipeline, or environment reuse during generation.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from .native_config import NativeConfig
    from .native_engine import NativeEngine
    output_stream = sys.stdout
    # Library prints belong in the log, never in the NDJSON protocol.
    with contextlib.redirect_stdout(sys.stderr):
        # On Windows, importing NumPy through torch after the blocking stdin
        # reader starts can deadlock native module initialization. Initialize
        # NumPy on the main thread before serve starts that reader.
        importlib.import_module("numpy")
        serve(NativeEngine(NativeConfig(args.runtime, args.model_directory)), sys.stdin, output_stream)


if __name__ == "__main__":
    main()
