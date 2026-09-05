from __future__ import annotations

import os
import re
import shlex
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore

from nodes.core import Spec


WAN22_VIDEO_NODE_KIND = "wan22_video"
WAN22_VIDEO_NODE_ALIASES = [
    "wan 2.2 video",
    "wan2.2 video",
    "wan22",
    "wan22_video",
    "wan_video",
    "wan ti2v",
    "wan2.2 ti2v",
    "wan22_ti2v",
    "text_image_to_video",
    "text image to video",
]
WAN22_VIDEO_NODE_KINDS = {WAN22_VIDEO_NODE_KIND, *WAN22_VIDEO_NODE_ALIASES}
WAN22_VIDEO_BODY_W = 520
WAN22_VIDEO_BODY_H = 542

MP4_OUTPUT_PARAM = "mp4_path"
STATUS_OUTPUT_PARAM = "wan_status"
OUTPUT_PARAMS = (MP4_OUTPUT_PARAM, STATUS_OUTPUT_PARAM)

_PARAM_PROMPT = "__wan22_prompt"
_PARAM_IMAGE = "__wan22_image"
_PARAM_NEGATIVE_PROMPT = "__wan22_negative_prompt"
_PARAM_MODEL_PATH = "__wan22_model_path"
_PARAM_RUNTIME_PATH = "__wan22_runtime_path"
_PARAM_SIZE = "__wan22_size"
_PARAM_FRAME_NUM = "__wan22_frame_num"
_PARAM_IMAGE_CHANGE = "__wan22_image_change"
_PARAM_SAMPLE_SHIFT = "__wan22_sample_shift"
_PARAM_SAMPLE_GUIDE_SCALE = "__wan22_sample_guide_scale"
_PARAM_SAMPLE_STEPS = "__wan22_sample_steps"
_PARAM_SAMPLE_SOLVER = "__wan22_sample_solver"
_PARAM_OUTPUT_DIR = "__wan22_output_dir"
_PARAM_SEED = "__wan22_seed"
_PARAM_OFFLOAD = "__wan22_offload"
_PARAM_T5_CPU = "__wan22_t5_cpu"
_PARAM_CONVERT_DTYPE = "__wan22_convert_dtype"
_PARAM_EXTRA_ARGS = "__wan22_extra_args"
_HIDDEN_PARAM = "__ui_hidden_params"

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
_MANIFEST_NAME = "qubitmcp_wan22_ti2v_manifest.json"
_WEIGHT_EXTS = {".safetensors", ".pt", ".pth", ".bin"}
_TIP_PROMPT = "Describe the motion, camera, and what should stay consistent."
_TIP_NEGATIVE = "Extra things to suppress, appended to Wan's built-in negative prompt. Useful for distorted faces and artifacts."
_TIP_IMAGE = "Optional reference image. When set, Wan animates from this image."
_TIP_RUNTIME = "Folder containing the Wan2.2 checkout and its .venv."
_TIP_MODEL = "Folder containing the Wan2.2 TI2V-5B model weights."
_TIP_SIZE = "Wan TI2V size key. For image-to-video, this sets target pixel area and Wan follows the input image aspect ratio."
_TIP_FRAMES = "Video frame count. Shorter clips usually drift less from the input image."
_TIP_SOLVER = "Sampler solver. UniPC is the default; DPM++ can look different on difficult prompts."
_TIP_SEED = "Use a number to repeat a result. Leave blank or random for a new seed."
_TIP_CHANGE = "Preservation helper. Lower values reduce effective Denoise and CFG for image-to-video; 1.0 is normal Wan behavior."
_TIP_DENOISE = "Wan sample shift. Lower values are usually less aggressive and can reduce warping."
_TIP_CFG = "Prompt guidance. Lower values can reduce overcooked face and shape changes."
_TIP_STEPS = "Diffusion sampling steps. More can improve detail but is slower and may not fix deformation."
_TIP_OFFLOAD = "Move model parts to CPU during generation to reduce VRAM use. Slower, but safer on limited GPUs."
_TIP_T5_CPU = "Run the text encoder on CPU to reduce VRAM use. Usually leave enabled on smaller GPUs."
_TIP_DTYPE = "Convert model dtype for lower memory use. Usually leave enabled on Windows/local runs."
_TIP_OUTPUT = "Folder where generated MP4 files are written."
_REQUIRED_PYTHON_MODULES = (
    "torch",
    "PIL",
    "diffusers",
    "transformers",
    "accelerate",
    "tqdm",
    "imageio",
    "imageio_ffmpeg",
    "easydict",
    "ftfy",
    "cv2",
    "einops",
)


def _app_home_dir() -> Path:
    raw = (os.environ.get("QUBITMCP_HOME") or os.environ.get("QUBITFIELD_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    try:
        repo = Path(__file__).resolve().parents[2]
        probe = repo / f"._qubit_wan22_write_test_{os.getpid()}.tmp"
        probe.write_text("write-test", encoding="ascii")
        probe.unlink(missing_ok=True)
        return repo
    except Exception:
        try:
            repo = Path(__file__).resolve().parents[2]
            if (repo / "third_party" / "Wan2.2").exists() or (repo / "models" / "Wan2.2-TI2V-5B").exists():
                return repo
        except Exception:
            pass
    try:
        repo = Path.cwd()
        if (repo / "third_party" / "Wan2.2").exists() or (repo / "models" / "Wan2.2-TI2V-5B").exists():
            return repo
    except Exception:
        pass
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        return Path(local).expanduser() / "QubitMCP"
    return Path.home() / ".qubitmcp"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def wan22_root(home: Path | None = None) -> Path:
    return (home or _app_home_dir()) / "third_party" / "Wan2.2"


def wan22_python(home: Path | None = None) -> Path:
    return wan22_root(home) / ".venv" / "Scripts" / "python.exe"


def wan22_model_dir(home: Path | None = None) -> Path:
    return (home or _app_home_dir()) / "models" / "Wan2.2-TI2V-5B"


def wan22_setup_script() -> Path:
    return _repo_root() / "setup.bat"


def wan22_helper_script() -> Path:
    return _repo_root() / "nodes" / "wan22_video" / "setup_wan22_video.bat"


def _setup_python() -> str:
    raw = str(getattr(sys, "executable", "") or "").strip()
    if raw and Path(raw).exists():
        return raw
    root_py = _repo_root() / ".venv" / "Scripts" / "python.exe"
    if root_py.exists():
        return str(root_py)
    return "python"


def _workflow_dir_from_ref(raw) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        path = Path(text).expanduser()
        return path.parent if path.suffix else path
    except Exception:
        return None


def _workflow_refs_from_widget(widget):
    seen = set()
    current = widget
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        try:
            path = getattr(current, "_current_path", None)
        except Exception:
            path = None
        if path:
            yield path
        try:
            current = current.parentWidget()
        except Exception:
            break


def _workflow_dir_for_node(node_item) -> Path | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    candidates = []
    if scene is not None:
        try:
            for view in scene.views() or []:
                candidates.extend(_workflow_refs_from_widget(view))
                try:
                    candidates.extend(_workflow_refs_from_widget(view.window()))
                except Exception:
                    pass
        except Exception:
            pass
        try:
            candidates.append(getattr(scene, "_filename", None))
        except Exception:
            pass
    try:
        candidates.extend(_workflow_refs_from_widget(node_item.window()))
    except Exception:
        pass
    try:
        candidates.extend(_workflow_refs_from_widget(QtWidgets.QApplication.activeWindow()))
    except Exception:
        pass
    for raw in candidates:
        workflow_dir = _workflow_dir_from_ref(raw)
        if workflow_dir is not None:
            return workflow_dir
    return None


def _default_output_dir(node_item) -> Path:
    workflow_dir = _workflow_dir_for_node(node_item)
    if workflow_dir is not None:
        return workflow_dir / "videos" / "wan22"
    return Path(tempfile.gettempdir()) / "EchoGraph" / "wan22"


def _clean_path_text(raw: Any) -> str:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _safe_stem(raw: str, fallback: str = "wan22") -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw or "").strip()).strip("._-")
    return text[:80] or fallback


