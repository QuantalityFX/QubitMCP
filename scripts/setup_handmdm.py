r"""Prepare isolated HandMDM resources; no Qubit Field integration is performed.

Run with Qubit Field's Python 3.10, or Python 3.9 to match upstream:
  .\.venv\Scripts\python.exe -X utf8 scripts/setup_handmdm.py --install
  .\.venv\Scripts\python.exe -X utf8 scripts/setup_handmdm.py --download-models --download-annotations
  .\.venv\Scripts\python.exe -X utf8 scripts/setup_handmdm.py --references manipulation

Without action flags this only checks local resources and writes a report under
logs/. Exit codes: 0 = ready for a stock inference attempt, 2 = resources missing,
1 = setup action failed. Readiness is not proof that inference has succeeded.
The node invokes --application, which needs no annotation downloads or renderer.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT / "third_party" / "HandMDM"
LOGS = ROOT / "logs" / "FeatureAdchitecture" / "HandMDM"
MODEL_NAME = "mdm_bobsl3dt_phonology_hms"
CORE = {
    "priorMDM": "https://github.com/priorMDM/priorMDM.git",
    "HandMDM": "https://github.com/leorebensabath/HandMDM.git",
}
# These URLs are the explicit repository links in the user's hand roadmap.
# Cloning reference source does not download datasets, weights, or install it.
REFERENCES = {
    "Motion-X": "https://github.com/IDEA-Research/Motion-X.git",
    "OakInk": "https://github.com/oakink/OakInk.git",
    "OakInk2": "https://github.com/oakink/OakInk2.git",
    "OakInk2-TaMF": "https://github.com/oakink/OakInk2-TaMF.git",
    "SignAvatars": "https://github.com/ZhengdiYu/SignAvatars.git",
    "SignAvatar": "https://github.com/dongludeeplearning/SignAvatar.git",
    "InterHand2.6M": "https://github.com/facebookresearch/InterHand2.6M.git",
}
MANIPULATION = ("Motion-X", "OakInk", "OakInk2", "OakInk2-TaMF")
DOWNLOADS = {
    "models": ("1GeCAgX-tPAC8J9loeEmDSjCwjMBTLCor", Path("models")),
    "annotations": ("1GEfr1UeiOnXZnr12PmUlbu_jAqass6Yz", Path("datasets/annotations")),
}


def run(command, *, cwd=ROOT, capture=False, timeout=None):
    command = [str(part) for part in command]
    if not capture:
        print("> " + subprocess.list2cmdline(command), flush=True)
    return subprocess.run(command, cwd=cwd, check=True, capture_output=capture,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


def clone_missing(destination: Path, url: str):
    if destination.exists():
        # Do not confuse a non-repository folder with the enclosing app's Git repo.
        if not (destination / ".git").exists():
            raise RuntimeError(f"Existing folder is not a checkout: {destination}")
        origin = run(["git", "-C", destination, "remote", "get-url", "origin"],
                     capture=True).stdout.strip()
        if origin.rstrip("/").removesuffix(".git").lower() != url.removesuffix(".git").lower():
            raise RuntimeError(f"Unexpected origin at {destination}: {origin}; left unchanged.")
        print(f"Keeping existing checkout: {destination}", flush=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", url, destination])


def runtime_python() -> Path:
    return REPOSITORY / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def install_environment(base_python: Path):
    version = json.loads(run([base_python, "-c",
                             "import json,sys; print(json.dumps(list(sys.version_info[:2])))"],
                            capture=True).stdout)
    if version not in ([3, 9], [3, 10]):
        raise RuntimeError("Use Python 3.9 (upstream) or 3.10 (this workstation).")
    python = runtime_python()
    environment = REPOSITORY / ".venv"
    if not python.is_file():
        if environment.exists():
            raise RuntimeError(f"Incomplete environment: {environment}; inspect before retrying.")
        run([base_python, "-m", "venv", environment])
    actual = json.loads(run([python, "-c",
                            "import json,sys; print(json.dumps(list(sys.version_info[:2])))"],
                           capture=True).stdout)
    if actual not in ([3, 9], [3, 10]):
        raise RuntimeError(f"Existing HandMDM environment uses unsupported Python {actual}.")
    pip = [python, "-m", "pip", "install", "--retries", "1", "--timeout", "30"]
    # Keep pkg_resources for upstream CLIP and Lightning; follow PriorMDM's
    # separate-environment approach without changing its older torch packages.
    run(pip + ["pip==24.3.1", "setuptools==69.5.1", "wheel==0.43.0"])
    run(pip + ["torch==2.4.1", "torchvision==0.19.1", "--index-url",
               "https://download.pytorch.org/whl/cu118"])
    run(pip + ["--no-build-isolation", "-r", REPOSITORY / "requirements.txt", "gdown==5.2.0"])
    run([python, "-m", "pip", "check"])
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / "environment-freeze.txt").write_text(
        run([python, "-m", "pip", "freeze"], capture=True).stdout, encoding="utf-8")


def digest(path: Path) -> bytes:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.digest()


def copy_missing(source: Path, destination: Path):
    """Copy a completed download without replacing any local assets."""
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"Unexpected symbolic link in download: {path}")
        if not path.is_file():
            continue
        target = destination / path.relative_to(source)
        if target.exists():
            if not target.is_file() or digest(path) != digest(target):
                raise RuntimeError(f"Different local asset exists; left unchanged: {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as incoming, target.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)


def download_folder(name: str):
    python = runtime_python()
    if not python.is_file():
        raise RuntimeError("Run --install before downloading assets.")
    folder_id, relative = DOWNLOADS[name]
    cache = REPOSITORY / "downloads" / name
    cache.parent.mkdir(parents=True, exist_ok=True)
    # No trailing separator: gdown uses this as the folder root itself.
    run([python, "-m", "gdown", "--folder", f"https://drive.google.com/drive/folders/{folder_id}",
         "--continue", "-O", cache])
    if not cache.is_dir() or not any(p.is_file() for p in cache.rglob("*")):
        raise RuntimeError(f"No files downloaded from the official {name} folder.")
    copy_missing(cache, REPOSITORY / relative)


PROBE = r'''
import json
import sys
from pathlib import Path
import torch, torchvision, pytorch_lightning, hydra, clip
from src.model.gaussian import GaussianDiffusion
from src.data.text import TextEmbeddings
from prepare.motion_feats_to_smpl import np_feats_to_smplx
result = {"python": __import__("sys").version, "torch": torch.__version__,
          "cuda_available": torch.cuda.is_available(), "core_imports_ok": True}
try:
    if "--application" not in sys.argv:
        import inference
    result["stock_cli_import_ok"] = True
except Exception as exc:
    result["stock_cli_import_ok"] = False
    result["stock_cli_import_error"] = str(exc)
result["statistics"] = {}
for folder, width in (("motion_stats/bobsl3dt", 274), ("text_stats/bobsl3dt/clip", 512)):
    for name in ("mean", "std"):
        filename = f"{folder}/{name}.pt"
        value = torch.load(filename, map_location="cpu", weights_only=True)
        result["statistics"][filename] = {"shape": list(value.shape),
            "valid": tuple(value.shape) == (width,) and bool(torch.isfinite(value).all())
                     and (name != "std" or bool((value >= 0).all()))}
result["clip_weights_cached"] = (Path.home() / ".cache/clip/ViT-B-32.pt").is_file()
print(json.dumps(result))
'''


def inspect_resources(*, application=False) -> dict:
    missing = []
    runtime = {}
    required = [REPOSITORY / "inference.py", REPOSITORY / "requirements.txt"]
    run_dir = REPOSITORY / "models" / MODEL_NAME
    checkpoint = run_dir / "checkpoints/last.ckpt"
    config_path = run_dir / "config.json"
    required += [checkpoint, config_path]
    required += [REPOSITORY / folder / (name + ".pt")
                 for folder in ("motion_stats/bobsl3dt", "text_stats/bobsl3dt/clip")
                 for name in ("mean", "std")]
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(f"Missing or empty: {path.relative_to(ROOT)}")
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            encoder = config["data"]["text_encoder"]
            # inference.py forcibly preloads these even for a new input prompt.
            base = REPOSITORY / "datasets/annotations" / encoder["dataname"] / "text_embeddings"
            for suffix in (() if application else (".npy", "_slice.npy", "_index.json")):
                path = base / (encoder["modelname"] + suffix)
                if not path.is_file():
                    missing.append(f"Stock CLI preloaded text embeddings missing: {path}")
            for key in ("motion_normalizer", "text_normalizer"):
                normalizer = config["diffusion"][key]
                if not normalizer.get("disable", False):
                    for name in ("mean.pt", "std.pt"):
                        path = REPOSITORY / normalizer["base_dir"] / name
                        if not path.is_file():
                            missing.append(f"Checkpoint-config statistics missing: {path}")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            missing.append(f"Cannot validate checkpoint config: {exc}")
    elif not application:
        missing.append("Stock CLI text embeddings: download annotations, then validate against config.json.")
    python = runtime_python()
    if not python.is_file():
        missing.append("HandMDM isolated environment: run --install.")
    elif (REPOSITORY / "src/model/gaussian.py").is_file():
        try:
            result = run([python, "-c", PROBE] + (["--application"] if application else []),
                         cwd=REPOSITORY, capture=True, timeout=90)
            runtime = json.loads(result.stdout.strip().splitlines()[-1])
            if not runtime["stock_cli_import_ok"]:
                missing.append("Stock CLI imports: " + runtime["stock_cli_import_error"])
            if not all(item["valid"] for item in runtime["statistics"].values()):
                missing.append("Motion/text statistics have invalid shapes or values.")
        except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", None) or str(exc)
            missing.append("HandMDM runtime probe failed: " + detail[-3000:])
    return {
        "schema_version": 1, "ready": not missing,
        "mode": "application" if application else "stock_cli",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "repository": str(REPOSITORY), "python": str(python),
        "checkpoint": str(checkpoint), "runtime": runtime,
        "ready_for_stock_attempt": not missing and not application,
        "ready_for_app_attempt": not missing and application,
        "inference_verified": False, "missing": missing,
        "notes": [
            "No generation is performed by this setup script.",
            "CLIP ViT-B/32 may download on first inference; cached-file presence is not a checksum check.",
            "Use length=14 render=false explicitly; checked-in config defaults to length=15 render=true.",
            "Stock preview requires SMPL-X assets and a fix for its /lustre path and EGL setup on Windows.",
            "The app includes an approximate finger skeleton; production rig calibration and wrist-twist refinement remain.",
        ],
        "ffmpeg_on_path": shutil.which("ffmpeg"),
        "download_links": {name: f"https://drive.google.com/drive/folders/{entry[0]}"
                           for name, entry in DOWNLOADS.items()},
        "optional_reference_checkouts": {
            name: (ROOT / "third_party/motion_research" / name / ".git").exists()
            for name in REFERENCES},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clone", action="store_true", help="Clone missing core repositories; keep existing checkouts.")
    parser.add_argument("--install", action="store_true", help="Clone if needed and install only HandMDM/.venv.")
    parser.add_argument("--base-python", type=Path, default=Path(sys.executable), help="Python 3.9 or 3.10 for venv creation.")
    parser.add_argument("--download-models", action="store_true", help="Download the official models folder (may contain multiple checkpoints).")
    parser.add_argument("--download-annotations", action="store_true", help="Download annotations including text embeddings required by the stock CLI.")
    parser.add_argument("--references", choices=("none", "manipulation", "all"), default="none",
                        help="Optional roadmap sources only, under third_party/motion_research.")
    parser.add_argument("--application", action="store_true", help="Check the app worker, which does not preload annotations or import the renderer.")
    parser.add_argument("--report", type=Path, help="Optional report path within this repository's logs folder.")
    args = parser.parse_args(argv)
    target = (args.report or LOGS / "setup_readiness.json").resolve()
    if not target.is_relative_to((ROOT / "logs").resolve()):
        parser.error("Setup reports must be under the repository's logs folder.")
    action_error = None
    try:
        if args.clone or args.install:
            for name, url in CORE.items():
                clone_missing(ROOT / "third_party" / name, url)
        if args.references != "none":
            names = MANIPULATION if args.references == "manipulation" else REFERENCES
            for name in names:
                clone_missing(ROOT / "third_party/motion_research" / name, REFERENCES[name])
        if args.install:
            install_environment(args.base_python.resolve())
        for name in DOWNLOADS:
            if getattr(args, "download_" + name):
                download_folder(name)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        action_error = str(exc)
        print(f"Setup action failed: {action_error}", file=sys.stderr, flush=True)
    report = inspect_resources(application=args.application)
    if action_error:
        report["action_error"] = action_error
        report.update(ready=False, failed=True, error=action_error)
    LOGS.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {target}")
    for item in report["missing"]:
        print("- " + item)
    if report["ready"]:
        print("Resources ready for an inference attempt; generation remains unverified.")
    else:
        print("Resources are incomplete; see the report and HandMDM_Readiness_Review.md.")
    return 1 if action_error else (0 if report["ready"] else 2)


if __name__ == "__main__":
    raise SystemExit(main())
