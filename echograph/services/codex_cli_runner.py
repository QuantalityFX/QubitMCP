from __future__ import annotations

import ast
import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    tomllib = None

DEFAULT_MODEL = "gpt-5.3-codex"
DEFAULT_MODEL_REASONING_EFFORT = "xhigh"
DEFAULT_SANDBOX_MODE = "read-only"
DEFAULT_APPROVAL_POLICY = "never"
SANDBOX_MODES = {"read-only", "workspace-write"}
APPROVAL_POLICIES = {"never", "on-request", "on-failure", "untrusted"}
REASONING_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}


class CodexCliError(RuntimeError):
    pass


class CodexCliNotFound(CodexCliError):
    pass


@dataclass(frozen=True)
class CodexExecRequest:
    working_directory: str | Path
    output_last_message_path: str | Path
    codex_executable: str = ""
    model: str = DEFAULT_MODEL
    model_reasoning_effort: str = DEFAULT_MODEL_REASONING_EFFORT
    sandbox_mode: str = DEFAULT_SANDBOX_MODE
    approval_policy: str = DEFAULT_APPROVAL_POLICY
    codex_home: str = ""
    network_access: bool = False
    writable_roots: Sequence[str] | str = field(default_factory=list)
    extra_args: Sequence[str] | str = field(default_factory=list)


@dataclass(frozen=True)
class CodexCommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def text(self) -> str:
        return "\n".join(part for part in (self.stdout.strip(), self.stderr.strip()) if part).strip()


def _clean(value: object) -> str:
    return str(value or "").strip()


def default_codex_home() -> str:
    home = _clean(os.environ.get("CODEX_HOME"))
    if home:
        return os.path.expandvars(os.path.expanduser(home))
    return str(Path.home() / ".codex")


def default_codex_config_path(codex_home: str = "") -> str:
    home = _clean(codex_home) or default_codex_home()
    return str(Path(os.path.expandvars(os.path.expanduser(home))) / "config.toml")


def _existing_file(path: Path) -> Path | None:
    try:
        return path if path.exists() and path.is_file() else None
    except Exception:
        return None


def _codex_candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    user_profile = Path(os.path.expandvars(os.environ.get("USERPROFILE") or str(Path.home())))
    appdata = Path(os.path.expandvars(os.environ.get("APPDATA") or str(user_profile / "AppData" / "Roaming")))
    local_appdata = Path(
        os.path.expandvars(os.environ.get("LOCALAPPDATA") or str(user_profile / "AppData" / "Local"))
    )

    if os.name == "nt":
        candidates.extend(
            [
                appdata / "npm" / "codex.cmd",
                user_profile / ".codex" / "qubitmcp-cli" / "node_modules" / ".bin" / "codex.cmd",
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "codex.cmd",
            ]
        )
        npx_root = local_appdata / "npm-cache" / "_npx"
        try:
            cached = [
                path
                for path in npx_root.glob("*\\node_modules\\.bin\\codex.cmd")
                if _existing_file(path) is not None
            ]
            cached.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            candidates.extend(cached)
        except Exception:
            pass
    else:
        candidates.extend(
            [
                user_profile / ".codex" / "qubitmcp-cli" / "node_modules" / ".bin" / "codex",
                user_profile / ".local" / "bin" / "codex",
            ]
        )

    seen: set[str] = set()
    existing: list[Path] = []
    for candidate in candidates:
        found = _existing_file(candidate)
        if found is None:
            continue
        key = str(found).lower()
        if key in seen:
            continue
        seen.add(key)
        existing.append(found)
    return existing


def _is_powershell_script(executable: str) -> bool:
    return os.name == "nt" and Path(str(executable or "").strip().strip("\"'")).suffix.lower() == ".ps1"


def _powershell_command_prefix(script_path: str) -> list[str]:
    shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path]


def _codex_command_prefix(codex_executable: str = "") -> list[str]:
    executable = resolve_codex_executable(codex_executable)
    if _is_powershell_script(executable):
        return _powershell_command_prefix(executable)
    return [executable]


def _parse_simple_scalar(value: str) -> object:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if text.startswith(("'", '"')):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (str, int, float, bool, list, dict)):
                return parsed
        except Exception:
            return text.strip("'\"")
    return text


def _set_nested_value(target: dict, dotted_key: str, value: object) -> None:
    parts = [part.strip().strip("'\"") for part in str(dotted_key or "").split(".") if part.strip()]
    if not parts:
        return
    cursor = target
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[parts[-1]] = value