def _param_value(model, name: str, default: str = "") -> str:
    if model is None:
        return default
    key = str(name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "") or "").strip().lower() == key:
            return str(entry.get("value", "") or "")
    return default


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = getattr(model, "params", None)
    if params is None:
        params = []
        setattr(model, "params", params)
    if not isinstance(params, list):
        params = list(params)
        setattr(model, "params", params)
    key = str(name or "").strip().lower()
    for entry in params:
        if isinstance(entry, dict) and str(entry.get("name", "") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            return
    params.append({"name": name, "value": default})


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    hidden_entry = None
    for entry in params:
        if isinstance(entry, dict) and str(entry.get("name", "") or "").strip().lower() == _HIDDEN_PARAM:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": _HIDDEN_PARAM, "value": ""}
        params.append(hidden_entry)
    hidden = {part.strip().lower() for part in str(hidden_entry.get("value", "") or "").split(",") if part.strip()}
    for name in names or []:
        key = str(name or "").strip().lower()
        if key:
            hidden.add(key)
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _set_param_value(node_item, name: str, value: str, *, notify_scene: bool = True) -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = str(name or "").strip().lower()
    text = str(value or "")
    found = False
    changed = False
    for entry in params:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "") or "").strip().lower() != key:
            continue
        found = True
        if str(entry.get("value", "") or "") != text:
            entry["value"] = text
            changed = True
        break
    if not found:
        params.append({"name": name, "value": text})
        changed = True
    model.params = params
    if not changed or not notify_scene:
        return
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is not None and hasattr(scene, "set_node_params"):
        try:
            scene.set_node_params(model.name, list(getattr(model, "params", None) or []), rebuild=False, emit=True)
            return
        except TypeError:
            try:
                scene.set_node_params(model.name, list(getattr(model, "params", None) or []))
                return
            except Exception:
                pass
        except Exception:
            pass
    if scene is not None and hasattr(scene, "paramChanged"):
        try:
            scene.paramChanged.emit(model.name, list(getattr(model, "params", None) or []))
        except Exception:
            pass


def _resolve_path(node_item, raw: str) -> Path:
    text = _clean_path_text(raw)
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return (_workflow_dir_for_node(node_item) or Path.cwd()) / path


def _output_dir(node_item, raw: str) -> Path:
    text = _clean_path_text(raw)
    if not text:
        return _default_output_dir(node_item)
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return (_workflow_dir_for_node(node_item) or Path.cwd()) / path


def _edge_port(edge) -> str:
    name = getattr(edge, "dst_port_name", None) or getattr(edge, "dst_label", None) or getattr(edge, "dst_name", None) or ""
    return str(name or "").strip().lower()


def _ordered_in_edges(node_item) -> list:
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return []
    try:
        return list(scene._ordered_in_edges(node_item))
    except Exception:
        try:
            return list(scene._in_edges(node_item))
        except Exception:
            return []


def _connected_value(node_item, ports: set[str], param_names: tuple[str, ...] = ()) -> str:
    scene = node_item.scene() if hasattr(node_item, "scene") else None
    if scene is None:
        return ""
    ports = {str(port or "").strip().lower() for port in ports if str(port or "").strip()}
    edges = _ordered_in_edges(node_item)
    named = [edge for edge in edges if _edge_port(edge) in ports]
    if ports and named:
        edges = named
    elif ports:
        return ""
    for edge in edges:
        src = getattr(edge, "src", None)
        model = getattr(src, "model", None)
        for param_name in param_names:
            value = _param_value(model, param_name, "").strip()
            if value:
                return value
        try:
            text = str(scene.resolve_text_value(src) or "").strip()
        except Exception:
            text = ""
        if text:
            return text
    return ""


def _has_incomplete_download(path: Path) -> bool:
    cache = path / ".cache" / "huggingface" / "download"
    if not cache.exists():
        return False
    try:
        for current_root, _dirs, files in os.walk(cache):
            for name in files:
                if str(name).lower().endswith(".incomplete"):
                    return True
    except Exception:
        return False
    return False


