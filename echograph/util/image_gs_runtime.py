from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


IMAGE_GS_REPO_URL = "https://github.com/NYU-ICL/image-gs.git"


@dataclass(frozen=True)
class ImageGsRuntimeStatus:
    root: Path
    python: Path
    ready: bool
    detail: str


def app_home_dir() -> Path:
    raw = (os.environ.get("QUBITMCP_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    try:
        repo = Path(__file__).resolve().parents[2]
        if (repo / "third_party" / "image-gs").exists():
            return repo
    except Exception:
        pass
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        return Path(local).expanduser() / "QubitMCP"
    return Path.home() / ".qubitmcp"


def image_gs_root(home: Path | None = None) -> Path:
    return (home or app_home_dir()) / "third_party" / "image-gs"


def image_gs_python(home: Path | None = None) -> Path:
    return image_gs_root(home) / ".venv" / "Scripts" / "python.exe"


def image_gs_setup_script() -> Path:
    return Path(__file__).resolve().parents[2] / "nodes" / "image_gs" / "setup_image_gs.bat"


def image_gs_status(home: Path | None = None) -> ImageGsRuntimeStatus:
    root = image_gs_root(home)
    python = image_gs_python(home)
    if not root.exists():
        return ImageGsRuntimeStatus(root, python, False, "Image-GS checkout is not installed.")
    if not (root / ".git").exists():
        return ImageGsRuntimeStatus(root, python, False, "Image-GS path is not a git checkout.")
    if not (root / "main.py").exists() or not (root / "cfgs" / "default.yaml").exists():
        return ImageGsRuntimeStatus(root, python, False, "Image-GS checkout is missing expected runtime files.")
    if not python.exists():
        return ImageGsRuntimeStatus(root, python, False, "Image-GS virtual environment is missing.")
    return ImageGsRuntimeStatus(root, python, True, "Image-GS runtime is ready.")


def image_gs_dependency_status(home: Path | None = None, *, timeout: int = 45) -> ImageGsRuntimeStatus:
    status = image_gs_status(home)
    if not status.ready:
        return status
    probe = (
        "import torch; import cv2; import flip_evaluator; import imageio.v3; "
        "import lpips; import matplotlib; import numpy; import scipy; import skimage; "
        "import torchmetrics; import yaml; from PIL import Image; "
        "from fused_ssim import fused_ssim; "
        "from gsplat import project_gaussians_2d_scale_rot, rasterize_gaussians_no_tiles, rasterize_gaussians_sum; "
        "from model import GaussianSplatting2D; from utils.misc_utils import load_cfg"
    )
    env = os.environ.copy()
    bundled_gsplat = status.root / "gsplat"
    if bundled_gsplat.exists():
        old_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(bundled_gsplat) + (os.pathsep + old_path if old_path else "")
    try:
        completed = subprocess.run(
            [str(status.python), "-c", probe],
            cwd=str(status.root),
            capture_output=True,
            text=True,
            timeout=max(15, int(timeout)),
            env=env,
        )
    except Exception as exc:
        return ImageGsRuntimeStatus(status.root, status.python, False, f"Image-GS dependency probe failed: {exc}")
    if completed.returncode == 0:
        return ImageGsRuntimeStatus(status.root, status.python, True, "Image-GS dependencies are ready.")
    detail = (completed.stderr or completed.stdout or "").strip().splitlines()
    tail = detail[-1] if detail else f"exit code {completed.returncode}"
    return ImageGsRuntimeStatus(
        status.root,
        status.python,
        False,
        "Image-GS dependencies are missing in the Image-GS venv. Use Setup Image-GS in the node InfoCard or rerun setup.bat, then try again. "
        + tail,
    )