def _parse_simple_toml(text: str) -> dict:
    data: dict = {}
    current = data
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            table = line.strip("[]").strip()
            current = data
            parts = [part.strip().strip("'\"") for part in table.split(".") if part.strip()]
            for part in parts:
                child = current.get(part)
                if not isinstance(child, dict):
                    child = {}
                    current[part] = child
                current = child
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        _set_nested_value(current, key.strip(), _parse_simple_scalar(value))
    return data


def read_codex_config(codex_home: str = "", config_path: str = "") -> dict:
    path_text = _clean(config_path) or default_codex_config_path(codex_home)
    path = Path(os.path.expandvars(os.path.expanduser(path_text)))
    if not path.exists() or not path.is_file():
        return {}
    if tomllib is not None:
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    try:
        return _parse_simple_toml(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def normalize_reasoning_effort(value: str) -> str:
    effort = _clean(value).lower()
    return effort if effort in REASONING_EFFORTS else DEFAULT_MODEL_REASONING_EFFORT


def codex_config_defaults(codex_home: str = "", config_path: str = "") -> dict[str, object]:
    path_text = _clean(config_path) or default_codex_config_path(codex_home)
    data = read_codex_config(codex_home, path_text)
    workspace_write = data.get("sandbox_workspace_write", {})
    if not isinstance(workspace_write, Mapping):
        workspace_write = {}
    network_value = workspace_write.get("network_access")
    if network_value is None:
        network_value = data.get("network_access", False)
    writable_roots = workspace_write.get("writable_roots")
    return {
        "codex_home": _clean(codex_home) or default_codex_home(),
        "config_path": path_text,
        "model": _clean(data.get("model")) or DEFAULT_MODEL,
        "model_reasoning_effort": normalize_reasoning_effort(
            _clean(data.get("model_reasoning_effort")) or DEFAULT_MODEL_REASONING_EFFORT
        ),
        "sandbox_mode": normalize_sandbox_mode(_clean(data.get("sandbox_mode")) or DEFAULT_SANDBOX_MODE),
        "approval_policy": normalize_approval_policy(
            _clean(data.get("approval_policy")) or DEFAULT_APPROVAL_POLICY
        ),
        "network_access": normalize_bool(network_value),
        "writable_roots": split_writable_roots(writable_roots),
    }


def detect_codex_executable() -> str:
    for path in _codex_candidate_paths():
        return str(path)
    names = ("codex.cmd", "codex") if os.name == "nt" else ("codex", "codex.cmd")
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return ""


def resolve_codex_executable(explicit: str = "") -> str:
    raw = _clean(explicit).strip("\"'")
    if raw:
        expanded = os.path.expandvars(os.path.expanduser(raw))
        path_like = any(sep and sep in expanded for sep in (os.sep, os.altsep)) or Path(expanded).is_absolute()
        if path_like:
            path = Path(expanded)
            if not path.exists():
                raise CodexCliNotFound(f"Codex executable was not found: {path}")
            if path.is_dir():
                raise CodexCliNotFound(f"Codex executable path is a directory: {path}")
            return str(path)
        found = shutil.which(expanded)
        if found:
            return found
        raise CodexCliNotFound(f"Codex executable was not found on PATH: {raw}")

    found = detect_codex_executable()
    if found:
        return found
    raise CodexCliNotFound(
        "Codex CLI was not found on PATH. Install Codex CLI or set codex_executable on the Codex Sandbox node."
    )


def normalize_sandbox_mode(value: str) -> str:
    mode = _clean(value)
    return mode if mode in SANDBOX_MODES else DEFAULT_SANDBOX_MODE


def normalize_approval_policy(value: str) -> str:
    policy = _clean(value)
    return policy if policy in APPROVAL_POLICIES else DEFAULT_APPROVAL_POLICY


def normalize_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _clean(value).lower() in {"1", "true", "yes", "on"}


def split_writable_roots(writable_roots: Sequence[str] | str | None) -> list[str]:
    if writable_roots is None:
        return []
    if isinstance(writable_roots, str):
        raw_parts = writable_roots.replace("\n", ";").split(";")
    else:
        raw_parts = list(writable_roots)

    roots: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        text = _clean(part).strip("\"'")
        if not text:
            continue
        try:
            normalized = str(Path(os.path.expandvars(os.path.expanduser(text))).resolve(strict=False))
        except Exception:
            normalized = text
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        roots.append(normalized)
    return roots


def _toml_string_array(values: Sequence[str]) -> str:
    return "[" + ", ".join(json.dumps(str(value)) for value in values) + "]"


def split_extra_args(extra_args: Sequence[str] | str | None) -> list[str]:
    if extra_args is None:
        return []
    if isinstance(extra_args, str):
        text = extra_args.strip()
        if not text:
            return []
        try:
            return [part for part in shlex.split(text, posix=True) if part]
        except ValueError:
            return [part for part in text.split() if part]
    return [_clean(part) for part in extra_args if _clean(part)]


def build_codex_env(codex_home: str = "", base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ)
    home = _clean(codex_home)
    if home:
        env["CODEX_HOME"] = os.path.expandvars(os.path.expanduser(home))
    return env


def build_codex_exec_command(request: CodexExecRequest) -> list[str]:
    executable = resolve_codex_executable(request.codex_executable)
    sandbox_mode = normalize_sandbox_mode(request.sandbox_mode)
    approval_policy = normalize_approval_policy(request.approval_policy)
    model = _clean(request.model) or DEFAULT_MODEL
    reasoning_effort = normalize_reasoning_effort(request.model_reasoning_effort)
    working_directory = _clean(request.working_directory)
    output_path = _clean(request.output_last_message_path)

    if _is_powershell_script(executable):
        network = "true" if normalize_bool(request.network_access) else "false"
        cmd = _powershell_command_prefix(executable)
        cmd.extend(
            [
                "-Exec",
                "-Model",
                model,
                "-Reasoning",
                reasoning_effort,
                "-Sandbox",
                sandbox_mode,
                "-Approval",
                approval_policy,
                "-NetworkAccess",
                network,
            ]
        )
        if working_directory:
            cmd.extend(["-Cd", working_directory])
        if output_path:
            cmd.extend(["-OutputLastMessage", output_path])
        cmd.extend(split_extra_args(request.extra_args))
        return cmd

    cmd = [
        executable,
        "--model",
        model,
        "--sandbox",
        sandbox_mode,
        "--ask-for-approval",
        approval_policy,
        "-c",
        f"approval_policy={approval_policy}",
        "-c",
        f"sandbox_mode={sandbox_mode}",
        "-c",
        f"model_reasoning_effort={reasoning_effort}",
        "-c",
        "web_search=disabled",
    ]
    if sandbox_mode == "workspace-write":
        network = "true" if normalize_bool(request.network_access) else "false"
        cmd.extend(["-c", f"sandbox_workspace_write.network_access={network}"])
        writable_roots = split_writable_roots(request.writable_roots)
        if writable_roots:
            cmd.extend(["-c", f"sandbox_workspace_write.writable_roots={_toml_string_array(writable_roots)}"])

    cmd.extend(split_extra_args(request.extra_args))
    cmd.extend(["exec", "-", "--skip-git-repo-check"])
    if working_directory:
        cmd.extend(["--cd", working_directory])
    cmd.extend(["--color", "never"])
    if output_path:
        cmd.extend(["--output-last-message", output_path])
    return cmd


def build_codex_command(args: Sequence[str], codex_executable: str = "") -> list[str]:
    return [*_codex_command_prefix(codex_executable), *[_clean(arg) for arg in args if _clean(arg)]]


def build_login_command(codex_executable: str = "") -> list[str]:
    return build_codex_command(["login"], codex_executable)


def build_login_status_command(codex_executable: str = "") -> list[str]:
    return build_codex_command(["login", "status"], codex_executable)


def build_logout_command(codex_executable: str = "") -> list[str]:
    return build_codex_command(["logout"], codex_executable)


def format_command(command: Sequence[str]) -> str:
    try:
        return subprocess.list2cmdline([str(part) for part in command])
    except Exception:
        return " ".join(str(part) for part in command)


def run_codex_command_capture(
    args: Sequence[str],
    *,
    codex_executable: str = "",
    codex_home: str = "",
    cwd: str | Path | None = None,
    timeout: float | None = 30.0,
    subprocess_kwargs: Mapping[str, object] | None = None,
) -> CodexCommandResult:
    command = build_codex_command(args, codex_executable)
    proc = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        env=build_codex_env(codex_home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **dict(subprocess_kwargs or {}),
    )
    return CodexCommandResult(int(proc.returncode), proc.stdout or "", proc.stderr or "")


def start_visible_codex_command(
    args: Sequence[str],
    *,
    codex_executable: str = "",
    codex_home: str = "",
    cwd: str | Path | None = None,
) -> subprocess.Popen:
    command = build_codex_command(args, codex_executable)
    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_CONSOLE"):
        creationflags |= int(getattr(subprocess, "CREATE_NEW_CONSOLE"))
    return subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        env=build_codex_env(codex_home),
        creationflags=creationflags,
    )