def _has_weight_file(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        for current_root, dirs, files in os.walk(path):
            dirs[:] = [name for name in dirs if name not in {".cache", ".git"}]
            for name in files:
                if Path(name).suffix.lower() in _WEIGHT_EXTS:
                    return True
    except Exception:
        return False
    return False


def _module_in_venv(python: Path, module: str) -> bool:
    try:
        venv = python.parent.parent
    except Exception:
        return False
    parts = [part for part in str(module or "").split(".") if part]
    if not parts:
        return False
    rel = Path(*parts)
    for site in (venv / "Lib" / "site-packages", venv / "lib" / "site-packages"):
        if (site / rel).exists():
            return True
        if len(parts) == 1 and (site / f"{parts[0]}.py").exists():
            return True
    return False


def _missing_python_modules(python: Path) -> list[str]:
    return [module for module in _REQUIRED_PYTHON_MODULES if not _module_in_venv(python, module)]


@dataclass(frozen=True)
class Wan22RuntimeStatus:
    root: Path
    python: Path
    model_dir: Path
    ready: bool
    detail: str


def wan22_status(runtime_root: str | Path = "", model_dir: str | Path = "") -> Wan22RuntimeStatus:
    root = Path(runtime_root).expanduser() if str(runtime_root or "").strip() else wan22_root()
    python = root / ".venv" / "Scripts" / "python.exe"
    model = Path(model_dir).expanduser() if str(model_dir or "").strip() else wan22_model_dir()
    if not root.exists():
        return Wan22RuntimeStatus(root, python, model, False, "Wan2.2 runtime is not installed.")
    if not (root / "generate.py").exists():
        return Wan22RuntimeStatus(root, python, model, False, "Wan2.2 generate.py is missing.")
    if not python.exists():
        return Wan22RuntimeStatus(root, python, model, False, "Wan2.2 Python environment is missing.")
    missing = _missing_python_modules(python)
    if missing:
        shown = ", ".join(missing[:4])
        suffix = "" if len(missing) <= 4 else f", and {len(missing) - 4} more"
        return Wan22RuntimeStatus(root, python, model, False, f"Wan2.2 Python environment is missing {shown}{suffix}. Click Setup Wan to repair.")
    if not model.exists():
        return Wan22RuntimeStatus(root, python, model, False, "Wan2.2 TI2V-5B model is not downloaded.")
    if _has_incomplete_download(model):
        return Wan22RuntimeStatus(root, python, model, False, "Wan2.2 model download is still running; incomplete files remain.")
    if not _has_weight_file(model):
        return Wan22RuntimeStatus(root, python, model, False, "Wan2.2 model folder has no weight files.")
    return Wan22RuntimeStatus(root, python, model, True, "Wan2.2 TI2V-5B runtime is ready.")


@dataclass
class Wan22RunSettings:
    prompt: str
    image: str
    negative_prompt: str
    runtime_root: Path
    model_dir: Path
    size: str
    frame_num: int
    image_change: float
    sample_shift: float
    sample_guide_scale: float
    sample_steps: int
    sample_solver: str
    output_dir: Path
    seed: str
    offload: bool
    t5_cpu: bool
    convert_dtype: bool
    extra_args: str
    node_name: str


def _coerce_bool(raw: str, default: bool = False) -> bool:
    text = str(raw or "").strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _coerce_int(raw: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(float(str(raw or "").strip()))
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


def _coerce_float(raw: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(raw or "").strip())
    except Exception:
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


def _format_float(value: float) -> str:
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def _wan_frame_count(raw: str | int) -> int:
    value = _coerce_int(str(raw), 121, 17, 121)
    if (value - 1) % 4:
        value = int(round((value - 1) / 4.0) * 4 + 1)
    return max(17, min(121, value))


def _validate_image_path(raw: str, node_item=None) -> str:
    text = _clean_path_text(raw)
    if not text:
        return ""
    path = _resolve_path(node_item, text) if node_item is not None else Path(text).expanduser()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Image path does not exist: {path}")
    if path.suffix.lower() not in _IMAGE_EXTS:
        raise ValueError(f"Unsupported image extension '{path.suffix}'.")
    return str(path)


def _output_path(settings: Wan22RunSettings) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return settings.output_dir / f"{_safe_stem(settings.node_name)}_{stamp}.mp4"


def wan22_command(settings: Wan22RunSettings, output: Path) -> list[str]:
    sample_shift = float(settings.sample_shift)
    sample_guide_scale = float(settings.sample_guide_scale)
    if settings.image:
        image_change = max(0.0, min(1.0, float(settings.image_change)))
        sample_shift = 1.0 + ((sample_shift - 1.0) * image_change)
        sample_guide_scale = 1.0 + ((sample_guide_scale - 1.0) * image_change)
    command = [
        str(settings.runtime_root / ".venv" / "Scripts" / "python.exe"),
        "generate.py",
        "--task",
        "ti2v-5B",
        "--size",
        settings.size,
        "--frame_num",
        str(settings.frame_num),
        "--ckpt_dir",
        str(settings.model_dir),
        "--prompt",
        settings.prompt,
        "--save_file",
        str(output),
    ]
    if settings.image:
        command.extend(["--image", settings.image])
    if settings.negative_prompt:
        command.extend(["--sample_neg_prompt", settings.negative_prompt])
    command.extend(
        [
            "--sample_solver",
            settings.sample_solver,
            "--sample_steps",
            str(settings.sample_steps),
            "--sample_shift",
            _format_float(sample_shift),
            "--sample_guide_scale",
            _format_float(sample_guide_scale),
        ]
    )
    if settings.offload:
        command.extend(["--offload_model", "True"])
    if settings.convert_dtype:
        command.append("--convert_model_dtype")
    if settings.t5_cpu:
        command.append("--t5_cpu")
    seed_text = str(settings.seed or "").strip()
    if seed_text and seed_text not in {"-1", "random", "Random"}:
        command.extend(["--base_seed", seed_text])
    extra = str(settings.extra_args or "").strip()
    if extra:
        command.extend(shlex.split(extra, posix=False))
    return command


def _latest_mp4_after(root: Path, started_at: float) -> Path | None:
    candidates: list[Path] = []
    for folder in (root, root / "outputs", root / "samples", root / "results"):
        if not folder.exists():
            continue
        try:
            for path in folder.glob("*.mp4"):
                if path.is_file() and path.stat().st_mtime >= started_at - 2:
                    candidates.append(path)
        except Exception:
            pass
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _best_failure_line(text: str, fallback: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return fallback
    priority = (
        "[wan2.2] ERROR:",
        "ERROR:",
        "ModuleNotFoundError",
        "No matching distribution",
        "Failed building wheel",
        "subprocess-exited-with-error",
    )
    for token in priority:
        for line in reversed(lines):
            if token.lower() in line.lower():
                return line
    for line in reversed(lines):
        if "FAILED. See " not in line and "Log:" not in line:
            return line
    return lines[-1]


def _button_style(primary: bool = False, stop: bool = False) -> str:
    if stop:
        return (
            "QPushButton{background:#3a1620;color:#fee2e2;border:1px solid #9f3346;"
            "border-radius:4px;padding:5px 8px;font-size:11px;}"
            "QPushButton:hover{background:#4a1d2a;border-color:#f87171;}"
            "QPushButton:disabled{color:#7f4b55;background:#1f1218;border-color:#4f2530;}"
        )
    if primary:
        return (
            "QPushButton{background:#0f766e;color:#f8fafc;border:1px solid #5eead4;"
            "border-radius:4px;padding:5px 10px;font-size:11px;font-weight:600;}"
            "QPushButton:hover{background:#0d9488;}"
            "QPushButton:disabled{background:#334155;color:#94a3b8;border-color:#475569;}"
        )
    return (
        "QPushButton{background:#16202f;color:#e2e8f0;border:1px solid #3b4b63;"
        "border-radius:4px;padding:5px 8px;font-size:11px;}"
        "QPushButton:hover{background:#1e2b3d;}"
        "QPushButton:disabled{color:#64748b;background:#111827;border-color:#243247;}"
    )


def _field_style() -> str:
    return (
        "QWidget#Wan22VideoWidget{background:#0f1216;border:1px solid #334155;border-radius:0px;}"
        "QLabel{color:#cbd5e1;font-size:11px;}"
        "QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox{background:#0f172a;color:#e2e8f0;"
        "border:1px solid #334155;border-radius:4px;padding:3px 5px;font-size:11px;}"
        "QPlainTextEdit{background:#0f172a;color:#e2e8f0;border:1px solid #334155;"
        "border-radius:4px;padding:4px 6px;font-size:11px;}"
        "QCheckBox{color:#cbd5e1;font-size:11px;}"
        "QToolButton{background:#16202f;color:#e2e8f0;border:1px solid #3b4b63;border-radius:4px;}"
        "QToolButton:hover{background:#1e2b3d;border-color:#7dd3fc;}"
        "QLineEdit:disabled,QPlainTextEdit:disabled{background:#111827;color:#64748b;border-color:#334155;}"
    )


class Wan22VideoWidget(QtWidgets.QWidget):
    def __init__(self, node_item, parent=None):
        super().__init__(parent)
        self._node_item = node_item
        self._proc: QtCore.QProcess | None = None
        self._setup_proc: QtCore.QProcess | None = None
        self._tail = ""
        self._setup_tail = ""
        self._run_started_at = 0.0
        self._run_output_path: Path | None = None
        self._syncing = False
        self._scene_connected = False
        self._connected_prompt = ""
        self._connected_image = ""

        build_ports(node_item)
        self.setObjectName("Wan22VideoWidget")
        self.setMinimumSize(WAN22_VIDEO_BODY_W, WAN22_VIDEO_BODY_H)
        self.setStyleSheet(_field_style())

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self._prompt_edit = QtWidgets.QPlainTextEdit()
        self._prompt_edit.setPlaceholderText("Prompt")
        self._prompt_edit.setFixedHeight(78)
        self._prompt_edit.textChanged.connect(self._commit_controls)
        self._prompt_edit.setToolTip(_TIP_PROMPT)
        root.addLayout(self._row("Prompt", self._prompt_edit, _TIP_PROMPT), 0)

        self._negative_edit = QtWidgets.QLineEdit()
        self._negative_edit.setPlaceholderText("distorted face, extra shapes, bad anatomy")
        self._negative_edit.setToolTip(_TIP_NEGATIVE)
        self._negative_edit.editingFinished.connect(self._commit_controls)
        root.addLayout(self._row("Negative", self._negative_edit, _TIP_NEGATIVE), 0)

        self._image_edit = QtWidgets.QLineEdit()
        self._image_edit.setPlaceholderText("Optional image for image-to-video")
        self._image_edit.setToolTip(_TIP_IMAGE)
        self._image_edit.editingFinished.connect(self._commit_controls)
        self._image_btn = self._tool_button("Browse image")
        self._image_btn.clicked.connect(self._browse_image)
        image_row = self._row("Image", self._image_edit, _TIP_IMAGE)
        image_row.addWidget(self._image_btn, 0)
        root.addLayout(image_row, 0)

        self._runtime_edit = QtWidgets.QLineEdit()
        self._runtime_edit.setToolTip(_TIP_RUNTIME)
        self._runtime_edit.editingFinished.connect(self._commit_controls)
        self._runtime_btn = self._tool_button("Choose Wan2.2 runtime folder")
        self._runtime_btn.clicked.connect(self._browse_runtime_dir)
        self._setup_btn = QtWidgets.QPushButton("Setup Wan")
        self._setup_btn.setStyleSheet(_button_style())
        self._setup_btn.setToolTip("Install or repair the local Wan2.2 runtime and model.")
        self._setup_btn.clicked.connect(self._on_setup)
        runtime_row = self._row("Runtime", self._runtime_edit, _TIP_RUNTIME)
        runtime_row.addWidget(self._runtime_btn, 0)
        runtime_row.addWidget(self._setup_btn, 0)
        root.addLayout(runtime_row, 0)

        self._model_edit = QtWidgets.QLineEdit()
        self._model_edit.setToolTip(_TIP_MODEL)
        self._model_edit.editingFinished.connect(self._commit_controls)
        self._model_btn = self._tool_button("Choose Wan2.2 TI2V model folder")
        self._model_btn.clicked.connect(self._browse_model_dir)
        model_row = self._row("Model", self._model_edit, _TIP_MODEL)
        model_row.addWidget(self._model_btn, 0)
        root.addLayout(model_row, 0)

        settings_row = QtWidgets.QHBoxLayout()
        settings_row.setContentsMargins(0, 0, 0, 0)
        settings_row.setSpacing(6)
        settings_row.addWidget(self._small_label("Size", _TIP_SIZE), 0)
        self._size_combo = QtWidgets.QComboBox()
        self._size_combo.setToolTip(_TIP_SIZE)
        self._size_combo.addItem("1280*704", "1280*704")
        self._size_combo.addItem("704*1280", "704*1280")
        self._size_combo.currentIndexChanged.connect(self._commit_controls)
        settings_row.addWidget(self._size_combo, 1)
        settings_row.addWidget(self._small_label("Frames", _TIP_FRAMES), 0)
        self._frame_spin = QtWidgets.QSpinBox()
        self._frame_spin.setRange(17, 121)
        self._frame_spin.setSingleStep(4)
        self._frame_spin.setFixedWidth(66)
        self._frame_spin.setToolTip(_TIP_FRAMES)
        self._frame_spin.valueChanged.connect(self._commit_controls)
        settings_row.addWidget(self._frame_spin, 0)
        settings_row.addWidget(self._small_label("Solver", _TIP_SOLVER), 0)
        self._solver_combo = QtWidgets.QComboBox()
        self._solver_combo.setToolTip(_TIP_SOLVER)
        self._solver_combo.addItem("UniPC", "unipc")
        self._solver_combo.addItem("DPM++", "dpm++")
        self._solver_combo.setFixedWidth(76)
        self._solver_combo.currentIndexChanged.connect(self._commit_controls)
        settings_row.addWidget(self._solver_combo, 0)
        settings_row.addWidget(self._small_label("Seed", _TIP_SEED), 0)
        self._seed_edit = QtWidgets.QLineEdit()
        self._seed_edit.setPlaceholderText("random")
        self._seed_edit.setToolTip(_TIP_SEED)
        self._seed_edit.setFixedWidth(88)
        self._seed_edit.editingFinished.connect(self._commit_controls)
        settings_row.addWidget(self._seed_edit, 0)
        root.addLayout(settings_row, 0)

        control_row = QtWidgets.QHBoxLayout()
        control_row.setContentsMargins(0, 0, 0, 0)
        control_row.setSpacing(6)
        control_row.addWidget(self._small_label("Change", _TIP_CHANGE), 0)
        self._image_change_spin = QtWidgets.QDoubleSpinBox()
        self._image_change_spin.setRange(0.0, 1.0)
        self._image_change_spin.setDecimals(2)
        self._image_change_spin.setSingleStep(0.05)
        self._image_change_spin.setFixedWidth(66)
        self._image_change_spin.setToolTip(_TIP_CHANGE)
        self._image_change_spin.valueChanged.connect(self._commit_controls)
        control_row.addWidget(self._image_change_spin, 0)
        control_row.addWidget(self._small_label("Denoise", _TIP_DENOISE), 0)
        self._sample_shift_spin = QtWidgets.QDoubleSpinBox()
        self._sample_shift_spin.setRange(1.0, 8.0)
        self._sample_shift_spin.setDecimals(2)
        self._sample_shift_spin.setSingleStep(0.25)
        self._sample_shift_spin.setFixedWidth(66)
        self._sample_shift_spin.setToolTip(_TIP_DENOISE)
        self._sample_shift_spin.valueChanged.connect(self._commit_controls)
        control_row.addWidget(self._sample_shift_spin, 0)
        control_row.addWidget(self._small_label("CFG", _TIP_CFG), 0)
        self._guide_spin = QtWidgets.QDoubleSpinBox()
        self._guide_spin.setRange(1.0, 8.0)
        self._guide_spin.setDecimals(2)
        self._guide_spin.setSingleStep(0.25)
        self._guide_spin.setFixedWidth(66)
        self._guide_spin.setToolTip(_TIP_CFG)
        self._guide_spin.valueChanged.connect(self._commit_controls)
        control_row.addWidget(self._guide_spin, 0)
        control_row.addWidget(self._small_label("Steps", _TIP_STEPS), 0)
        self._steps_spin = QtWidgets.QSpinBox()
        self._steps_spin.setRange(10, 80)
        self._steps_spin.setSingleStep(5)
        self._steps_spin.setFixedWidth(60)
        self._steps_spin.setToolTip(_TIP_STEPS)
        self._steps_spin.valueChanged.connect(self._commit_controls)
        control_row.addWidget(self._steps_spin, 0)
        control_row.addStretch(1)
        root.addLayout(control_row, 0)

        option_row = QtWidgets.QHBoxLayout()
        option_row.setContentsMargins(0, 0, 0, 0)
        option_row.setSpacing(10)
        option_row.addSpacing(56)
        self._offload_check = QtWidgets.QCheckBox("Offload")
        self._t5_cpu_check = QtWidgets.QCheckBox("T5 CPU")
        self._dtype_check = QtWidgets.QCheckBox("Dtype")
        self._offload_check.setToolTip(_TIP_OFFLOAD)
        self._t5_cpu_check.setToolTip(_TIP_T5_CPU)
        self._dtype_check.setToolTip(_TIP_DTYPE)
        for check in (self._offload_check, self._t5_cpu_check, self._dtype_check):
            check.toggled.connect(self._commit_controls)
            option_row.addWidget(check, 0)
        option_row.addStretch(1)
        root.addLayout(option_row, 0)

        self._output_dir_edit = QtWidgets.QLineEdit()
        self._output_dir_edit.setToolTip(_TIP_OUTPUT)
        self._output_dir_edit.editingFinished.connect(self._commit_controls)
        self._output_btn = self._tool_button("Choose output folder")
        self._output_btn.clicked.connect(self._browse_output_dir)
        output_row = self._row("Output", self._output_dir_edit, _TIP_OUTPUT)
        output_row.addWidget(self._output_btn, 0)
        root.addLayout(output_row, 0)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(6)
        self._generate_btn = QtWidgets.QPushButton("Generate")
        self._generate_btn.setStyleSheet(_button_style(primary=True))
        self._generate_btn.clicked.connect(self._on_generate)
        self._stop_btn = QtWidgets.QPushButton("Stop")
        self._stop_btn.setStyleSheet(_button_style(stop=True))
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setVisible(False)
        self._open_btn = QtWidgets.QPushButton("Open")
        self._open_btn.setStyleSheet(_button_style())
        self._open_btn.clicked.connect(self._open_output)
        self._copy_btn = QtWidgets.QPushButton("Copy")
        self._copy_btn.setStyleSheet(_button_style())
        self._copy_btn.clicked.connect(self._copy_output_path)
        button_row.addWidget(self._generate_btn, 1)
        button_row.addWidget(self._stop_btn, 0)
        button_row.addWidget(self._open_btn, 0)
        button_row.addWidget(self._copy_btn, 0)
        root.addLayout(button_row, 0)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(46)
        self._status.setMaximumHeight(62)
        self._status.setStyleSheet(
            "QLabel{background:#09111f;border:1px solid #1e293b;border-radius:4px;"
            "padding:4px 6px;color:#94a3b8;font-size:11px;}"
        )
        root.addWidget(self._status, 0)

        self._mp4_out = QtWidgets.QLineEdit()
        self._mp4_out.setReadOnly(True)
        root.addLayout(self._row("MP4", self._mp4_out), 0)

        self._refresh_from_params()
        self._schedule_scene_sync()

    def sizeHint(self):
        return QtCore.QSize(WAN22_VIDEO_BODY_W, WAN22_VIDEO_BODY_H)

    def minimumSizeHint(self):
        return QtCore.QSize(WAN22_VIDEO_BODY_W, WAN22_VIDEO_BODY_H)

    def _model(self):
        return getattr(self._node_item, "model", None)

    def _row(self, label: str, widget: QtWidgets.QWidget, tooltip: str = "") -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        lab = QtWidgets.QLabel(label)
        lab.setMinimumWidth(50)
        lab.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        if tooltip:
            lab.setToolTip(tooltip)
            widget.setToolTip(tooltip)
        row.addWidget(lab, 0)
        row.addWidget(widget, 1)
        return row

    def _small_label(self, text: str, tooltip: str = "") -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        if tooltip:
            label.setToolTip(tooltip)
        return label

    def _tool_button(self, tooltip: str) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton()
        btn.setToolTip(tooltip)
        btn.setFixedSize(26, 24)
        try:
            icon = QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_DialogOpenButton)
            btn.setIcon(icon)
            btn.setIconSize(QtCore.QSize(16, 16))
        except Exception:
            btn.setText("...")
        return btn

    def _schedule_scene_sync(self) -> None:
        QtCore.QTimer.singleShot(0, self._sync_scene)
        QtCore.QTimer.singleShot(120, self._sync_scene)

    def _sync_scene(self) -> None:
        scene = self._node_item.scene() if hasattr(self._node_item, "scene") else None
        if scene is not None and not self._scene_connected:
            if hasattr(scene, "linksChanged"):
                try:
                    scene.linksChanged.connect(self._on_graph_changed)
                except Exception:
                    pass
            if hasattr(scene, "paramChanged"):
                try:
                    scene.paramChanged.connect(self._on_graph_changed)
                except Exception:
                    pass
            self._scene_connected = True
        self._refresh_connected_inputs()

    def _on_graph_changed(self, *_args) -> None:
        QtCore.QTimer.singleShot(0, self._refresh_connected_inputs)

    def _refresh_connected_inputs(self) -> None:
        model = self._model()
        prompt = _connected_value(self._node_item, {"prompt"}, ())
        image = _connected_value(
            self._node_item,
            {"image", "input_image"},
            ("path", "source_image", "image_url", "output", "preview_image"),
        )
        self._connected_prompt = prompt
        self._connected_image = image
        self._syncing = True
        try:
            self._prompt_edit.setEnabled(not bool(prompt))
            self._image_edit.setEnabled(not bool(image))
            self._image_btn.setEnabled(not bool(image))
            if prompt:
                self._prompt_edit.setPlainText(prompt)
            else:
                self._prompt_edit.setPlainText(_param_value(model, _PARAM_PROMPT, ""))
            if image:
                self._image_edit.setText(image)
            else:
                self._image_edit.setText(_param_value(model, _PARAM_IMAGE, ""))
        finally:
            self._syncing = False

    def _refresh_from_params(self) -> None:
        model = self._model()
        self._syncing = True
        try:
            self._prompt_edit.setPlainText(_param_value(model, _PARAM_PROMPT, ""))
            self._negative_edit.setText(_param_value(model, _PARAM_NEGATIVE_PROMPT, ""))
            self._image_edit.setText(_param_value(model, _PARAM_IMAGE, ""))
            self._runtime_edit.setText(_param_value(model, _PARAM_RUNTIME_PATH, "") or str(wan22_root()))
            self._model_edit.setText(_param_value(model, _PARAM_MODEL_PATH, "") or str(wan22_model_dir()))
            size = _param_value(model, _PARAM_SIZE, "1280*704") or "1280*704"
            idx = self._size_combo.findData(size)
            self._size_combo.setCurrentIndex(max(0, idx))
            self._frame_spin.setValue(_wan_frame_count(_param_value(model, _PARAM_FRAME_NUM, "121")))
            self._image_change_spin.setValue(_coerce_float(_param_value(model, _PARAM_IMAGE_CHANGE, "1.0"), 1.0, 0.0, 1.0))
            self._sample_shift_spin.setValue(_coerce_float(_param_value(model, _PARAM_SAMPLE_SHIFT, "5.0"), 5.0, 1.0, 8.0))
            self._guide_spin.setValue(_coerce_float(_param_value(model, _PARAM_SAMPLE_GUIDE_SCALE, "5.0"), 5.0, 1.0, 8.0))
            self._steps_spin.setValue(_coerce_int(_param_value(model, _PARAM_SAMPLE_STEPS, "50"), 50, 10, 80))
            solver = _param_value(model, _PARAM_SAMPLE_SOLVER, "unipc") or "unipc"
            solver_idx = self._solver_combo.findData(solver)
            self._solver_combo.setCurrentIndex(max(0, solver_idx))
            self._seed_edit.setText(_param_value(model, _PARAM_SEED, ""))
            self._offload_check.setChecked(_coerce_bool(_param_value(model, _PARAM_OFFLOAD, "true"), True))
            self._t5_cpu_check.setChecked(_coerce_bool(_param_value(model, _PARAM_T5_CPU, "true"), True))
            self._dtype_check.setChecked(_coerce_bool(_param_value(model, _PARAM_CONVERT_DTYPE, "true"), True))
            output_dir = _param_value(model, _PARAM_OUTPUT_DIR, "")
            self._output_dir_edit.setText(output_dir or str(_default_output_dir(self._node_item)))
            self._mp4_out.setText(_param_value(model, MP4_OUTPUT_PARAM, ""))
            self._status.setText(_param_value(model, STATUS_OUTPUT_PARAM, "") or "Ready.")
        finally:
            self._syncing = False
        self._refresh_connected_inputs()
        self._refresh_status()

    def _commit_controls(self) -> None:
        if self._syncing:
            return
        if not self._connected_prompt:
            _set_param_value(self._node_item, _PARAM_PROMPT, self._prompt_edit.toPlainText().strip(), notify_scene=False)
        if not self._connected_image:
            _set_param_value(self._node_item, _PARAM_IMAGE, self._image_edit.text().strip(), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_NEGATIVE_PROMPT, self._negative_edit.text().strip(), notify_scene=False)
        default_runtime = str(wan22_root())
        runtime = self._runtime_edit.text().strip() or default_runtime
        _set_param_value(self._node_item, _PARAM_RUNTIME_PATH, "" if runtime == default_runtime else runtime, notify_scene=False)
        default_model = str(wan22_model_dir())
        model_path = self._model_edit.text().strip() or default_model
        _set_param_value(self._node_item, _PARAM_MODEL_PATH, "" if model_path == default_model else model_path, notify_scene=False)
        _set_param_value(self._node_item, _PARAM_SIZE, str(self._size_combo.currentData() or "1280*704"), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_FRAME_NUM, str(_wan_frame_count(int(self._frame_spin.value()))), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_IMAGE_CHANGE, _format_float(self._image_change_spin.value()), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_SAMPLE_SHIFT, _format_float(self._sample_shift_spin.value()), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_SAMPLE_GUIDE_SCALE, _format_float(self._guide_spin.value()), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_SAMPLE_STEPS, str(int(self._steps_spin.value())), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_SAMPLE_SOLVER, str(self._solver_combo.currentData() or "unipc"), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_SEED, self._seed_edit.text().strip(), notify_scene=False)
        _set_param_value(self._node_item, _PARAM_OFFLOAD, "true" if self._offload_check.isChecked() else "false", notify_scene=False)
        _set_param_value(self._node_item, _PARAM_T5_CPU, "true" if self._t5_cpu_check.isChecked() else "false", notify_scene=False)
        _set_param_value(self._node_item, _PARAM_CONVERT_DTYPE, "true" if self._dtype_check.isChecked() else "false", notify_scene=False)
        default_output = str(_default_output_dir(self._node_item))
        output_dir = self._output_dir_edit.text().strip() or default_output
        _set_param_value(self._node_item, _PARAM_OUTPUT_DIR, "" if output_dir == default_output else output_dir, notify_scene=False)
        self._refresh_status()

    def _set_status(self, text: str, *, error: bool = False, ok: bool = False) -> None:
        clean = str(text or "").strip()
        if error:
            style = "background:#160b0b;border:1px solid #7f1d1d;color:#fca5a5;"
        elif ok:
            style = "background:#07140f;border:1px solid #14532d;color:#86efac;"
        else:
            style = "background:#09111f;border:1px solid #1e293b;color:#94a3b8;"
        self._status.setStyleSheet(f"QLabel{{{style}border-radius:4px;padding:4px 6px;font-size:11px;}}")
        self._status.setText(clean)
        _set_param_value(self._node_item, STATUS_OUTPUT_PARAM, clean, notify_scene=False)

    def _refresh_status(self) -> None:
        if self._proc is not None:
            return
        if self._setup_proc is not None:
            self._set_status("Wan2.2 setup is running. Wait for the download/install to finish.")
            self._generate_btn.setEnabled(False)
            self._setup_btn.setEnabled(False)
            return
        status = wan22_status(self._runtime_edit.text().strip(), self._model_edit.text().strip())
        self._generate_btn.setEnabled(status.ready)
        self._setup_btn.setEnabled(True)
        self._set_status(status.detail, ok=status.ready, error=not status.ready)

    def _browse_image(self) -> None:
        parent = _dialog_parent(self._node_item) or self
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            "Choose Wan2.2 Input Image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff);;All Files (*.*)",
        )
        if path:
            self._image_edit.setText(path)
            self._commit_controls()

    def _browse_runtime_dir(self) -> None:
        parent = _dialog_parent(self._node_item) or self
        start = self._runtime_edit.text().strip() or str(wan22_root())
        path = QtWidgets.QFileDialog.getExistingDirectory(parent, "Choose Wan2.2 Runtime Folder", start)
        if path:
            self._runtime_edit.setText(path)
            self._commit_controls()

    def _browse_model_dir(self) -> None:
        parent = _dialog_parent(self._node_item) or self
        start = self._model_edit.text().strip() or str(wan22_model_dir())
        path = QtWidgets.QFileDialog.getExistingDirectory(parent, "Choose Wan2.2 TI2V Model Folder", start)
        if path:
            self._model_edit.setText(path)
            self._commit_controls()

    def _browse_output_dir(self) -> None:
        parent = _dialog_parent(self._node_item) or self
        start = self._output_dir_edit.text().strip() or str(_default_output_dir(self._node_item))
        path = QtWidgets.QFileDialog.getExistingDirectory(parent, "Choose Wan2.2 Output Folder", start)
        if path:
            self._output_dir_edit.setText(path)
            self._commit_controls()

    def _set_busy(self, busy: bool) -> None:
        self._generate_btn.setEnabled(not busy)
        self._setup_btn.setEnabled(not busy and self._setup_proc is None)
        self._stop_btn.setVisible(bool(busy))
        self._stop_btn.setEnabled(bool(busy))
        for widget in (
            self._prompt_edit,
            self._negative_edit,
            self._image_edit,
            self._image_btn,
            self._runtime_edit,
            self._runtime_btn,
            self._model_edit,
            self._model_btn,
            self._size_combo,
            self._frame_spin,
            self._solver_combo,
            self._image_change_spin,
            self._sample_shift_spin,
            self._guide_spin,
            self._steps_spin,
            self._seed_edit,
            self._offload_check,
            self._t5_cpu_check,
            self._dtype_check,
            self._output_dir_edit,
            self._output_btn,
        ):
            try:
                widget.setEnabled(not busy)
            except Exception:
                pass
        self._generate_btn.setText("Working..." if busy else "Generate")
        try:
            self._node_item.setBusyState(busy, "wan22" if busy else "")
        except Exception:
            pass

    def _collect_settings(self) -> Wan22RunSettings:
        if self._setup_proc is not None:
            raise RuntimeError("Wan2.2 setup is still running. Wait for it to finish before generating.")
        self._commit_controls()
        prompt = self._connected_prompt or self._prompt_edit.toPlainText().strip()
        if not prompt:
            raise ValueError("Enter a prompt for Wan2.2.")
        image = _validate_image_path(self._connected_image or self._image_edit.text().strip(), self._node_item)
        runtime = Path(self._runtime_edit.text().strip() or str(wan22_root())).expanduser()
        model_dir = Path(self._model_edit.text().strip() or str(wan22_model_dir())).expanduser()
        status = wan22_status(runtime, model_dir)
        if not status.ready:
            raise RuntimeError(f"{status.detail} Use Setup Wan or run setup.bat wan22_video.")
        model = self._model()
        output_dir = _output_dir(self._node_item, self._output_dir_edit.text().strip())
        return Wan22RunSettings(
            prompt=prompt,
            image=image,
            negative_prompt=self._negative_edit.text().strip(),
            runtime_root=runtime,
            model_dir=model_dir,
            size=str(self._size_combo.currentData() or "1280*704"),
            frame_num=_wan_frame_count(int(self._frame_spin.value())),
            image_change=_coerce_float(str(self._image_change_spin.value()), 1.0, 0.0, 1.0),
            sample_shift=_coerce_float(str(self._sample_shift_spin.value()), 5.0, 1.0, 8.0),
            sample_guide_scale=_coerce_float(str(self._guide_spin.value()), 5.0, 1.0, 8.0),
            sample_steps=_coerce_int(str(self._steps_spin.value()), 50, 10, 80),
            sample_solver=str(self._solver_combo.currentData() or "unipc"),
            output_dir=output_dir,
            seed=self._seed_edit.text().strip(),
            offload=self._offload_check.isChecked(),
            t5_cpu=self._t5_cpu_check.isChecked(),
            convert_dtype=self._dtype_check.isChecked(),
            extra_args=_param_value(model, _PARAM_EXTRA_ARGS, ""),
            node_name=_safe_stem(str(getattr(model, "name", "") or "wan22")),
        )

    def _on_generate(self) -> None:
        if self._proc is not None:
            return
        try:
            settings = self._collect_settings()
            output = _output_path(settings)
            output.parent.mkdir(parents=True, exist_ok=True)
            command = wan22_command(settings, output)
        except Exception as exc:
            self._set_status(str(exc), error=True)
            return

        proc = QtCore.QProcess(self)
        self._proc = proc
        self._tail = ""
        self._run_started_at = datetime.now().timestamp()
        self._run_output_path = output
        self._set_busy(True)
        self._set_status("Starting Wan2.2 generation...")

        def append_output() -> None:
            chunks = []
            try:
                out = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
                err = bytes(proc.readAllStandardError()).decode("utf-8", errors="replace")
                if out:
                    chunks.append(out)
                if err:
                    chunks.append(err)
            except Exception:
                return
            if not chunks:
                return
            self._tail = (self._tail + "\n" + "".join(chunks).strip())[-16000:]
            lines = [line.strip() for line in self._tail.splitlines() if line.strip()]
            if lines:
                self._set_status(lines[-1][-240:])

        def finished(exit_code: int, _exit_status) -> None:
            append_output()
            self._proc = None
            self._set_busy(False)
            out_path = self._run_output_path
            if int(exit_code) == 0 and out_path is not None and out_path.exists():
                self._finish_success(out_path)
            elif int(exit_code) == 0:
                latest = _latest_mp4_after(output.parent, self._run_started_at) or _latest_mp4_after(settings.runtime_root, self._run_started_at)
                if latest is not None:
                    self._finish_success(latest)
                else:
                    self._set_status("Wan2.2 finished but no MP4 output was found.", error=True)
            else:
                detail = _best_failure_line(self._tail, f"exit code {exit_code}")
                self._set_status(f"Wan2.2 generation failed: {detail}", error=True)
            try:
                proc.deleteLater()
            except Exception:
                pass

        proc.setWorkingDirectory(str(settings.runtime_root))
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("WANDB_DISABLED", "true")
        env.insert("QUBITMCP_HOME", str(_app_home_dir()))
        proc.setProcessEnvironment(env)
        proc.readyReadStandardOutput.connect(append_output)
        proc.readyReadStandardError.connect(append_output)
        proc.finished.connect(finished)
        proc.start(command[0], command[1:])
        if not proc.waitForStarted(5000):
            self._proc = None
            self._set_busy(False)
            self._set_status("Could not start Wan2.2 generation process.", error=True)

    def _finish_success(self, path: Path) -> None:
        _set_param_value(self._node_item, MP4_OUTPUT_PARAM, str(path), notify_scene=False)
        _set_param_value(self._node_item, STATUS_OUTPUT_PARAM, f"Generated {path.name}.", notify_scene=True)
        try:
            model = self._model()
            if model is not None:
                model.info = str(path)
        except Exception:
            pass
        self._mp4_out.setText(str(path))
        self._set_status(f"Generated {path.name}.", ok=True)

    def _on_stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        self._set_status("Stopping Wan2.2 generation...")
        try:
            proc.terminate()
            QtCore.QTimer.singleShot(5000, lambda: proc.kill() if proc.state() != QtCore.QProcess.NotRunning else None)
        except Exception:
            pass

    def _on_setup(self) -> None:
        if self._setup_proc is not None:
            return
        script = wan22_helper_script()
        if not script.exists():
            self._set_status(f"Wan2.2 setup helper was not found: {script}", error=True)
            return
        self._commit_controls()
        self._setup_tail = ""
        proc = QtCore.QProcess(self)
        self._setup_proc = proc
        self._setup_btn.setEnabled(False)
        self._generate_btn.setEnabled(False)
        self._set_status("Running Wan2.2 setup...")

        def append_output() -> None:
            chunks = []
            try:
                out = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
                err = bytes(proc.readAllStandardError()).decode("utf-8", errors="replace")
                if out:
                    chunks.append(out)
                if err:
                    chunks.append(err)
            except Exception:
                return
            if not chunks:
                return
            self._setup_tail = (self._setup_tail + "\n" + "".join(chunks).strip())[-16000:]
            lines = [line.strip() for line in self._setup_tail.splitlines() if line.strip()]
            if lines:
                self._set_status(lines[-1][-240:])

        def finished(exit_code: int, _exit_status) -> None:
            append_output()
            self._setup_proc = None
            self._setup_btn.setEnabled(True)
            self._generate_btn.setEnabled(True)
            if int(exit_code) != 0:
                detail = _best_failure_line(self._setup_tail, f"exit code {exit_code}")
                self._set_status(f"Wan2.2 setup failed: {detail}", error=True)
            else:
                self._runtime_edit.setText(str(wan22_root()))
                self._model_edit.setText(str(wan22_model_dir()))
                self._commit_controls()
                self._refresh_status()
            try:
                proc.deleteLater()
            except Exception:
                pass

        proc.setWorkingDirectory(str(_repo_root()))
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("QUBITMCP_HOME", str(_app_home_dir()))
        env.insert("QUBITFIELD_HOME", str(_app_home_dir()))
        proc.setProcessEnvironment(env)
        proc.readyReadStandardOutput.connect(append_output)
        proc.readyReadStandardError.connect(append_output)
        proc.finished.connect(finished)
        proc.start("cmd.exe", ["/c", str(script), str(_app_home_dir()), _setup_python()])
        if not proc.waitForStarted(5000):
            self._setup_proc = None
            self._setup_btn.setEnabled(True)
            self._generate_btn.setEnabled(True)
            self._set_status("Could not start Wan2.2 setup.", error=True)

    def _open_output(self) -> None:
        raw = self._mp4_out.text().strip() or _param_value(self._model(), MP4_OUTPUT_PARAM, "")
        try:
            if raw and Path(raw).expanduser().exists():
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(Path(raw).expanduser())))
                return
            out = _output_dir(self._node_item, self._output_dir_edit.text().strip())
            out.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(out)))
        except Exception as exc:
            self._set_status(str(exc), error=True)

    def _copy_output_path(self) -> None:
        raw = self._mp4_out.text().strip() or _param_value(self._model(), MP4_OUTPUT_PARAM, "")
        try:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(raw)
            self._set_status("Copied MP4 path." if raw else "No MP4 path to copy.", error=not bool(raw))
        except Exception as exc:
            self._set_status(str(exc), error=True)


