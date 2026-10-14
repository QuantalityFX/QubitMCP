"""Application-owned PriorMDM installer. Run with the app's Python 3.10:

python -m echograph.motion.backends.priormdm.installer

Default: report prerequisites. --install creates an isolated .venv and installs
inference packages. --download-assets downloads the official 50-step checkpoint,
SMPL archive and HumanML3D normalization files into shared app storage. Downloads
are cached for reuse. Existing files and upstream source are never reset/replaced.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
import zlib
from urllib.request import urlopen

from .backend import PROJECT_ROOT as ROOT, PriorMDMConfig, INSPECTED_REVISION
from .paths import default_repository, storage_root
from .setup_support import find_checkpoint, inspect_setup
from .normalization_assets import ensure_normalization

# Sources: upstream README.md section 4 and prepare/download_smpl_files.sh.
ASSETS = (("humanml-50steps.zip", "1RpAon66KsWRDLhoh3uREnDeycoq6JnX1", "save"),
          ("smpl.zip", "1INYlGA76ak_cKGzvpOV2Pe6RkYTlXTW2", "body_models"))


def extract_new_files(archive: Path, destination: Path, *, resume: bool = False, strip_prefix: str = "") -> None:
    """Reject traversal and overwrites; a retry may reuse byte-identical files."""
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zipped:
        members = []
        seen = set()
        for entry in zipped.infolist():
            parts = PurePosixPath(entry.filename.replace("\\", "/"))
            if parts.is_absolute() or ".." in parts.parts or any(":" in p for p in parts.parts):
                raise ValueError(f"Unsafe archive path: {entry.filename}")
            if strip_prefix:
                if not parts.parts or parts.parts[0] != strip_prefix:
                    raise ValueError(f"Unexpected source archive root: {entry.filename}")
                if len(parts.parts) == 1:
                    continue
                parts = PurePosixPath(*parts.parts[1:])
            target = (root / Path(*parts.parts)).resolve()
            if not target.is_relative_to(root) or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError(f"Unsafe archive entry: {entry.filename}")
            if not entry.is_dir():
                if target in seen:
                    raise FileExistsError(f"Refusing to replace or duplicate {target}")
                seen.add(target)
                if target.exists():
                    if not resume or not target.is_file() or target.stat().st_size != entry.file_size:
                        raise FileExistsError(f"Refusing to replace {target}")
                    checksum = 0
                    with target.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            checksum = zlib.crc32(chunk, checksum)
                    if checksum != entry.CRC:
                        raise FileExistsError(f"Existing file differs from downloaded asset: {target}")
                    continue
            members.append((entry, target))
        for entry, target in members:
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                partial_path = None
                try:
                    with zipped.open(entry) as src, tempfile.NamedTemporaryFile(
                            dir=target.parent, prefix=".priormdm-extract-", delete=False) as dst:
                        partial_path = Path(dst.name)
                        shutil.copyfileobj(src, dst)
                    if target.exists():
                        raise FileExistsError(f"Refusing to replace {target}")
                    partial_path.rename(target)
                finally:
                    if partial_path is not None:
                        partial_path.unlink(missing_ok=True)


def run(command: list[str], **kwargs) -> None:
    print(subprocess.list2cmdline(command), flush=True)
    kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    subprocess.run(command, check=True, **kwargs)


def ensure_source(repository: Path, download_cache: Path | None = None) -> None:
    """Install the inspected upstream source, including into asset-only folders."""
    if (repository / "utils/model_util.py").is_file():
        return
    downloads = download_cache or repository / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / f"priormdm-source-{INSPECTED_REVISION}.zip"
    if not archive.is_file():
        url = f"https://codeload.github.com/priorMDM/priorMDM/zip/{INSPECTED_REVISION}"
        print(f"Downloading PriorMDM source: {url}", flush=True)
        partial = archive.with_suffix(".partial")
        try:
            with urlopen(url, timeout=60) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output)
            with zipfile.ZipFile(partial) as zipped:
                if zipped.testzip() is not None:
                    raise ValueError("PriorMDM source download is corrupt; retry setup.")
            partial.replace(archive)
        finally:
            partial.unlink(missing_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        roots = {PurePosixPath(name).parts[0] for name in zipped.namelist() if name}
        if len(roots) != 1:
            raise ValueError("Unexpected PriorMDM source archive layout.")
        prefix = roots.pop()
        if f"{prefix}/utils/model_util.py" not in zipped.namelist():
            raise ValueError("PriorMDM source archive is missing utils/model_util.py.")
    extract_new_files(archive, repository, resume=True, strip_prefix=prefix)
    print(f"PriorMDM source ready: {repository}", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=default_repository())
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--dataset", type=Path, help="Advanced override; defaults to the shared repository/dataset/HumanML3D folder.")
    parser.add_argument("--download-cache", type=Path, help="Optional shared download cache; defaults to repository/downloads.")
    parser.add_argument("--python", dest="runtime_python", type=Path,
                        help="Interpreter to check; installation always uses the repository's isolated .venv.")
    parser.add_argument("--report", type=Path, help="Write a structured setup report for the node UI.")
    parser.add_argument("--clone", action="store_true", help="Download missing upstream source (Git is not required).")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--download-assets", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--check-runtime", action="store_true")
    parser.add_argument("--torch-version", default="1.13.1")
    parser.add_argument("--torchvision-version", default="0.14.1")
    parser.add_argument("--torch-index", default="https://download.pytorch.org/whl/cu117")
    args = parser.parse_args(argv)
    repository = args.repository.resolve()
    try:
        return perform_setup(args, parser, repository)
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({"schema_version": 1, "ready": False,
                                               "failed": True, "error": str(exc)}, indent=2), encoding="utf-8")
        print(f"Setup stopped: {exc}", file=sys.stderr)
        return 2


def perform_setup(args, parser, repository: Path) -> int:
    dataset = (args.dataset or repository / "dataset" / "HumanML3D").resolve()
    if args.clone or args.install:
        ensure_source(repository, args.download_cache)
    if (args.install or args.download_assets) and not (repository / "utils/model_util.py").is_file():
        raise ValueError("PriorMDM source is missing. Run setup with --install or --clone first.")
    environment = repository / ".venv"
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if args.install:
        if not (3, 8) <= sys.version_info[:2] <= (3, 10):
            parser.error("Run the stock setup with Python 3.8–3.10; the app .venv uses 3.10.")
        if not python.exists():
            if environment.exists():
                parser.error(f"Incomplete environment exists: {environment}; inspect it before retrying.")
            run([sys.executable, "-m", "venv", str(environment)])
        pip = [str(python), "-m", "pip", "install", "--retries", "0", "--timeout", "20"]
        run(pip + ["pip==24.0", "setuptools==69.5.1", "wheel==0.43.0"])
        run(pip + [f"torch=={args.torch_version}", f"torchvision=={args.torchvision_version}", "--index-url", args.torch_index])
        requirements = ROOT / "echograph/motion/backends/priormdm/requirements-inference.txt"
        run(pip + ["--no-build-isolation", "-r", str(requirements)])
        freeze = subprocess.check_output([str(python), "-m", "pip", "freeze"], text=True)
        log_dir = storage_root() / "logs" / "motion" / "priormdm"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "environment-freeze.txt").write_text(freeze, encoding="utf-8")
    if args.download_assets:
        if not python.is_file():
            parser.error("Run --install first to create the isolated download environment.")
        downloads = (args.download_cache or repository / "downloads").resolve()
        downloads.mkdir(parents=True, exist_ok=True)
        ensure_normalization(dataset, downloads / "humanml3d")
        for filename, file_id, folder in ASSETS:
            archive = downloads / filename
            complete = downloads / (filename + ".extracted")
            if complete.is_file() and archive.is_file():
                with zipfile.ZipFile(archive) as zipped:
                    all_present = all((repository / folder / entry.filename).is_file()
                                      for entry in zipped.infolist() if not entry.is_dir())
                if all_present:
                    print(f"Already extracted {filename}; leaving existing assets unchanged.")
                    continue
            if not archive.exists():
                partial = archive.with_suffix(".partial")
                # Resume a cancelled download owned by this installer.
                run([str(python), "-m", "gdown", file_id, "--continue", "-O", str(partial)])
                partial.rename(archive)
            extract_new_files(archive, repository / folder, resume=True)
            complete.write_text("Extracted without replacing existing files.\n", encoding="utf-8")
    config = PriorMDMConfig(repository=repository,
                            python=(args.runtime_python.resolve() if args.runtime_python and not args.install else python),
                            checkpoint=args.checkpoint.resolve() if args.checkpoint else find_checkpoint(repository),
                            dataset=dataset,
                            download_cache=args.download_cache.resolve() if args.download_cache else None)
    report = inspect_setup(config, check_runtime=args.check_runtime or args.probe or args.install)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("PriorMDM is ready." if report["ready"] else "PriorMDM still needs the following setup:", flush=True)
        for missing in report["missing"]:
            print("- " + missing, flush=True)
    else:
        print(json.dumps(report, indent=2))
    return 1 if report["missing"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Setup stopped: {exc}", file=sys.stderr)
        raise SystemExit(2)
