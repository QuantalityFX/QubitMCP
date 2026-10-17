"""Install official Diffusers packages and weights in a dedicated environment.

This Qubit-authored installer uses public pip and Hugging Face Hub APIs. No
ComfyUI source, workflow, environment, conversion code, or custom node is used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .config import PROJECT_ROOT, saved_config
from .native_config import MODEL_ID, MODEL_REVISION, NativeConfig, native_versions

OWNER = ".qubit-native-runtime.json"
STAMP = ".qubit-native-installed.json"
MODEL_OWNER = ".qubit-native-model.json"
MODEL_BYTES = 33_115_613_408  # Official BF16 encoder, transformer and VAE, approximately.
COMPONENTS = ("processor/", "scheduler/", "text_encoder/", "transformer/", "vae/")


def run(command, **kwargs):
    print("Running:", " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def check_paths(config):
    target = config.runtime.expanduser().resolve()
    model = config.model.expanduser().resolve()
    legacy = saved_config()
    forbidden = (PROJECT_ROOT / ".venv", PROJECT_ROOT / "venv", legacy.repository, legacy.runtime)
    for path in forbidden:
        path = path.resolve()
        if target == path or target.is_relative_to(path) or path.is_relative_to(target):
            raise ValueError("Choose a separate native runtime folder outside the application environment and ComfyUI folders.")
        if model == path or model.is_relative_to(path) or path.is_relative_to(model):
            raise ValueError("Choose a native model folder outside the application environment and ComfyUI folders.")
    if target == PROJECT_ROOT or PROJECT_ROOT.is_relative_to(target):
        raise ValueError("Choose a dedicated native runtime directory.")
    if target.exists() and any(target.iterdir()):
        try:
            owner = json.loads((target / OWNER).read_text(encoding="utf-8"))
            if owner.get("owner") != "qubit-qwen-native":
                raise ValueError()
        except (OSError, ValueError):
            raise ValueError(f"Existing folder is not managed by native Qwen setup: {target}. Files were preserved.") from None
    if model == target or target.is_relative_to(model) or model.is_relative_to(target / ".venv") or model.is_relative_to(target / ".setup-tmp"):
        raise ValueError("Choose a dedicated model folder separate from the runtime's Python environment and setup files.")
    if model.exists() and any(model.iterdir()):
        managed_default = model == target / "models/Qwen-Image-2.1" and (target / OWNER).is_file()
        try:
            owner = json.loads((model / MODEL_OWNER).read_text(encoding="utf-8"))
            managed = owner.get("owner") == "qubit-qwen-model" and owner.get("model_id") == MODEL_ID
        except (OSError, ValueError):
            managed = False
        try:
            index = json.loads((model / "model_index.json").read_text(encoding="utf-8"))
            official = index.get("_class_name") == "QwenImage21Pipeline"
        except (OSError, ValueError):
            official = False
        if not (managed_default or managed or official):
            raise ValueError(f"Model folder contains unrelated files: {model}. Choose an empty folder or an existing Qwen 2.1 checkpoint. Files were preserved.")
    return NativeConfig(target, model)


def nearest_existing(path):
    while not path.exists():
        parent = path.parent
        if parent == path:
            raise ValueError(f"Drive or folder does not exist: {path}")
        path = parent
    return path


def space_requirements(config):
    existing = sum(p.stat().st_size for p in config.model.rglob("*.safetensors")) if config.model.exists() else 0
    partials = config.model / ".cache/huggingface/download"
    partial_bytes = sum(p.stat().st_size for p in partials.rglob("*.incomplete")) if partials.exists() else 0
    remaining = max(0, MODEL_BYTES - existing - partial_bytes)
    # Group by actual filesystem volume, including mounted directories/SUBST drives.
    # Each location has 4 GiB headroom; one volume retains the original total check.
    groups = {}
    for path, required, label in (
            (config.runtime, (3 if config.python.exists() else 12) * 1024**3 + 4 * 1024**3, "runtime"),
            (config.model, remaining + 4 * 1024**3, "model")):
        ancestor = nearest_existing(path)
        volume = ancestor.stat().st_dev
        group = groups.setdefault(volume, {"ancestor": ancestor, "required": 0, "locations": []})
        group["required"] += required
        group["locations"].append((label, str(path)))
    return list(groups.values())


def check_space(config):
    for group in space_requirements(config):
        free = shutil.disk_usage(group["ancestor"]).free
        if free < group["required"]:
            locations = ", ".join(f"{label}: {path}" for label, path in group["locations"])
            raise RuntimeError(f"Native setup needs approximately {group['required'] / 10**9:.1f} GB free "
                               f"for {locations}; {free / 10**9:.1f} GB is available on this drive. "
                               "Choose another folder or free space.")


def prepare_download_location(config):
    config.model.mkdir(parents=True, exist_ok=True)
    (config.model / MODEL_OWNER).write_text(json.dumps({"owner": "qubit-qwen-model", "model_id": MODEL_ID}), encoding="utf-8")
    # Hub metadata, incomplete weights and Xet transfer cache stay on the model drive.
    os.environ["HF_XET_CACHE"] = str(config.model / ".cache/huggingface/xet")
    os.environ["HF_XET_CHUNK_CACHE_SIZE_BYTES"] = "0"


def model_entries(info):
    entries = []
    for sibling in info.siblings:
        name = sibling.rfilename
        if name not in ("LICENSE", "README.md", "model_index.json") and not name.startswith(COMPONENTS):
            continue
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Invalid model path from Hub metadata.")
        lfs = sibling.lfs
        sha256 = lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None)
        if not sibling.size or not (sha256 or sibling.blob_id):
            raise ValueError(f"Hub did not supply verification metadata for {name}.")
        entries.append({"path": name, "bytes": sibling.size, "sha256": sha256, "git_blob": sibling.blob_id})
    names = {entry["path"] for entry in entries}
    if not {"model_index.json", "LICENSE"}.issubset(names) or not all(
            any(name.startswith(prefix) for name in names) for prefix in COMPONENTS):
        raise ValueError("Official checkpoint is missing required components.")
    return entries


def verify_file(path, entry):
    if not path.is_file() or path.stat().st_size != entry["bytes"]:
        return False
    digest = hashlib.sha256() if entry.get("sha256") else hashlib.sha1()
    if not entry.get("sha256"):
        digest.update(f"blob {entry['bytes']}\0".encode())
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == (entry.get("sha256") or entry["git_blob"])


def download(config):
    from huggingface_hub import HfApi, hf_hub_download
    info = HfApi().model_info(MODEL_ID, revision=MODEL_REVISION, files_metadata=True)
    if info.sha != MODEL_REVISION:
        raise RuntimeError("Hub revision differs from the pinned native checkpoint.")
    entries = model_entries(info)
    for index, entry in enumerate(entries):
        destination = config.model / entry["path"]
        print(f"[{index + 1}/{len(entries)}] Verifying {entry['path']}", flush=True)
        if verify_file(destination, entry):
            continue
        # Only remove a corrupt managed download, never an unrelated folder or model.
        if destination.exists():
            destination.unlink()
        hf_hub_download(MODEL_ID, entry["path"], revision=MODEL_REVISION, local_dir=str(config.model))
        if not verify_file(destination, entry):
            raise RuntimeError(f"Model verification failed: {entry['path']}. Run Setup again.")
    index = json.loads((config.model / "model_index.json").read_text(encoding="utf-8"))
    if index.get("_class_name") != "QwenImage21Pipeline":
        raise RuntimeError("Checkpoint is not the official QwenImage21Pipeline.")
    return entries


def preflight():
    import inspect
    import torch
    from diffusers import QwenImage21Pipeline
    from transformers import Qwen3VLForConditionalGeneration, Qwen3VLProcessor
    for name, expected in native_versions()["packages"].items():
        actual = importlib.metadata.version(name)
        if actual.split("+")[0] != expected:
            raise RuntimeError(f"Native {name} version is {actual}, expected {expected}. Repair the native runtime.")
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("Native BF16 requires a supported NVIDIA GPU and a working CUDA driver.")
    required = {"image", "width", "height", "true_cfg_scale", "callback_on_step_end", "output_resolution", "use_kv_cache"}
    missing = required - set(inspect.signature(QwenImage21Pipeline.__call__).parameters)
    if missing:
        raise RuntimeError(f"Installed Diffusers pipeline lacks required API: {sorted(missing)}")
    if not hasattr(QwenImage21Pipeline, "enable_model_cpu_offload"):
        raise RuntimeError("Installed Diffusers does not support CPU model offloading.")
    print(f"Native API preflight passed: {torch.cuda.get_device_name(0)}, CUDA {torch.version.cuda}", flush=True)


def install(args):
    if not args.accept_model_license or args.license_use not in ("research", "separately_licensed"):
        raise ValueError("Accept the Qwen model license and select a permitted model use before setup.")
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Native setup requires Python 3.12. Install it and retry.")
    config = check_paths(NativeConfig(args.runtime, getattr(args, "model_directory", None)))
    check_space(config)
    config.runtime.mkdir(parents=True, exist_ok=True)
    (config.runtime / OWNER).write_text(json.dumps({"owner": "qubit-qwen-native"}), encoding="utf-8")
    prepare_download_location(config)
    # Pip's temporary wheels/unpacking must use the runtime drive checked above.
    temporary = config.runtime / ".setup-tmp"
    temporary.mkdir(exist_ok=True)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(temporary)
    # An unsuccessful repair cannot leave an old 'ready' marker behind.
    (config.runtime / STAMP).unlink(missing_ok=True)
    if not config.python.exists():
        run([sys.executable, "-m", "venv", config.runtime / ".venv"])
    versions = native_versions()
    packages = versions["packages"]
    constraints = config.runtime / "qubit-native-requirements.txt"
    constraints.write_text("\n".join(f"{name}=={version}" for name, version in packages.items()) + "\n", encoding="utf-8")
    run([config.python, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
         f"torch=={packages['torch']}", f"torchvision=={packages['torchvision']}", "--index-url", versions["torch_index"]])
    run([config.python, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--constraint", constraints] +
        [f"{name}=={version}" for name, version in packages.items() if name not in ("torch", "torchvision")])
    run([config.python, "-m", "pip", "check"])
    run([config.python, "-u", "-m", "echograph.image_generation.native_installer", "--preflight"])
    # The runtime owns Hub packages; the application interpreter never needs them.
    run([config.python, "-u", "-m", "echograph.image_generation.native_installer", "--download",
         "--runtime", config.runtime, "--model-directory", config.model,
         "--accept-model-license", "--license-use", args.license_use])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--model-directory", type=Path, help="Official checkpoint folder; defaults to <runtime>/models/Qwen-Image-2.1")
    parser.add_argument("--accept-model-license", action="store_true")
    parser.add_argument("--license-use", choices=("research", "separately_licensed"))
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        preflight()
        return
    if args.runtime is None:
        parser.error("--runtime is required")
    if args.download:
        if not args.accept_model_license or not args.license_use:
            raise ValueError("Model license acknowledgement is required.")
        config = check_paths(NativeConfig(args.runtime, args.model_directory))
        prepare_download_location(config)
        entries = download(config)
        preflight()
        freeze = subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True)
        (config.runtime / "installed-requirements.txt").write_text(freeze, encoding="utf-8")
        stamp = {"model_id": MODEL_ID, "model_revision": MODEL_REVISION, "model_directory": str(config.model),
                 "packages": native_versions()["packages"],
                 "files": entries, "license_use": args.license_use, "installed_at": datetime.now(timezone.utc).isoformat()}
        (config.runtime / STAMP).write_text(json.dumps(stamp, indent=2), encoding="utf-8")
        print("Native setup complete. Start the native backend to generate an image.", flush=True)
    else:
        install(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Native setup failed: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