def _dialog_parent(node_item):
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                if win is not None:
                    return win
        except Exception:
            pass
    try:
        win = node_item.window()
        if win is not None:
            return win
    except Exception:
        pass
    return QtWidgets.QApplication.activeWindow()


def build_ports(node_item) -> None:
    defaults = {
        _PARAM_PROMPT: "",
        _PARAM_IMAGE: "",
        _PARAM_NEGATIVE_PROMPT: "",
        _PARAM_MODEL_PATH: str(wan22_model_dir()),
        _PARAM_RUNTIME_PATH: str(wan22_root()),
        _PARAM_SIZE: "1280*704",
        _PARAM_FRAME_NUM: "121",
        _PARAM_IMAGE_CHANGE: "1.0",
        _PARAM_SAMPLE_SHIFT: "5.0",
        _PARAM_SAMPLE_GUIDE_SCALE: "5.0",
        _PARAM_SAMPLE_STEPS: "50",
        _PARAM_SAMPLE_SOLVER: "unipc",
        _PARAM_OUTPUT_DIR: "",
        _PARAM_SEED: "",
        _PARAM_OFFLOAD: "true",
        _PARAM_T5_CPU: "true",
        _PARAM_CONVERT_DTYPE: "true",
        _PARAM_EXTRA_ARGS: "",
    }
    for name, default in defaults.items():
        _ensure_param(node_item, name, default)
    for name in OUTPUT_PARAMS:
        _ensure_param(node_item, name, "")
    _ensure_hidden_params(getattr(node_item, "model", None), [*defaults.keys(), *OUTPUT_PARAMS])
    try:
        setattr(node_item, "_default_named_input", "prompt")
        setattr(node_item, "_show_default_input_with_named", True)
        node_item.ensure_input("prompt")
        node_item.ensure_input("image")
    except Exception:
        pass
    try:
        for name in OUTPUT_PARAMS:
            node_item.ensure_output(name)
    except Exception:
        pass


