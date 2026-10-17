"""Install pinned Qwen resources into a dedicated ComfyUI environment.

Run with Python 3.12: python -m echograph.image_generation.installer --help

Setup sequences and version metadata follow the MIT-licensed 0xSojalSec/Qwen-image
reference at dc0083c0d17581f04ebbca962771e36f82f31af8. Its notice is retained in
resources/UPSTREAM_LICENSE.txt. ComfyUI itself is fetched as a separate runtime;
this module does not contain its inference implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

from .schema import LICENSE_URL, RESOURCES

MANIFEST = json.loads((RESOURCES / "manifest.json").read_text(encoding="utf-8"))


def run(command, **kwargs):
    print("+ " + subprocess.list2cmdline(list(map(str, command))), flush=True)
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def git_output(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def ensure_checkout(target, repository, revision):
    target = Path(target)
    pending = target / ".qubit-qwen-checkout.json"
    expected = {"repository": repository, "revision": revision}
    resuming = False
    if target.exists() and any(target.iterdir()):
        try:
            resuming = json.loads(pending.read_text()) == expected
        except (OSError, ValueError):
            pass
        try:
            head = git_output(target, "rev-parse", "HEAD") if (target / ".git").exists() else ""
        except subprocess.CalledProcessError:
            head = ""
        if head != revision and not (resuming and not head):
            raise RuntimeError(f"{target} is not the pinned checkout ({revision}). Use a separate installation folder; existing files were preserved.")
        if head and git_output(target, "status", "--porcelain", "--untracked-files=no"):
            raise RuntimeError(f"Tracked files have local changes in {target}; restore them before setup.")
        if head == revision:
            return
    target.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps(expected))
    if not (target / ".git").exists():
        run(["git", "init", target])
    remotes = git_output(target, "remote")
    if "origin" not in remotes.splitlines():
        run(["git", "-C", target, "remote", "add", "origin", repository])
    elif git_output(target, "remote", "get-url", "origin") != repository:
        raise RuntimeError(f"Unexpected source remote in {target}; existing files were preserved.")
    run(["git", "-C", target, "fetch", "--depth", "1", "origin", revision])
    run(["git", "-C", target, "checkout", "--detach", "FETCH_HEAD"])


def selected_models(variant):
    # Shared files always come from the official repack, including a Q4-only install.
    native = MANIFEST["models"]["int8"]
    files = [(native, spec) for spec in native["files"]
             if variant == "int8" or not spec["path"].startswith("diffusion_models/")]
    if variant == "gguf_q4":
        quantized = MANIFEST["models"]["gguf_q4"]
        files += [(quantized, spec) for spec in quantized["files"] if spec["path"].startswith("diffusion_models/")]
    return files


def valid_model(path, spec):
    if not path.is_file() or path.stat().st_size != spec["bytes"]:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == spec["sha256"]


def download_models(runtime, variant):
    # This branch is executed by the isolated runtime Python, which owns HF Hub.
    from huggingface_hub import hf_hub_download
    records = []
    for manifest, spec in selected_models(variant):
        target = runtime / "models" / spec["path"]
        if valid_model(target, spec):
            print("VERIFIED " + str(target), flush=True)
        else:
            if target.exists():
                raise RuntimeError(f"Checksum mismatch at {target}. Move the mismatched file aside before retrying.")
            target.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(target.parent).free < spec["bytes"] + 1024**3:
                raise RuntimeError(f"Not enough space to download {target.name}.")
            print("DOWNLOADING " + spec["path"], flush=True)
            # Shared staging/cache allows interruption and avoids duplicate encoder/VAE downloads.
            source = Path(hf_hub_download(repo_id=manifest["repository"], revision=manifest["revision"],
                          filename=spec.get("source", spec["path"]), local_dir=runtime / ".model-downloads"))
            if not valid_model(source, spec):
                raise RuntimeError(f"Downloaded SHA-256 mismatch: {source}")
            source.replace(target)
            print("VERIFIED " + str(target), flush=True)
        records.append({**spec, "repository": manifest["repository"], "revision": manifest["revision"]})
    return records


def install(args):
    if not args.accept_model_license:
        raise RuntimeError("Accept the Qwen model license for research/evaluation or confirm a separate commercial license: " + LICENSE_URL)
    if not (3, 10) <= sys.version_info[:2] <= (3, 13):
        raise RuntimeError("Use Python 3.12 (supported: 3.10..3.13).")
    if not shutil.which("git"):
        raise RuntimeError("Install Git for Windows and retry Setup.")
    runtime = args.runtime.expanduser().resolve()
    runtime.parent.mkdir(parents=True, exist_ok=True)
    # Do not mutate a runtime that another ComfyUI process may be using.
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8188)) == 0:
            raise RuntimeError("Stop the local service on port 8188 before installing dependencies.")
    versions = json.loads((RESOURCES / (args.variant + "-versions.json")).read_text())
    runtime_exists = runtime.exists() and any(runtime.iterdir())
    marker = runtime / ".qubit-qwen-runtime.json"
    if runtime_exists and not (marker.exists() or (runtime / ".qwen21-installer.json").exists()
                               or (runtime / ".qubit-qwen-checkout.json").exists()):
        raise RuntimeError(f"Existing runtime is not managed by Qwen setup: {runtime}. Existing files were preserved.")
    selected = selected_models(args.variant)
    remaining = sum(spec["bytes"] for _, spec in selected
                    if not (runtime / "models" / spec["path"]).is_file())
    required = max(45 * 10**9 if not runtime_exists else 0, remaining + 8 * 1024**3)
    if shutil.disk_usage(runtime.parent).free < required:
        raise RuntimeError(f"Setup needs at least {required / 10**9:.1f} GB free on {runtime.anchor}.")
    ensure_checkout(args.repository.expanduser().resolve(), MANIFEST["source_repository"], MANIFEST["source_revision"])
    ensure_checkout(runtime, versions["comfy_repository"], versions["comfy_commit"])
    marker.write_text(json.dumps({"versions": versions, "source_revision": MANIFEST["source_revision"]}), encoding="utf-8")
    python = runtime / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        # sys._base_executable avoids inheriting Qubit Field's virtual environment.
        run([getattr(sys, "_base_executable", sys.executable), "-m", "venv", runtime / ".venv"])
    pip = [python, "-m", "pip", "install"]
    run(pip + ["--upgrade", "pip"])
    run(pip + [f"{package}=={versions[package]}" for package in ("torch", "torchvision", "torchaudio")]
        + ["--index-url", versions["torch_index"]])
    requirements = []
    for line in (runtime / "requirements.txt").read_text().splitlines():
        package = line.strip().split("=")[0]
        if package in ("torch", "torchvision", "torchaudio"):
            continue
        requirements.append(f"transformers=={versions['transformers']}" if line.startswith("transformers") else line)
    req_path = runtime / ".qubit-qwen-requirements.txt"
    constraints = runtime / ".qubit-qwen-constraints.txt"
    req_path.write_text("\n".join(requirements) + "\n")
    constraints.write_text("\n".join(f"{key}=={versions[key]}" for key in ("torch", "torchvision", "torchaudio")) + "\n")
    run(pip + ["-r", req_path, "-c", constraints, "huggingface_hub>=1.0,<2"])
    if args.variant == "gguf_q4":
        ensure_checkout(runtime / "custom_nodes/ComfyUI-GGUF", versions["gguf_repository"], versions["gguf_commit"])
        run(pip + ["-c", constraints, f"gguf=={versions['gguf_python']}", "sentencepiece", "protobuf"])
    run([python, "-c", "import torch; print('Torch', torch.__version__, 'CUDA', torch.version.cuda); assert torch.cuda.is_available(), 'CUDA unavailable; check driver and PyTorch wheel'"])
    run([python, "-u", "-X", "utf8", "-m", "echograph.image_generation.installer",
         "--runtime", runtime, "--variant", args.variant, "--download-only"], cwd=Path(__file__).resolve().parents[2])
    receipt = runtime / (".qubit-qwen-" + args.variant + ".json")
    receipt.write_text(json.dumps({"source_revision": MANIFEST["source_revision"], "versions": versions,
        "models": [{**spec, "repository": source["repository"], "revision": source["revision"]} for source, spec in selected],
        "license_url": LICENSE_URL, "license_use": args.license_use, "verified_at": time.time()}, indent=2), encoding="utf-8")
    print("Setup complete. Start Local Backend, then Check Backend before generating.", flush=True)


def main():
    from .config import RuntimeConfig
    defaults = RuntimeConfig()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=defaults.repository)
    parser.add_argument("--runtime", type=Path, default=defaults.runtime)
    parser.add_argument("--variant", choices=("int8", "gguf_q4"), default="int8")
    parser.add_argument("--accept-model-license", action="store_true")
    parser.add_argument("--license-use", choices=("research", "separately_licensed"), default="research")
    parser.add_argument("--download-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.download_only:
            download_models(args.runtime, args.variant)
        else:
            install(args)
    except Exception as exc:
        print("ERROR: " + str(exc), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