def _ensure_body_space(node_item, bottom_y: int) -> None:
    try:
        pad = int(getattr(node_item, "_PADDING", 8))
        min_h = float(bottom_y + pad)
        if float(getattr(node_item, "height", 0.0) or 0.0) < min_h:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.height = min_h
    except Exception:
        pass


def _set_manual_ports(node_item, y_cursor: int) -> None:
    for name, offset in {"prompt": 34, "image": 152}.items():
        try:
            node_item._input_port_pos[name.lower()] = (QtCore.QPointF(0.0, float(y_cursor + offset)), name)
        except Exception:
            pass
    for name, offset in {STATUS_OUTPUT_PARAM: 492, MP4_OUTPUT_PARAM: 524}.items():
        try:
            node_item._output_port_pos[name.lower()] = (QtCore.QPointF(float(node_item.width), float(y_cursor + offset)), name)
        except Exception:
            pass


def render_node_body(node_item, y_cursor: int) -> int:
    build_ports(node_item)
    body = Wan22VideoWidget(node_item)
    proxy = QtWidgets.QGraphicsProxyWidget(node_item)
    proxy.setWidget(body)
    proxy.setZValue(node_item.zValue() + 0.1)
    proxy.setPos(0, y_cursor)
    hint = body.sizeHint().expandedTo(body.minimumSizeHint())
    w = max(int(hint.width()), int(getattr(node_item, "width", WAN22_VIDEO_BODY_W) or WAN22_VIDEO_BODY_W))
    h = max(int(hint.height()), int(body.minimumSizeHint().height()))
    try:
        body.setMinimumWidth(w)
        body.setMaximumWidth(w)
        proxy.setMinimumWidth(w)
        proxy.setMaximumWidth(w)
        proxy.setPreferredSize(w, h)
    except Exception:
        pass
    proxy.resize(w, h)
    try:
        if int(getattr(node_item, "width", 0) or 0) < w:
            try:
                node_item.prepareGeometryChange()
            except Exception:
                pass
            node_item.width = w
    except Exception:
        pass
    try:
        node_item._plugin_proxies.append(proxy)
    except Exception:
        pass
    _set_manual_ports(node_item, int(y_cursor))
    bottom_y = int(y_cursor) + h
    _ensure_body_space(node_item, bottom_y)
    return bottom_y


WAN22_VIDEO_SPEC = Spec(
    stripe_color="#0f766e",
    render_node_body=render_node_body,
    build_ports=build_ports,
)


__all__ = [
    "WAN22_VIDEO_NODE_ALIASES",
    "WAN22_VIDEO_NODE_KIND",
    "WAN22_VIDEO_NODE_KINDS",
    "WAN22_VIDEO_BODY_H",
    "WAN22_VIDEO_BODY_W",
    "WAN22_VIDEO_SPEC",
    "build_ports",
    "render_node_body",
    "wan22_command",
    "wan22_model_dir",
    "wan22_root",
    "wan22_status",
]
