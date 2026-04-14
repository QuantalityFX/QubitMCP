from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
from typing import Any, Dict, List, Tuple
from urllib import error as urllib_error
from urllib import request as urllib_request

try:
    from PySide6 import QtWidgets, QtGui, QtCore
except Exception:
    try:
        from PySide2 import QtWidgets, QtGui, QtCore  # type: ignore
    except Exception:
        QtWidgets = None  # type: ignore[assignment]
        QtGui = None  # type: ignore[assignment]
        QtCore = None  # type: ignore[assignment]

from echograph.rigging.fbx_stage3_ingest import (
    FBXBindIngestError,
    compare_skeleton_layout,
    ingest_fbx_bind_data,
)
from echograph.rigging.fbx_stage4_animation import (
    FBXAnimationIngestError,
    ingest_fbx_animation_data,
)
from echograph.rigging.fbx_stage5_evaluator import evaluate_rig_at_time
from nodes.core import Spec
from nodes.util_graph import param_change_relevant as _param_change_relevant

ROLE_PORTS: Tuple[str, str, str] = ("rest_geometry", "capture_pose", "animated_pose")
FBX_KIND_ALIASES: Tuple[str, str, str] = ("fbx_import", "fbx import", "fbximport")
FBX_REQUIRED_RUNTIME_FILES: Tuple[str, str, str] = ("fbx.pyd", "FbxCommon.py", "libfbxsdk.dll")
FBX_WINDOWS_INSTALLER_URL = (
    "https://damassets.autodesk.net/content/dam/autodesk/www/files/"
    "fbx202039_fbxpythonsdk_win.exe"
)
FBX_WINDOWS_INSTALLER_FILENAME = "fbx202039_fbxpythonsdk_win.exe"

_FBX_SDK_PROMPT_SESSION_SHOWN = False
_FBX_SDK_PROMPT_SUPPRESS_CACHE: bool | None = None


def _app_home_dir() -> Path:
    raw = (os.environ.get("QUBITMCP_HOME") or "").strip()
    if raw:
        try:
            return Path(raw).expanduser()
        except Exception:
            pass
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        try:
            return Path(local).expanduser() / "QubitMCP"
        except Exception:
            pass
    return Path.home() / ".qubitmcp"


def _default_fbx_sdk_source_dir() -> Path:
    return _app_home_dir() / "third_party" / "fbx_sdk"


def _default_fbx_sdk_installer_dir() -> Path:
    return _app_home_dir() / "third_party" / "fbx_sdk_installer"


def _windows_fbx_sdk_install_roots() -> List[Path]:
    roots: List[Path] = []
    seen: set[str] = set()
    for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        try:
            p = Path(raw) / "Autodesk" / "FBX" / "FBX Python SDK"
        except Exception:
            continue
        token = str(p).lower()
        if token not in seen:
            roots.append(p)
            seen.add(token)
    for fallback in (
        Path("C:/Program Files/Autodesk/FBX/FBX Python SDK"),
        Path("C:/Program Files (x86)/Autodesk/FBX/FBX Python SDK"),
    ):
        token = str(fallback).lower()
        if token not in seen:
            roots.append(fallback)
            seen.add(token)
    return roots


def _fbx_sdk_prompt_state_path() -> Path:
    return _app_home_dir() / "fbx_sdk_prompt_state.json"


def _load_fbx_sdk_prompt_state() -> Dict[str, Any]:
    path = _fbx_sdk_prompt_state_path()
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return dict(payload)
    except Exception:
        pass
    return {}


def _save_fbx_sdk_prompt_state(state: Dict[str, Any]) -> None:
    path = _fbx_sdk_prompt_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(state or {}), ensure_ascii=True, indent=2), encoding="utf-8")
    except Exception:
        pass


def _load_fbx_sdk_prompt_suppressed() -> bool:
    global _FBX_SDK_PROMPT_SUPPRESS_CACHE
    if _FBX_SDK_PROMPT_SUPPRESS_CACHE is not None:
        return bool(_FBX_SDK_PROMPT_SUPPRESS_CACHE)
    payload = _load_fbx_sdk_prompt_state()
    suppressed = bool(payload.get("suppressed", False))
    _FBX_SDK_PROMPT_SUPPRESS_CACHE = bool(suppressed)
    return bool(suppressed)


def _save_fbx_sdk_prompt_suppressed(suppressed: bool) -> None:
    global _FBX_SDK_PROMPT_SUPPRESS_CACHE
    payload = _load_fbx_sdk_prompt_state()
    payload["suppressed"] = bool(suppressed)
    _save_fbx_sdk_prompt_state(payload)
    _FBX_SDK_PROMPT_SUPPRESS_CACHE = bool(suppressed)


def _load_last_fbx_sdk_source_dir() -> str:
    payload = _load_fbx_sdk_prompt_state()
    return str(payload.get("last_source_dir", "") or "").strip()


def _save_last_fbx_sdk_source_dir(source_dir: str) -> None:
    raw = str(source_dir or "").strip()
    if not raw:
        return
    payload = _load_fbx_sdk_prompt_state()
    payload["last_source_dir"] = raw
    _save_fbx_sdk_prompt_state(payload)


def _load_last_fbx_sdk_installer_path() -> str:
    payload = _load_fbx_sdk_prompt_state()
    return str(payload.get("last_installer_path", "") or "").strip()


def _save_last_fbx_sdk_installer_path(installer_path: str) -> None:
    raw = str(installer_path or "").strip()
    if not raw:
        return
    payload = _load_fbx_sdk_prompt_state()
    payload["last_installer_path"] = raw
    _save_fbx_sdk_prompt_state(payload)


def _runtime_fbx_expected_paths() -> Dict[str, Path]:
    paths = {}
    try:
        paths = dict(sysconfig.get_paths() or {})
    except Exception:
        paths = {}
    site_packages_raw = (paths.get("purelib") or paths.get("platlib") or "").strip()
    if site_packages_raw:
        site_packages = Path(site_packages_raw)
    else:
        site_packages = Path(sys.executable).resolve().parent.parent / "Lib" / "site-packages"
    scripts_dir = Path(sys.executable).resolve().parent
    return {
        "fbx_pyd": site_packages / "fbx.pyd",
        "fbxcommon_py": site_packages / "FbxCommon.py",
        "libfbxsdk_dll": scripts_dir / "libfbxsdk.dll",
    }


def _scan_fbx_sdk_source_dir(source_dir: Path) -> Dict[str, Any]:
    root = Path(source_dir)
    found: Dict[str, str] = {}
    missing: List[str] = []
    if not root.exists():
        return {
            "exists": False,
            "found": found,
            "missing": ["fbx binding (fbx-*.whl or fbx*.pyd)", "FbxCommon.py"],
        }

    fbx_wheel = _find_pattern_recursive(root, "fbx-*.whl")
    fbx_pyd = _find_pattern_recursive(root, "fbx*.pyd")
    fbx_common = _find_file_recursive(root, "FbxCommon.py")
    libfbxsdk = _find_file_recursive(root, "libfbxsdk.dll")

    if fbx_wheel is None and fbx_pyd is None:
        missing.append("fbx binding (fbx-*.whl or fbx*.pyd)")
    elif fbx_wheel is not None:
        found["fbx wheel"] = str(fbx_wheel)
    elif fbx_pyd is not None:
        found["fbx module"] = str(fbx_pyd)
    if fbx_common is None:
        missing.append("FbxCommon.py")
    else:
        found["FbxCommon.py"] = str(fbx_common)
    if libfbxsdk is not None:
        found["libfbxsdk.dll"] = str(libfbxsdk)
    return {"exists": True, "found": found, "missing": missing}


def _candidate_fbx_sdk_source_dirs(source_hint: str = "") -> List[Path]:
    candidates: List[Path] = []
    seen: set[str] = set()

    def _add(path_value: str | Path | None) -> None:
        if not path_value:
            return
        try:
            p = Path(path_value).expanduser()
        except Exception:
            return
        token = str(p).strip().lower()
        if not token or token in seen:
            return
        seen.add(token)
        candidates.append(p)

    _add(source_hint)
    _add(_load_last_fbx_sdk_source_dir())
    _add(_default_fbx_sdk_source_dir())
    _add(os.environ.get("FBX_SDK_SOURCE", ""))
    if os.name == "nt":
        for root in _windows_fbx_sdk_install_roots():
            _add(root)
            try:
                if root.exists():
                    for child in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
                        if child.is_dir():
                            _add(child)
            except Exception:
                pass
    return candidates


def _find_ready_fbx_source_dir(source_hint: str = "") -> str:
    for candidate in _candidate_fbx_sdk_source_dirs(source_hint):
        scan = _scan_fbx_sdk_source_dir(candidate)
        if scan.get("exists") and not list(scan.get("missing") or []):
            return str(candidate)
    return ""


def _default_windows_fbx_source_hint() -> Path:
    for root in _windows_fbx_sdk_install_roots():
        try:
            if root.exists():
                return root
        except Exception:
            pass
    return Path("C:/Program Files/Autodesk/FBX/FBX Python SDK")


def _default_fbx_windows_installer_path() -> Path:
    return _default_fbx_sdk_installer_dir() / FBX_WINDOWS_INSTALLER_FILENAME


def _download_fbx_windows_installer(installer_path: Path) -> Tuple[bool, str]:
    target = Path(installer_path)
    if target.exists():
        return True, f"Using cached Autodesk installer:\n{target}"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return False, f"Failed to prepare installer folder:\n{target.parent}\n\n{exc}"

    temp_path = target.with_suffix(target.suffix + ".part")
    try:
        req = urllib_request.Request(
            FBX_WINDOWS_INSTALLER_URL,
            headers={"User-Agent": "QubitMCP-FBXSetup/1.0"},
        )
        with urllib_request.urlopen(req, timeout=600) as response, temp_path.open("wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        os.replace(str(temp_path), str(target))
    except urllib_error.URLError as exc:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
        return (
            False,
            "Failed to download Autodesk FBX installer.\n"
            f"URL: {FBX_WINDOWS_INSTALLER_URL}\n\n{exc}",
        )
    except Exception as exc:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
        return False, f"Failed to download Autodesk FBX installer:\n{exc}"

    return True, f"Downloaded Autodesk installer:\n{target}"


def _pwsh_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_windows_installer_elevated_and_wait(installer_path: Path) -> Tuple[bool, str]:
    powershell_exe = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell_exe:
        return (
            False,
            "Installer requires administrator elevation, but PowerShell was not found.\n\n"
            "Run this installer manually as Administrator:\n"
            f"{installer_path}",
        )

    script = (
        "$ErrorActionPreference='Stop'; "
        f"$p = Start-Process -FilePath {_pwsh_quote(str(installer_path))} "
        f"-WorkingDirectory {_pwsh_quote(str(installer_path.parent))} "
        "-Verb RunAs -Wait -PassThru; "
        "if ($null -eq $p) { exit 0 } else { exit [int]$p.ExitCode }"
    )
    try:
        proc = subprocess.run(
            [
                powershell_exe,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return False, f"Failed to run installer with elevation:\n{exc}"

    code = int(getattr(proc, "returncode", 1))
    if code == 0:
        return True, "Autodesk FBX SDK installer completed."

    detail = (proc.stderr or proc.stdout or "").strip()
    lower = detail.lower()
    if "canceled" in lower or "cancelled" in lower:
        return (
            False,
            "Installer elevation was cancelled.\n\n"
            "Please approve the UAC prompt and run 'Install FBX Support' again.",
        )
    return (
        False,
        "Autodesk installer did not complete successfully after requesting elevation.\n"
        f"Exit code: {code}\n\n"
        + (detail or "No additional installer output."),
    )


def _find_file_recursive(root: Path, name: str) -> Path | None:
    try:
        direct = root / name
        if direct.exists() and direct.is_file():
            return direct
    except Exception:
        pass
    try:
        for candidate in root.rglob(name):
            if candidate.exists() and candidate.is_file():
                return candidate
    except Exception:
        return None
    return None


def _find_pattern_recursive(root: Path, pattern: str) -> Path | None:
    try:
        for candidate in root.rglob(pattern):
            if candidate.exists() and candidate.is_file():
                return candidate
    except Exception:
        return None
    return None


def _probe_fbxsdk_runtime() -> Dict[str, Any]:
    expected = _runtime_fbx_expected_paths()
    try:
        fbx_spec = importlib.util.find_spec("fbx")
    except Exception:
        fbx_spec = None
    try:
        fbxcommon_spec = importlib.util.find_spec("FbxCommon")
    except Exception:
        fbxcommon_spec = None

    fbx_runtime_import_ok = False
    fbx_runtime_manager_ok = False
    fbx_runtime_error = ""
    try:
        import fbx as fbx_mod  # type: ignore

        fbx_runtime_import_ok = True
        manager_ctor = getattr(fbx_mod, "FbxManager", None)
        create_fn = getattr(manager_ctor, "Create", None) if manager_ctor is not None else None
        if callable(create_fn):
            manager = None
            try:
                manager = create_fn()
                fbx_runtime_manager_ok = bool(manager is not None)
            finally:
                if manager is not None:
                    try:
                        manager.Destroy()
                    except Exception:
                        pass
        else:
            # If binding imports but no manager ctor, still treat runtime as usable.
            fbx_runtime_manager_ok = True
    except Exception as exc:
        fbx_runtime_error = str(exc)

    fbxcommon_import_ok = False
    fbxcommon_import_error = ""
    if fbxcommon_spec is not None:
        try:
            import FbxCommon as _fbx_common  # type: ignore

            _ = _fbx_common
            fbxcommon_import_ok = True
        except Exception as exc:
            fbxcommon_import_error = str(exc)

    dll_loadable = False
    dll_error = ""
    dll_candidates = ["libfbxsdk.dll", str(expected.get("libfbxsdk_dll", ""))]
    for candidate in dll_candidates:
        if not candidate:
            continue
        try:
            ctypes.CDLL(candidate)
            dll_loadable = True
            dll_error = ""
            break
        except Exception as exc:
            dll_error = str(exc)

    return {
        "ok": bool(fbx_runtime_import_ok and fbx_runtime_manager_ok),
        "fbx_found": bool(fbx_spec is not None),
        "fbx_origin": str(getattr(fbx_spec, "origin", "") or "") if fbx_spec is not None else "",
        "fbxcommon_found": bool(fbxcommon_spec is not None),
        "fbxcommon_origin": str(getattr(fbxcommon_spec, "origin", "") or "") if fbxcommon_spec is not None else "",
        "fbx_runtime_import_ok": bool(fbx_runtime_import_ok),
        "fbx_runtime_manager_ok": bool(fbx_runtime_manager_ok),
        "fbx_runtime_error": str(fbx_runtime_error or "").strip(),
        "fbxcommon_import_ok": bool(fbxcommon_import_ok),
        "fbxcommon_import_error": str(fbxcommon_import_error or "").strip(),
        "dll_loadable": bool(dll_loadable),
        "dll_error": str(dll_error or "").strip(),
        "expected": {
            "fbx_pyd": str(expected.get("fbx_pyd", "")),
            "fbxcommon_py": str(expected.get("fbxcommon_py", "")),
            "libfbxsdk_dll": str(expected.get("libfbxsdk_dll", "")),
        },
        "python_executable": str(sys.executable),
        "python_version": str(sys.version.split()[0]),
    }


def _install_fbxsdk_runtime_from_source(source_dir: Path) -> Tuple[bool, str]:
    source_dir = Path(source_dir)
    if not source_dir.exists():
        return False, f"Source folder does not exist:\n{source_dir}"

    expected = _runtime_fbx_expected_paths()
    fbx_wheel = _find_pattern_recursive(source_dir, "fbx-*.whl")
    fbx_pyd = _find_pattern_recursive(source_dir, "fbx*.pyd")
    fbx_common = _find_file_recursive(source_dir, "FbxCommon.py")
    libfbxsdk = _find_file_recursive(source_dir, "libfbxsdk.dll")

    missing: List[str] = []
    if fbx_wheel is None and fbx_pyd is None:
        missing.append("fbx binding (fbx-*.whl or fbx*.pyd)")
    if fbx_common is None:
        missing.append("FbxCommon.py")
    if missing:
        return (
            False,
            "Missing required files in selected folder:\n- "
            + "\n- ".join(missing)
            + f"\n\nSelected folder:\n{source_dir}",
        )

    installed_lines: List[str] = []
    try:
        if fbx_wheel is not None:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--force-reinstall", str(fbx_wheel)],
                check=False,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()
                return False, "Failed to install FBX wheel:\n" + (detail or str(fbx_wheel))
            installed_lines.append(f"wheel installed: {fbx_wheel}")
        elif fbx_pyd is not None:
            dst_mod = Path(expected["fbx_pyd"]).with_name(fbx_pyd.name)
            dst_mod.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(fbx_pyd), str(dst_mod))
            installed_lines.append(f"module copied: {dst_mod}")

        if fbx_common is not None:
            dst_common = Path(expected["fbxcommon_py"])
            dst_common.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(fbx_common), str(dst_common))
            installed_lines.append(f"FbxCommon.py copied: {dst_common}")

        if libfbxsdk is not None:
            dst_dll = Path(expected["libfbxsdk_dll"])
            dst_dll.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(libfbxsdk), str(dst_dll))
            installed_lines.append(f"libfbxsdk.dll copied: {dst_dll}")
        else:
            installed_lines.append("libfbxsdk.dll not found in source (optional for some wheel builds)")
    except Exception as exc:
        return False, f"Failed to install FBX runtime files:\n{exc}"

    try:
        importlib.invalidate_caches()
    except Exception:
        pass

    probe = _probe_fbxsdk_runtime()
    if not probe.get("ok"):
        details = [
            "FBX runtime files were copied, but runtime probe still failed.",
            "",
            f"fbx found: {probe.get('fbx_found')}",
            f"FbxCommon found: {probe.get('fbxcommon_found')}",
            f"fbx runtime import: {probe.get('fbx_runtime_import_ok')}",
            f"fbx manager create: {probe.get('fbx_runtime_manager_ok')}",
            f"libfbxsdk.dll loadable: {probe.get('dll_loadable')}",
        ]
        runtime_error = str(probe.get("fbx_runtime_error") or "").strip()
        if runtime_error:
            details.append(f"fbx runtime error: {runtime_error}")
        dll_error = str(probe.get("dll_error") or "").strip()
        if dll_error:
            details.append(f"dll note: {dll_error}")
        return False, "\n".join(details)

    lines = [
        "Autodesk FBX runtime installed successfully.",
        "",
    ]
    lines.extend(installed_lines)
    if not probe.get("fbxcommon_found"):
        lines.append("Warning: FbxCommon.py is missing (core FBX runtime may still work).")
    if not probe.get("dll_loadable"):
        lines.append("Note: libfbxsdk.dll not separately loadable (common with some wheel builds).")
    lines.extend(
        [
            "",
            f"Python runtime: {probe.get('python_executable')}",
            f"FBX module: {probe.get('fbx_origin')}",
        ]
    )
    return True, "\n".join(lines)


def _prompt_fbx_source_dir(parent, initial_dir: Path) -> str:
    if QtWidgets is None:
        return ""
    try:
        initial = str(initial_dir)
    except Exception:
        initial = ""
    try:
        return str(
            QtWidgets.QFileDialog.getExistingDirectory(
                parent,
                "Select FBX SDK Runtime Folder",
                initial,
            )
            or ""
        ).strip()
    except Exception:
        return ""


def _run_fbx_sdk_installer_and_wait(installer_path: Path) -> Tuple[bool, str]:
    installer = Path(installer_path)
    if not installer.exists():
        return False, f"Installer was not found:\n{installer}"
    if installer.suffix.lower() != ".exe":
        return False, f"Installer must be an .exe file:\n{installer}"
    try:
        proc = subprocess.run([str(installer)], cwd=str(installer.parent), check=False)
    except OSError as exc:
        if os.name == "nt" and int(getattr(exc, "winerror", 0) or 0) == 740:
            return _run_windows_installer_elevated_and_wait(installer)
        return False, f"Failed to run installer:\n{exc}"
    except Exception as exc:
        return False, f"Failed to run installer:\n{exc}"
    code = int(getattr(proc, "returncode", 1))
    if os.name == "nt" and code == 740:
        return _run_windows_installer_elevated_and_wait(installer)
    if code != 0:
        return (
            False,
            "Autodesk installer exited without completing successfully.\n"
            f"Exit code: {code}\n\n"
            "If you cancelled the installer, run setup again when ready.",
        )
    return (
        True,
        "Autodesk FBX SDK installer completed.",
    )


def _guess_fbx_installer_candidate() -> Path:
    saved = _load_last_fbx_sdk_installer_path()
    if saved:
        try:
            p = Path(saved)
            if p.exists():
                return p
        except Exception:
            pass
    default_installer_dir = _default_fbx_sdk_installer_dir()
    try:
        if default_installer_dir.exists():
            for cand in default_installer_dir.glob("*.exe"):
                if "fbx" in cand.name.lower():
                    return cand
    except Exception:
        pass
    return _default_fbx_windows_installer_path()


def _manual_install_fbxsdk_runtime(parent, source_dir: str) -> Tuple[bool, str, str]:
    start_dir = Path(source_dir) if source_dir else _default_windows_fbx_source_hint()
    picked = _prompt_fbx_source_dir(parent, start_dir)
    source = str(picked or "").strip()
    if not source:
        return False, "FBX SDK setup cancelled.", ""
    ok, report = _install_fbxsdk_runtime_from_source(Path(source))
    return ok, report, source


def _install_fbxsdk_runtime_guided(parent, source_dir_hint: str) -> Tuple[bool, str, str]:
    detected_source = _find_ready_fbx_source_dir(source_dir_hint)
    if detected_source:
        ok, report = _install_fbxsdk_runtime_from_source(Path(detected_source))
        return ok, report, detected_source

    if os.name != "nt":
        return (
            False,
            "Autodesk FBX runtime source was not found automatically.\n\n"
            "Please install Autodesk FBX Python SDK for this platform, then use "
            "'I already installed it...' to choose the folder with:\n"
            "- fbx-*.whl or fbx*.pyd\n"
            "- FbxCommon.py",
            "",
        )

    if QtWidgets is not None:
        proceed = QtWidgets.QMessageBox.question(
            parent,
            "FBX SDK Setup",
            "QubitMCP will download Autodesk FBX Python SDK installer from Autodesk,\n"
            "run it, then configure this app runtime automatically.\n\n"
            "Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes,
        )
        if proceed != QtWidgets.QMessageBox.Yes:
            return False, "FBX SDK setup cancelled.", ""

    installer_path = _guess_fbx_installer_candidate()
    download_report = ""
    if not installer_path.exists():
        ok_download, download_report = _download_fbx_windows_installer(installer_path)
        if not ok_download:
            return False, download_report, ""
    else:
        download_report = f"Using cached Autodesk installer:\n{installer_path}"
    _save_last_fbx_sdk_installer_path(str(installer_path))

    ok_run, run_report = _run_fbx_sdk_installer_and_wait(installer_path)
    if not ok_run:
        return False, run_report, ""

    detected_source = _find_ready_fbx_source_dir(source_dir_hint)
    if not detected_source:
        ok_manual, manual_report, manual_source = _manual_install_fbxsdk_runtime(parent, source_dir_hint)
        if ok_manual:
            return True, manual_report, manual_source
        return (
            False,
            run_report
            + "\n\nCould not auto-detect Autodesk FBX SDK source folder after installer.\n\n"
            + manual_report,
            manual_source,
        )

    ok_install, install_report = _install_fbxsdk_runtime_from_source(Path(detected_source))
    if ok_install:
        return (
            True,
            download_report + "\n\n" + run_report + "\n\n" + install_report,
            detected_source,
        )
    return (
        False,
        download_report
        + "\n\n"
        + run_report
        + "\n\nDetected source folder:\n"
        + detected_source
        + "\n\n"
        + install_report,
        detected_source,
    )


def _show_fbxsdk_setup_prompt(parent) -> None:
    if QtWidgets is None:
        return
    probe = _probe_fbxsdk_runtime()
    if probe.get("ok"):
        return

    default_source = _default_fbx_sdk_source_dir()
    source_dir = _load_last_fbx_sdk_source_dir() or str(default_source)
    auto_detected_source = _find_ready_fbx_source_dir(source_dir)
    source_scan = _scan_fbx_sdk_source_dir(Path(source_dir))
    installer_path = _guess_fbx_installer_candidate()
    try:
        default_source.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        _default_fbx_sdk_installer_dir().mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    msg = QtWidgets.QMessageBox(parent)
    msg.setIcon(QtWidgets.QMessageBox.Warning)
    msg.setWindowTitle("FBX SDK Setup")
    msg.setText("FBX Import needs Autodesk FBX SDK for full FBX compatibility.")
    source_status = "ready" if not source_scan.get("missing") else ("missing: " + ", ".join(source_scan.get("missing") or []))
    auto_detect_status = auto_detected_source if auto_detected_source else "<none>"
    if os.name == "nt":
        action_help = (
            "Install FBX Support will:\n"
            "1. Download Autodesk installer from the official URL\n"
            "2. Run the installer\n"
            "3. Auto-detect SDK files\n"
            "4. Install runtime files into this app venv"
        )
    else:
        action_help = (
            "Install FBX Support will try to auto-detect SDK files and install them\n"
            "into this app venv. Installer download is Windows-only."
        )
    msg.setInformativeText(
        f"{action_help}\n\n"
        "Install destination is fixed to this app runtime.\n"
        "Source folder contains Autodesk files to copy from.\n\n"
        f"Current source folder status: {source_status}\n"
        f"Auto-detected source folder: {auto_detect_status}\n\n"
        "Official Autodesk Windows installer URL:\n"
        f"{FBX_WINDOWS_INSTALLER_URL}\n\n"
        "Supported source layouts:\n"
        "- fbx-*.whl + FbxCommon.py\n"
        "- fbx*.pyd + FbxCommon.py (+ optional libfbxsdk.dll)"
    )
    details = "\n".join(
        [
            f"Python: {probe.get('python_version')} ({probe.get('python_executable')})",
            "",
            "Current source folder (user-managed):",
            source_dir,
            "",
            "Auto-detected source folder:",
            auto_detect_status,
            "",
            "Runtime destinations (app-managed):",
            str(probe.get("expected", {}).get("fbx_pyd", "")),
            str(probe.get("expected", {}).get("fbxcommon_py", "")),
            str(probe.get("expected", {}).get("libfbxsdk_dll", "")),
            "",
            "Recommended default source folder:",
            str(default_source),
            "",
            "Installer cache path:",
            str(installer_path),
            "",
            "Official Autodesk Windows installer URL:",
            FBX_WINDOWS_INSTALLER_URL,
        ]
    )
    msg.setDetailedText(details)

    btn_install = msg.addButton("Install FBX Support", QtWidgets.QMessageBox.AcceptRole)
    btn_manual = msg.addButton("I Already Installed It...", QtWidgets.QMessageBox.ActionRole)
    btn_copy_link = msg.addButton("Copy Download Link", QtWidgets.QMessageBox.ActionRole)
    btn_later = msg.addButton("Later", QtWidgets.QMessageBox.RejectRole)
    try:
        msg.setDefaultButton(btn_install)
    except Exception:
        pass
    suppress_cb = QtWidgets.QCheckBox("Don't ask again", msg)
    msg.setCheckBox(suppress_cb)
    msg.exec()

    clicked = msg.clickedButton()
    suppress = bool(suppress_cb.isChecked())
    if clicked is None or clicked is btn_later:
        if suppress:
            _save_fbx_sdk_prompt_suppressed(True)
        return

    if clicked is btn_copy_link:
        try:
            cb = QtWidgets.QApplication.clipboard()
            if cb is not None:
                cb.setText(FBX_WINDOWS_INSTALLER_URL)
        except Exception:
            pass
        QtWidgets.QMessageBox.information(
            parent,
            "FBX SDK Setup",
            "Download link copied to clipboard:\n\n" + FBX_WINDOWS_INSTALLER_URL,
        )
        if suppress:
            _save_fbx_sdk_prompt_suppressed(True)
        return

    if clicked is btn_manual:
        ok, report, resolved_source = _manual_install_fbxsdk_runtime(parent, source_dir)
    else:
        ok, report, resolved_source = _install_fbxsdk_runtime_guided(parent, source_dir)

    if ok:
        if resolved_source:
            _save_last_fbx_sdk_source_dir(resolved_source)
        _save_fbx_sdk_prompt_suppressed(False)
        QtWidgets.QMessageBox.information(parent, "FBX SDK Setup", report)
    else:
        QtWidgets.QMessageBox.warning(parent, "FBX SDK Setup", report)
        if suppress:
            _save_fbx_sdk_prompt_suppressed(True)


def _maybe_prompt_fbxsdk_setup(parent) -> None:
    global _FBX_SDK_PROMPT_SESSION_SHOWN
    if QtWidgets is None:
        return
    if _FBX_SDK_PROMPT_SESSION_SHOWN:
        return
    if _load_fbx_sdk_prompt_suppressed():
        return
    probe = _probe_fbxsdk_runtime()
    if probe.get("ok"):
        return

    _FBX_SDK_PROMPT_SESSION_SHOWN = True

    def _run() -> None:
        try:
            _show_fbxsdk_setup_prompt(parent)
        except Exception:
            pass

    if QtCore is not None:
        try:
            QtCore.QTimer.singleShot(0, _run)
            return
        except Exception:
            pass
    _run()


@dataclass
class SourceResolutionResult:
    status: str
    requested_sources: Dict[str, str] = field(default_factory=dict)
    effective_sources: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def message_lines(self) -> List[str]:
        lines = [f"Status: {self.status.upper()}"]
        for role in ROLE_PORTS:
            raw = (self.requested_sources.get(role) or "").strip()
            resolved = (self.effective_sources.get(role) or "").strip()
            if resolved:
                if raw and raw != resolved:
                    lines.append(f"{role}: {resolved} (requested: {raw})")
                elif (not raw) and role in ("capture_pose", "animated_pose"):
                    lines.append(f"{role}: {resolved} (defaulted from rest_geometry)")
                else:
                    lines.append(f"{role}: {resolved}")
            elif raw:
                lines.append(f"{role}: {raw} (unresolved)")
            else:
                lines.append(f"{role}: <none>")
        if self.errors:
            lines.append("Errors:")
            lines.extend(f"- {msg}" for msg in self.errors)
        if self.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {msg}" for msg in self.warnings)
        return lines


def _param_value(model, name: str) -> str:
    key = (name or "").strip().lower()
    for entry in (getattr(model, "params", None) or []):
        if (entry.get("name") or "").strip().lower() == key:
            return (entry.get("value") or "").strip()
    return ""


def _param_bool(model, name: str, default: bool = False) -> bool:
    raw = str(_param_value(model, name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _exclusive_joint_debug_flags(
    show_capture_joints: bool,
    show_animated_joints: bool,
) -> Tuple[bool, bool]:
    capture_enabled = bool(show_capture_joints)
    animated_enabled = bool(show_animated_joints)
    if capture_enabled and animated_enabled:
        # Keep this deterministic when loading older graphs that persisted both toggles on.
        animated_enabled = False
    return capture_enabled, animated_enabled


def _ensure_param(node_item, name: str, default: str = "") -> None:
    model = getattr(node_item, "model", None)
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    key = (name or "").strip().lower()
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            if "value" not in entry:
                entry["value"] = default
            model.params = params
            return
    params.append({"name": name, "value": default})
    model.params = params


def _remove_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    hidden_entry = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == store_key:
            hidden_entry = entry
            break
    if hidden_entry is None:
        return
    raw = hidden_entry.get("value", "")
    hidden = {tok.strip().lower() for tok in str(raw).split(",") if tok.strip()}
    for name in names or []:
        if name:
            hidden.discard(str(name).strip().lower())
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _ensure_hidden_params(model, names) -> None:
    if model is None:
        return
    params = list(getattr(model, "params", None) or [])
    store_key = "__ui_hidden_params"
    hidden_entry = None
    for entry in params:
        if (entry.get("name") or "").strip().lower() == store_key:
            hidden_entry = entry
            break
    if hidden_entry is None:
        hidden_entry = {"name": store_key, "value": ""}
        params.append(hidden_entry)
    raw = hidden_entry.get("value", "")
    hidden = {tok.strip().lower() for tok in str(raw).split(",") if tok.strip()}
    for name in names or []:
        if name:
            hidden.add(str(name).strip().lower())
    hidden_entry["value"] = ",".join(sorted(hidden))
    model.params = params


def _set_param_value(model, name: str, value: str) -> None:
    if model is None:
        return
    key = (name or "").strip().lower()
    params = list(getattr(model, "params", None) or [])
    for entry in params:
        if (entry.get("name") or "").strip().lower() == key:
            entry["value"] = value
            model.params = params
            return
    params.append({"name": name, "value": value})
    model.params = params


def _ensure_input(node_item, name: str) -> None:
    if hasattr(node_item, "ensure_input"):
        node_item.ensure_input(name)
    elif hasattr(node_item, "add_input_port"):
        node_item.add_input_port(name)
    elif hasattr(node_item, "add_input"):
        node_item.add_input(name)


def build_ports(node_item) -> None:
    for role in ROLE_PORTS:
        _ensure_param(node_item, role, "")
        _ensure_input(node_item, role)
    _ensure_param(node_item, "debug_log", "0")
    _ensure_param(node_item, "skin_weight_debug", "0")
    _ensure_param(node_item, "show_capture_joints", "0")
    _ensure_param(node_item, "show_animated_joints", "0")
    _ensure_hidden_params(
        getattr(node_item, "model", None),
        [
            "debug_log",
            "skin_weight_debug",
            "show_skin_weights",
            "show_capture_joints",
            "capture_joint_debug",
            "show_animated_joints",
            "animated_joint_debug",
        ],
    )
    # Stage 2 UX: keep role params visible/editable on the node.
    # Remove any legacy hidden flags from previous builds.
    _remove_hidden_params(getattr(node_item, "model", None), ROLE_PORTS)


def _ordered_in_edges(scene, item):
    try:
        return list(scene._ordered_in_edges(item))
    except Exception:
        try:
            return list(scene._in_edges(item))
        except Exception:
            return []


def _edge_dst_name(edge) -> str:
    return (
        (getattr(edge, "dst_port_name", None) or "")
        or (getattr(edge, "dst_label", None) or "")
        or (getattr(edge, "dst_name", None) or "")
    ).strip()


def _workflow_dir_for_node(node_item) -> Path | None:
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None

    scene_path = getattr(scene, "_filename", None) if scene is not None else None
    workflow_path = None
    if scene is not None:
        try:
            views = scene.views()
            if views:
                win = views[0].window()
                workflow_path = getattr(win, "_current_path", None)
        except Exception:
            pass

    workflow_path = workflow_path or scene_path
    if not workflow_path:
        return None
    try:
        return Path(workflow_path).parent
    except Exception:
        return None


def _resolve_existing_path(raw_path: str, base_dir: Path | None) -> Path | None:
    if not raw_path:
        return None
    try:
        path = Path(raw_path).expanduser()
    except Exception:
        return None

    try:
        if path.exists():
            return path.resolve()
    except Exception:
        pass

    if base_dir is None or path.is_absolute():
        return None
    try:
        alt = (base_dir / path).resolve()
        if alt.exists():
            return alt
    except Exception:
        return None
    return None


def _trace_source_item(scene, src_item):
    visited = set()
    current = src_item
    depth = 0
    while current is not None and depth < 8:
        cur_id = id(current)
        if cur_id in visited:
            return None, "source trace cycle detected."
        visited.add(cur_id)
        depth += 1

        model = getattr(current, "model", None)
        kind = (getattr(model, "kind", "") or "").strip().lower() if model is not None else ""
        if kind != "switch":
            return current, ""

        upstream = _ordered_in_edges(scene, current)
        if not upstream:
            return None, "switch input is not connected."
        current = getattr(upstream[0], "src", None)
    if depth >= 8:
        return None, "source trace exceeded maximum depth."
    return current, ""


def _source_path_from_item(src_item, role: str) -> Tuple[str, str]:
    model = getattr(src_item, "model", None)
    if model is None:
        return "", "connected source has no model."

    kind = (getattr(model, "kind", "") or "").strip().lower()
    name = (getattr(model, "name", "") or "").strip() or "<unnamed>"

    if kind in ("import", "html_preview"):
        path = _param_value(model, "path")
        if not path:
            return "", f"{role}: source node '{name}' has empty path."
        return path, ""

    if kind in FBX_KIND_ALIASES:
        path = (
            str(getattr(model, f"_fbx_resolved_{role}", "") or "").strip()
            or _param_value(model, f"resolved_{role}")
            or _param_value(model, role)
        )
        if not path:
            return "", f"{role}: upstream FBXImport node '{name}' has no resolved source."
        return path, ""

    return "", f"{role}: source node '{name}' has unsupported kind '{kind}'."


def _validate_fbx_path(role: str, raw_path: str, base_dir: Path | None) -> Tuple[str, str]:
    if not raw_path:
        return "", f"{role}: source is empty."
    path = _resolve_existing_path(raw_path, base_dir)
    if path is None:
        return "", f"{role}: file does not exist: {raw_path}"
    if path.suffix.lower() != ".fbx":
        return "", f"{role}: file is not .fbx: {path}"
    return str(path), ""


def _append_prefixed_messages(dst: List[str], prefix: str, messages) -> None:
    for msg in list(messages or []):
        text = str(msg or "").strip()
        if text:
            dst.append(f"{prefix}: {text}")


def _is_backend_unavailable(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    return ("pyassimp unavailable" in text) or ("fbx sdk unavailable" in text)


def _is_backend_parser_limitation(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    return "null pointer access" in text


def _has_fbxsdk_unavailable(exc: Exception) -> bool:
    text = str(exc or "").strip().lower()
    return "fbx sdk unavailable" in text


def _format_bind_ingest_failure(role: str, exc: Exception) -> str:
    message = str(exc or "").strip() or exc.__class__.__name__
    if "null pointer access" in message.lower():
        message = (
            f"{message} (pyassimp/assimp parser limitation for this FBX file)."
        )
    return f"{role}: bind ingest failed: {message}"


def _format_animation_ingest_failure(role: str, exc: Exception) -> str:
    message = str(exc or "").strip() or exc.__class__.__name__
    if "null pointer access" in message.lower():
        message = (
            f"{message} (pyassimp/assimp parser limitation for this FBX file)."
        )
    return f"{role}: animation ingest failed: {message}"


def _validate_bind_sources_stage3(
    *,
    effective: Dict[str, str],
    errors: List[str],
    warnings: List[str],
) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "rest_result": None,
        "capture_result": None,
        "capture_report": None,
    }
    rest_path = (effective.get("rest_geometry") or "").strip()
    if not rest_path:
        return state

    try:
        rest_result = ingest_fbx_bind_data(rest_path)
    except FBXBindIngestError as exc:
        if _is_backend_unavailable(exc) or _is_backend_parser_limitation(exc):
            warnings.append(
                "stage3 bind ingest skipped: "
                + _format_bind_ingest_failure("rest_geometry", exc)
            )
            if _has_fbxsdk_unavailable(exc):
                warnings.append(
                    "FBX SDK backend is not available in this runtime; "
                    "pyassimp fallback has limited FBX compatibility."
                )
        else:
            errors.append(_format_bind_ingest_failure("rest_geometry", exc))
        return state
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(_format_bind_ingest_failure("rest_geometry", exc))
        return state

    state["rest_result"] = rest_result
    _append_prefixed_messages(warnings, "rest_geometry", getattr(rest_result, "warnings", []))

    capture_path = (effective.get("capture_pose") or "").strip()
    if not capture_path or capture_path == rest_path:
        state["capture_result"] = rest_result
        if not capture_path:
            effective["capture_pose"] = rest_path
        return state

    try:
        capture_result = ingest_fbx_bind_data(
            capture_path,
            skeleton_name=rest_result.skeleton.name,
        )
    except FBXBindIngestError as exc:
        if _is_backend_unavailable(exc):
            warnings.append(
                f"capture_pose: bind ingest skipped ({exc}); using rest_geometry."
            )
        else:
            warnings.append(
                f"{_format_bind_ingest_failure('capture_pose', exc)}; override ignored."
            )
        effective["capture_pose"] = rest_path
        state["capture_result"] = rest_result
        return state
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(
            f"{_format_bind_ingest_failure('capture_pose', exc)}; override ignored."
        )
        effective["capture_pose"] = rest_path
        state["capture_result"] = rest_result
        return state

    _append_prefixed_messages(
        warnings, "capture_pose", getattr(capture_result, "warnings", [])
    )
    report = compare_skeleton_layout(rest_result.skeleton, capture_result.skeleton)
    state["capture_report"] = report
    _append_prefixed_messages(warnings, "capture_pose", report.warnings)
    if not report.compatible:
        for msg in report.errors:
            text = str(msg or "").strip()
            if text:
                warnings.append(f"capture_pose: {text}; override ignored.")
        effective["capture_pose"] = rest_path
        state["capture_result"] = rest_result
        return state

    state["capture_result"] = capture_result
    return state


def _validate_animation_sources_stage4(
    *,
    effective: Dict[str, str],
    bind_state: Dict[str, Any],
    warnings: List[str],
) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "rest_result": None,
        "animated_result": None,
        "alignment_summary": "",
        "alignment_metrics": None,
    }
    rest_path = (effective.get("rest_geometry") or "").strip()
    if not rest_path:
        return state

    skeleton = None
    capture_bind = bind_state.get("capture_result")
    if capture_bind is not None:
        skeleton = getattr(capture_bind, "skeleton", None)
    if skeleton is None:
        rest_bind = bind_state.get("rest_result")
        if rest_bind is not None:
            skeleton = getattr(rest_bind, "skeleton", None)

    def _append_alignment_warning(metrics_obj) -> None:
        if not isinstance(metrics_obj, dict):
            return
        if float(metrics_obj.get("failed", 0.0) or 0.0) >= 0.5:
            return
        mean_r = float(metrics_obj.get("mean_r", 0.0) or 0.0)
        max_r = float(metrics_obj.get("max_r", 0.0) or 0.0)
        mean_t = float(metrics_obj.get("mean_t", 0.0) or 0.0)
        max_t = float(metrics_obj.get("max_t", 0.0) or 0.0)
        if (mean_r > 25.0) or (max_r > 120.0) or (max_t > 1.0):
            warnings.append(
                "animated_pose: frame0 differs strongly from bind pose "
                f"(mean_t={mean_t:.5f}, max_t={max_t:.5f}, "
                f"mean_r={mean_r:.3f}deg, max_r={max_r:.3f}deg). "
                "If mesh explodes, this likely indicates bind/animation basis mismatch."
            )

    def _ingest_animation(path_value: str, role: str):
        try:
            result = ingest_fbx_animation_data(path_value, skeleton=skeleton)
        except FBXAnimationIngestError as exc:
            if _is_backend_unavailable(exc) or _is_backend_parser_limitation(exc):
                warnings.append(
                    "stage4 animation ingest skipped: "
                    + _format_animation_ingest_failure(role, exc)
                )
                if _has_fbxsdk_unavailable(exc):
                    warnings.append(
                        "FBX SDK backend is not available in this runtime; "
                        "pyassimp fallback has limited FBX animation compatibility."
                    )
            else:
                warnings.append(_format_animation_ingest_failure(role, exc))
            return None
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(_format_animation_ingest_failure(role, exc))
            return None

        _append_prefixed_messages(warnings, role, getattr(result, "warnings", []))
        if not list(getattr(result, "clips", []) or []):
            warnings.append(f"{role}: no animation clips were found.")
        return result

    rest_result = _ingest_animation(rest_path, "rest_geometry")
    state["rest_result"] = rest_result

    animated_path = (effective.get("animated_pose") or "").strip()
    if not animated_path or animated_path == rest_path:
        if not animated_path:
            effective["animated_pose"] = rest_path
        state["animated_result"] = rest_result
        state["alignment_summary"] = _clip_alignment_summary(skeleton, rest_result)
        state["alignment_metrics"] = _clip_alignment_metrics(skeleton, rest_result)
        _append_alignment_warning(state.get("alignment_metrics"))
        return state

    animated_result = _ingest_animation(animated_path, "animated_pose")
    if animated_result is None:
        if rest_result is not None:
            warnings.append("animated_pose: using rest_geometry animation clips.")
            effective["animated_pose"] = rest_path
            state["animated_result"] = rest_result
            state["alignment_summary"] = _clip_alignment_summary(skeleton, rest_result)
            state["alignment_metrics"] = _clip_alignment_metrics(skeleton, rest_result)
            _append_alignment_warning(state.get("alignment_metrics"))
        return state

    if (not list(getattr(animated_result, "clips", []) or [])) and (
        rest_result is not None and list(getattr(rest_result, "clips", []) or [])
    ):
        warnings.append("animated_pose: no clips found; using rest_geometry animation clips.")
        effective["animated_pose"] = rest_path
        state["animated_result"] = rest_result
        state["alignment_summary"] = _clip_alignment_summary(skeleton, rest_result)
        state["alignment_metrics"] = _clip_alignment_metrics(skeleton, rest_result)
        _append_alignment_warning(state.get("alignment_metrics"))
        return state

    state["animated_result"] = animated_result
    state["alignment_summary"] = _clip_alignment_summary(skeleton, animated_result)
    state["alignment_metrics"] = _clip_alignment_metrics(skeleton, animated_result)
    _append_alignment_warning(state.get("alignment_metrics"))
    return state


def resolve_fbx_import_sources(
    node_item,
    *,
    base_dir: Path | None = None,
    persist: bool = False,
    validate_bind_data: bool | None = None,
    validate_animation_data: bool = False,
) -> SourceResolutionResult:
    model = getattr(node_item, "model", None)
    scene = None
    try:
        scene = node_item.scene()
    except Exception:
        scene = None

    if base_dir is None:
        base_dir = _workflow_dir_for_node(node_item)

    requested = {role: "" for role in ROLE_PORTS}
    effective = {role: "" for role in ROLE_PORTS}
    errors: List[str] = []
    warnings: List[str] = []

    for role in ROLE_PORTS:
        role_edges = []
        if scene is not None:
            for edge in _ordered_in_edges(scene, node_item):
                if _edge_dst_name(edge).lower() == role:
                    role_edges.append(edge)

        wired_path = ""
        if role_edges:
            if len(role_edges) > 1:
                warnings.append(
                    f"{role}: multiple inputs connected; using first edge deterministically."
                )
            src = getattr(role_edges[0], "src", None)
            src_item, trace_issue = _trace_source_item(scene, src)
            if trace_issue:
                warnings.append(f"{role}: {trace_issue}")
            if src_item is not None:
                wired_path, source_issue = _source_path_from_item(src_item, role)
                if source_issue:
                    warnings.append(source_issue)

        param_path = _param_value(model, role)
        if wired_path:
            requested[role] = wired_path
        else:
            requested[role] = param_path
            if role_edges and param_path:
                warnings.append(
                    f"{role}: wired source unresolved; using parameter fallback."
                )

    rest_resolved, rest_issue = _validate_fbx_path(
        "rest_geometry", requested["rest_geometry"], base_dir
    )
    if rest_issue:
        errors.append(rest_issue)
    else:
        effective["rest_geometry"] = rest_resolved

    for role in ("capture_pose", "animated_pose"):
        raw = requested[role]
        if not raw:
            continue
        resolved, issue = _validate_fbx_path(role, raw, base_dir)
        if issue:
            warnings.append(f"{issue}; override ignored.")
        else:
            effective[role] = resolved

    if not effective["rest_geometry"]:
        if effective["capture_pose"] or effective["animated_pose"]:
            warnings.append(
                "capture_pose/animated_pose overrides were ignored because rest_geometry is invalid."
            )
        effective["capture_pose"] = ""
        effective["animated_pose"] = ""
    else:
        for role in ("capture_pose", "animated_pose"):
            if not effective[role]:
                effective[role] = effective["rest_geometry"]

    bind_validation_enabled = bool(persist) if validate_bind_data is None else bool(validate_bind_data)
    bind_state: Dict[str, Any] = {}
    if bind_validation_enabled and effective["rest_geometry"]:
        bind_state = _validate_bind_sources_stage3(
            effective=effective,
            errors=errors,
            warnings=warnings,
        )

    animation_validation_enabled = bool(validate_animation_data)
    animation_state: Dict[str, Any] = {}
    if animation_validation_enabled and effective["rest_geometry"]:
        animation_state = _validate_animation_sources_stage4(
            effective=effective,
            bind_state=bind_state,
            warnings=warnings,
        )

    status = "error" if errors else ("warning" if warnings else "ok")
    result = SourceResolutionResult(
        status=status,
        requested_sources=requested,
        effective_sources=effective,
        errors=errors,
        warnings=warnings,
    )

    if persist and model is not None:
        try:
            setattr(model, "_fbx_resolved_rest_geometry", effective["rest_geometry"])
            setattr(model, "_fbx_resolved_capture_pose", effective["capture_pose"])
            setattr(model, "_fbx_resolved_animated_pose", effective["animated_pose"])
            setattr(model, "_fbx_validation_state", status)
            setattr(model, "_fbx_validation_errors", list(errors))
            setattr(model, "_fbx_validation_warnings", list(warnings))
            setattr(model, "_fbx_validation_messages", list(result.message_lines()))
            setattr(model, "_fbx_bind_validation_enabled", bool(bind_validation_enabled))
            setattr(model, "_fbx_bind_rest_result", bind_state.get("rest_result"))
            setattr(model, "_fbx_bind_capture_result", bind_state.get("capture_result"))
            setattr(model, "_fbx_bind_capture_report", bind_state.get("capture_report"))
            setattr(model, "_fbx_anim_validation_enabled", bool(animation_validation_enabled))
            setattr(model, "_fbx_anim_rest_result", animation_state.get("rest_result"))
            setattr(model, "_fbx_anim_animated_result", animation_state.get("animated_result"))
            setattr(model, "_fbx_anim_alignment_summary", str(animation_state.get("alignment_summary") or ""))
        except Exception:
            pass

    return result


def _compact_source_line(role: str, path_value: str) -> str:
    raw = (path_value or "").strip()
    if not raw:
        return f"{role}: <none>"
    try:
        name = Path(raw).name
    except Exception:
        name = raw
    return f"{role}: {name}"


def _clip_summary_text(animation_result) -> str:
    if animation_result is None:
        return "clips: <unavailable>"
    try:
        clips = list(getattr(animation_result, "clips", []) or [])
    except Exception:
        clips = []
    if not clips:
        return "clips: 0"
    first = clips[0]
    try:
        first_name = str(getattr(first, "name", "") or "").strip() or "clip_0"
    except Exception:
        first_name = "clip_0"
    try:
        start = float(getattr(first, "start_time", 0.0) or 0.0)
    except Exception:
        start = 0.0
    try:
        end = float(getattr(first, "end_time", start) or start)
    except Exception:
        end = start
    try:
        track_count = int(len(getattr(first, "tracks", []) or []))
    except Exception:
        track_count = 0
    return (
        f"clips: {len(clips)}"
        f" | first: {first_name}"
        f" [{start:.3f}s-{end:.3f}s]"
        f" tracks={track_count}"
    )


def _quat_angle_deg(a, b) -> float:
    try:
        ax, ay, az, aw = (float(a[0]), float(a[1]), float(a[2]), float(a[3]))
        bx, by, bz, bw = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
    except Exception:
        return 0.0
    na = math.sqrt(max(1.0e-16, (ax * ax) + (ay * ay) + (az * az) + (aw * aw)))
    nb = math.sqrt(max(1.0e-16, (bx * bx) + (by * by) + (bz * bz) + (bw * bw)))
    ax, ay, az, aw = ax / na, ay / na, az / na, aw / na
    bx, by, bz, bw = bx / nb, by / nb, bz / nb, bw / nb
    dot = abs((ax * bx) + (ay * by) + (az * bz) + (aw * bw))
    dot = max(-1.0, min(1.0, dot))
    return math.degrees(2.0 * math.acos(dot))


def _clip_alignment_metrics(skeleton, animation_result) -> Dict[str, float] | None:
    if skeleton is None:
        return None
    if animation_result is None:
        return None
    try:
        clips = list(getattr(animation_result, "clips", []) or [])
    except Exception:
        clips = []
    if not clips:
        return None
    clip = clips[0]
    try:
        sample_time = float(getattr(clip, "start_time", 0.0) or 0.0)
    except Exception:
        sample_time = 0.0
    try:
        evaluation = evaluate_rig_at_time(
            skeleton,
            clip,
            sample_time,
            loop=False,
        )
    except Exception as exc:
        _ = exc
        return {"failed": 1.0}

    joints = list(getattr(skeleton, "joints", []) or [])
    local = list(getattr(evaluation, "local_transforms", []) or [])
    count = min(len(joints), len(local))
    if count <= 0:
        return None

    t_sum = 0.0
    r_sum = 0.0
    t_max = 0.0
    r_max = 0.0
    for idx in range(count):
        bind_xf = getattr(joints[idx], "local_bind", None)
        anim_xf = local[idx]
        if bind_xf is None or anim_xf is None:
            continue
        try:
            btx, bty, btz = bind_xf.translation
            atx, aty, atz = anim_xf.translation
            dt = math.sqrt(
                (float(atx) - float(btx)) ** 2
                + (float(aty) - float(bty)) ** 2
                + (float(atz) - float(btz)) ** 2
            )
        except Exception:
            dt = 0.0
        dr = _quat_angle_deg(
            getattr(bind_xf, "rotation", (0.0, 0.0, 0.0, 1.0)),
            getattr(anim_xf, "rotation", (0.0, 0.0, 0.0, 1.0)),
        )
        t_sum += float(dt)
        r_sum += float(dr)
        t_max = max(float(t_max), float(dt))
        r_max = max(float(r_max), float(dr))

    t_mean = t_sum / float(count)
    r_mean = r_sum / float(count)
    return {
        "sample_time": float(sample_time),
        "joint_count": float(count),
        "mean_t": float(t_mean),
        "max_t": float(t_max),
        "mean_r": float(r_mean),
        "max_r": float(r_max),
    }


def _clip_alignment_summary(skeleton, animation_result) -> str:
    metrics = _clip_alignment_metrics(skeleton, animation_result)
    if not metrics:
        return "align: <unavailable>"
    if float(metrics.get("failed", 0.0) or 0.0) > 0.5:
        return "align: failed"
    t_mean = float(metrics.get("mean_t", 0.0) or 0.0)
    t_max = float(metrics.get("max_t", 0.0) or 0.0)
    r_mean = float(metrics.get("mean_r", 0.0) or 0.0)
    r_max = float(metrics.get("max_r", 0.0) or 0.0)
    return (
        f"align: frame0-vs-bind mean_t={t_mean:.5f} max_t={t_max:.5f} "
        f"mean_r={r_mean:.3f}deg max_r={r_max:.3f}deg"
    )


def _status_presentable_text(result_status: str, *, bind_validated: bool) -> Tuple[str, str]:
    status = (result_status or "").strip().lower()
    if status == "error":
        return "ERROR", "#ef4444"
    if status == "warning":
        if bind_validated:
            return "WARNING", "#f59e0b"
        return "WARNING (paths)", "#f59e0b"
    if bind_validated:
        return "OK", "#22c55e"
    return "UNVALIDATED (paths ok)", "#94a3b8"


def _build_preview_asset(model, result: SourceResolutionResult) -> Dict[str, Any] | None:
    rest_path = str(result.effective_sources.get("rest_geometry", "") or "").strip()
    if not rest_path:
        return None
    owner = str(getattr(model, "name", "") or "").strip()
    if not owner:
        try:
            owner = Path(rest_path).stem
        except Exception:
            owner = "fbx_import"
    asset: Dict[str, Any] = {
        "path": rest_path,
        "texture": "",
        "node": owner,
        "ext": ".fbx",
        "visible": True,
    }
    bind_result = (
        getattr(model, "_fbx_bind_capture_result", None)
        or getattr(model, "_fbx_bind_rest_result", None)
    )
    skeleton = getattr(bind_result, "skeleton", None) if bind_result is not None else None
    meshes = list(getattr(bind_result, "meshes", []) or []) if bind_result is not None else []
    if skeleton is None:
        return asset

    animation_result = (
        getattr(model, "_fbx_anim_animated_result", None)
        or getattr(model, "_fbx_anim_rest_result", None)
    )
    clip = None
    try:
        clips = list(getattr(animation_result, "clips", []) or [])
    except Exception:
        clips = []
    if clips:
        clip = clips[0]
    weight_debug = _param_bool(
        model,
        "skin_weight_debug",
        default=_param_bool(model, "show_skin_weights", default=False),
    )
    show_capture_joints = _param_bool(
        model,
        "show_capture_joints",
        default=_param_bool(model, "capture_joint_debug", default=False),
    )
    show_animated_joints = _param_bool(
        model,
        "show_animated_joints",
        default=_param_bool(model, "animated_joint_debug", default=False),
    )
    show_capture_joints, show_animated_joints = _exclusive_joint_debug_flags(
        show_capture_joints,
        show_animated_joints,
    )
    debug_log = _param_bool(model, "debug_log", default=False)
    asset["fbx_rig_context"] = {
        "skeleton": skeleton,
        "clip": clip,
        "meshes": meshes,
        "loop": True,
        "mesh_skinning_enabled": True,
        "skin_weight_debug": bool(weight_debug),
        "show_capture_joints": bool(show_capture_joints),
        "show_animated_joints": bool(show_animated_joints),
        "fbx_debug_log": bool(debug_log),
    }
    asset["fbx_debug_log"] = bool(debug_log)
    return asset


def augment_infocard_footer(card, footer_layout) -> bool:
    if QtWidgets is None or QtGui is None:
        return False

    node = getattr(card, "_node_ref", None)
    scene = getattr(card, "_graph_scene", None)
    if node is None or scene is None:
        return False

    kind = (getattr(node, "kind", "") or "").strip().lower()
    if kind not in FBX_KIND_ALIASES:
        return False
    _ensure_hidden_params(
        node,
        [
            "debug_log",
            "skin_weight_debug",
            "show_skin_weights",
            "show_capture_joints",
            "capture_joint_debug",
            "show_animated_joints",
            "animated_joint_debug",
        ],
    )

    def _node_item():
        try:
            return scene._node_items.get(node.name)
        except Exception:
            return None

    status_label = QtWidgets.QLabel("Status: unresolved")
    status_label.setStyleSheet("color:#94a3b8;")
    detail_box = QtWidgets.QPlainTextEdit("")
    detail_box.setReadOnly(True)
    detail_box.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
    detail_box.setMinimumHeight(96)
    try:
        detail_box.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
    except Exception:
        pass
    detail_box.setStyleSheet(
        "QPlainTextEdit {"
        "color:#cbd5e1;"
        "background:#0f172a;"
        "border:1px solid #1e293b;"
        "border-radius:4px;"
        "padding:6px;"
        "}"
    )
    try:
        detail_box.setPlaceholderText("Validation details will appear here.")
    except Exception:
        pass

    button = QtWidgets.QPushButton("Validate FBX Sources")
    button.setToolTip("Resolve rest/capture/animated source roles and validate compatibility.")
    setup_button = QtWidgets.QPushButton("Setup FBX SDK")
    setup_button.setToolTip("Install or configure Autodesk FBX SDK runtime for this app.")
    view_button = QtWidgets.QPushButton("View")
    view_button.setToolTip("Open FBXImport output in the 3D viewport.")
    copy_button = QtWidgets.QPushButton("Copy Report")
    copy_button.setToolTip("Copy the full FBX validation report to clipboard.")
    weight_debug_toggle = QtWidgets.QCheckBox("Skin Weight Colors")
    weight_debug_toggle.setToolTip("Colorize the mesh by per-joint skinning weights.")
    weight_debug_toggle.setStyleSheet("QCheckBox{color:#cbd5e1;}")
    capture_joints_toggle = QtWidgets.QCheckBox("Show Capture Joints")
    capture_joints_toggle.setToolTip("Draw capture-pose skeleton joints on top of the mesh.")
    capture_joints_toggle.setStyleSheet("QCheckBox{color:#cbd5e1;}")
    animated_joints_toggle = QtWidgets.QCheckBox("Show Animated Joints")
    animated_joints_toggle.setToolTip("Draw animated skeleton joints on top of the mesh.")
    animated_joints_toggle.setStyleSheet("QCheckBox{color:#cbd5e1;}")
    try:
        item = _node_item()
        model_obj = getattr(item, "model", None) if item is not None else None
        weight_debug_toggle.setChecked(
            _param_bool(
                model_obj,
                "skin_weight_debug",
                default=_param_bool(model_obj, "show_skin_weights", default=False),
            )
        )
        checked_capture = _param_bool(
            model_obj,
            "show_capture_joints",
            default=_param_bool(model_obj, "capture_joint_debug", default=False),
        )
        checked_animated = _param_bool(
            model_obj,
            "show_animated_joints",
            default=_param_bool(model_obj, "animated_joint_debug", default=False),
        )
        checked_capture, checked_animated = _exclusive_joint_debug_flags(
            checked_capture,
            checked_animated,
        )
        capture_joints_toggle.setChecked(bool(checked_capture))
        animated_joints_toggle.setChecked(bool(checked_animated))
    except Exception:
        pass

    container = QtWidgets.QWidget(card)
    if QtWidgets is not None:
        try:
            container.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Preferred,
            )
        except Exception:
            pass
    stack = QtWidgets.QVBoxLayout(container)
    stack.setContentsMargins(0, 0, 0, 0)
    stack.setSpacing(6)

    button_row = QtWidgets.QHBoxLayout()
    button_row.setContentsMargins(0, 0, 0, 0)
    button_row.setSpacing(8)
    button_row.addWidget(setup_button)
    button_row.addWidget(button)
    button_row.addWidget(view_button)
    button_row.addWidget(copy_button)
    button_row.addStretch(1)
    toggle_row = QtWidgets.QHBoxLayout()
    toggle_row.setContentsMargins(0, 0, 0, 0)
    toggle_row.setSpacing(8)
    toggle_row.addWidget(weight_debug_toggle)
    toggle_row.addWidget(capture_joints_toggle)
    toggle_row.addWidget(animated_joints_toggle)
    toggle_row.addStretch(1)

    stack.addLayout(button_row)
    stack.addWidget(status_label)
    stack.addWidget(detail_box, 1)
    stack.addLayout(toggle_row)
    insert_idx = footer_layout.count()
    footer_layout.addWidget(container, 100)
    try:
        footer_layout.setStretch(insert_idx, 100)
    except Exception:
        pass

    report_text_holder = {"value": ""}

    def _refresh(*_args, persist: bool = False, toast: bool = False):
        item = _node_item()
        if item is None:
            status_label.setText("Status: no node item")
            status_label.setStyleSheet("color:#f59e0b;")
            detail_box.setPlainText("Connect this node to the graph canvas.")
            return None
        model_obj = getattr(item, "model", None)
        try:
            checked = _param_bool(
                model_obj,
                "skin_weight_debug",
                default=_param_bool(model_obj, "show_skin_weights", default=False),
            )
            weight_debug_toggle.blockSignals(True)
            weight_debug_toggle.setChecked(bool(checked))
        except Exception:
            pass
        finally:
            try:
                weight_debug_toggle.blockSignals(False)
            except Exception:
                pass
        checked_capture = False
        checked_animated = False
        try:
            checked_capture = _param_bool(
                model_obj,
                "show_capture_joints",
                default=_param_bool(model_obj, "capture_joint_debug", default=False),
            )
        except Exception:
            checked_capture = False
        try:
            checked_animated = _param_bool(
                model_obj,
                "show_animated_joints",
                default=_param_bool(model_obj, "animated_joint_debug", default=False),
            )
        except Exception:
            checked_animated = False
        checked_capture, checked_animated = _exclusive_joint_debug_flags(
            checked_capture,
            checked_animated,
        )
        try:
            capture_joints_toggle.blockSignals(True)
            capture_joints_toggle.setChecked(bool(checked_capture))
        except Exception:
            pass
        finally:
            try:
                capture_joints_toggle.blockSignals(False)
            except Exception:
                pass
        try:
            animated_joints_toggle.blockSignals(True)
            animated_joints_toggle.setChecked(bool(checked_animated))
        except Exception:
            pass
        finally:
            try:
                animated_joints_toggle.blockSignals(False)
            except Exception:
                pass

        bind_validated = bool(persist)
        result = resolve_fbx_import_sources(
            item,
            persist=persist,
            validate_bind_data=bind_validated,
            validate_animation_data=bind_validated,
        )
        status_text, status_color = _status_presentable_text(
            result.status, bind_validated=bind_validated
        )
        status_label.setStyleSheet(f"color:{status_color};")
        status_label.setText(f"Status: {status_text}")

        detail_lines = [
            _compact_source_line("rest", result.effective_sources.get("rest_geometry", "")),
            _compact_source_line("capture", result.effective_sources.get("capture_pose", "")),
            _compact_source_line("animated", result.effective_sources.get("animated_pose", "")),
        ]
        if bind_validated:
            model_obj = getattr(item, "model", None)
            animation_result = (
                getattr(model_obj, "_fbx_anim_animated_result", None)
                or getattr(model_obj, "_fbx_anim_rest_result", None)
            )
            detail_lines.append(_clip_summary_text(animation_result))
            align_text = str(getattr(model_obj, "_fbx_anim_alignment_summary", "") or "").strip()
            if align_text:
                detail_lines.append(align_text)
        detail_box.setPlainText("\n".join(detail_lines))
        tooltip_lines = []
        if not bind_validated and not result.errors:
            tooltip_lines.append("Bind and animation ingest not run yet. Click 'Validate FBX Sources'.")
        tooltip_lines.extend(result.message_lines())
        report_text = "\n".join(result.message_lines())
        report_text_holder["value"] = report_text
        detail_box.setToolTip("\n".join(tooltip_lines))

        if toast:
            report = report_text
            if result.status in ("error", "warning"):
                QtWidgets.QMessageBox.warning(card, "FBXImport Validation", report)
            else:
                QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), report, card)
        return result

    def _on_validate_clicked():
        _refresh(persist=True, toast=True)

    def _on_setup_clicked():
        _show_fbxsdk_setup_prompt(card)
        _refresh(persist=False, toast=False)

    def _on_copy_clicked():
        text = str(report_text_holder.get("value") or "").strip()
        if not text:
            text = str(detail_box.toPlainText() or "").strip()
        if not text:
            return
        try:
            clipboard = QtWidgets.QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
        except Exception:
            pass
        try:
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), "FBX report copied.", card)
        except Exception:
            pass

    def _on_view_clicked():
        item = _node_item()
        if item is None:
            QtWidgets.QMessageBox.warning(card, "FBXImport View", "Node item is not available.")
            return
        result = _refresh(persist=True, toast=False)
        if result is None:
            return
        if result.status == "error":
            QtWidgets.QMessageBox.warning(
                card,
                "FBXImport View",
                "\n".join(result.message_lines()),
            )
            return

        model = getattr(item, "model", None)
        asset = _build_preview_asset(model, result)
        if not isinstance(asset, dict):
            QtWidgets.QMessageBox.warning(
                card,
                "FBXImport View",
                "rest_geometry is not resolved. Validate sources first.",
            )
            return

        win = card.window()
        try:
            glv = getattr(win, "gl_view", None) if win is not None else None
            path_text = str(asset.get("path") or "").strip()
            context_obj = asset.get("fbx_rig_context")
            if glv is not None and path_text and isinstance(context_obj, dict):
                path_obj = Path(path_text)
                try:
                    cache_key = str(path_obj.resolve())
                except Exception:
                    cache_key = str(path_obj)
                try:
                    mtime = float(path_obj.stat().st_mtime)
                except Exception:
                    mtime = None
                cache = getattr(glv, "_mgl_fbx_rig_context_cache", None)
                if not isinstance(cache, dict):
                    cache = {}
                cache_entry = {"mtime": mtime, "context": dict(context_obj)}
                cache[cache_key] = cache_entry
                cache[str(path_obj)] = cache_entry
                setattr(glv, "_mgl_fbx_rig_context_cache", cache)
        except Exception:
            pass

        opened = False
        scene_handler = getattr(win, "open_scene_assets", None) if win is not None else None
        if callable(scene_handler):
            try:
                scene_handler([asset], frame=True)
                opened = True
            except Exception:
                opened = False
        if not opened:
            model_handler = getattr(win, "open_3d_model", None) if win is not None else None
            if callable(model_handler):
                try:
                    model_handler(str(asset.get("path") or ""), None, frame=True)
                    opened = True
                except Exception:
                    opened = False
        if not opened:
            QtWidgets.QMessageBox.warning(card, "FBXImport View", "3D view is not available.")
            return

        try:
            glv = getattr(win, "gl_view", None) if win is not None else None
            if glv is not None:
                toggle = getattr(glv, "_mgl_wireframe_toggle", None)
                if toggle is not None and not bool(toggle.isChecked()):
                    toggle.setChecked(True)
        except Exception:
            pass

    def _set_node_param(name: str, value: str) -> None:
        item = _node_item()
        if item is None:
            return
        model_obj = getattr(item, "model", None)
        setter = getattr(item, "_set_param_value", None)
        setter_ok = False
        if callable(setter):
            try:
                setter(name, value, rebuild=False, notify_scene=True)
                setter_ok = True
            except TypeError:
                try:
                    setter(name, value)
                    setter_ok = True
                except Exception:
                    pass
            except Exception:
                pass
        _set_param_value(model_obj, name, value)
        try:
            if model_obj is not None and hasattr(scene, "paramChanged") and not setter_ok:
                scene.paramChanged.emit(
                    getattr(model_obj, "name", "") or "",
                    list(getattr(model_obj, "params", None) or []),
                )
        except Exception:
            pass

    def _on_weight_debug_toggled(checked: bool):
        value = "1" if bool(checked) else "0"
        _set_node_param("skin_weight_debug", value)
        _set_node_param("show_skin_weights", value)

    def _set_joint_debug_flags(capture_enabled: bool, animated_enabled: bool) -> None:
        capture_enabled, animated_enabled = _exclusive_joint_debug_flags(
            capture_enabled,
            animated_enabled,
        )
        capture_value = "1" if bool(capture_enabled) else "0"
        animated_value = "1" if bool(animated_enabled) else "0"
        _set_node_param("show_capture_joints", capture_value)
        _set_node_param("capture_joint_debug", capture_value)
        _set_node_param("show_animated_joints", animated_value)
        _set_node_param("animated_joint_debug", animated_value)
        try:
            capture_joints_toggle.blockSignals(True)
            capture_joints_toggle.setChecked(bool(capture_enabled))
        except Exception:
            pass
        finally:
            try:
                capture_joints_toggle.blockSignals(False)
            except Exception:
                pass
        try:
            animated_joints_toggle.blockSignals(True)
            animated_joints_toggle.setChecked(bool(animated_enabled))
        except Exception:
            pass
        finally:
            try:
                animated_joints_toggle.blockSignals(False)
            except Exception:
                pass

    def _on_capture_joints_toggled(checked: bool):
        capture_enabled = bool(checked)
        animated_enabled = bool(animated_joints_toggle.isChecked()) and (not capture_enabled)
        _set_joint_debug_flags(capture_enabled, animated_enabled)
        try:
            _on_view_clicked()
        except Exception:
            pass

    def _on_animated_joints_toggled(checked: bool):
        animated_enabled = bool(checked)
        capture_enabled = bool(capture_joints_toggle.isChecked()) and (not animated_enabled)
        _set_joint_debug_flags(capture_enabled, animated_enabled)
        try:
            _on_view_clicked()
        except Exception:
            pass

    button.clicked.connect(_on_validate_clicked)
    setup_button.clicked.connect(_on_setup_clicked)
    copy_button.clicked.connect(_on_copy_clicked)
    view_button.clicked.connect(_on_view_clicked)
    weight_debug_toggle.toggled.connect(_on_weight_debug_toggled)
    capture_joints_toggle.toggled.connect(_on_capture_joints_toggled)
    animated_joints_toggle.toggled.connect(_on_animated_joints_toggled)

    def _on_links_changed(*_args):
        _refresh(persist=False, toast=False)

    def _on_param_changed(changed_name, *_args):
        item = _node_item()
        if item is None:
            return
        if _param_change_relevant(item, changed_name):
            _refresh(persist=False, toast=False)

    try:
        scene.linksChanged.connect(_on_links_changed)
    except Exception:
        pass
    try:
        scene.paramChanged.connect(_on_param_changed)
    except Exception:
        pass

    _refresh(persist=False, toast=False)
    _maybe_prompt_fbxsdk_setup(card)
    return True


FBX_IMPORT_SPEC = Spec(
    stripe_color="#2563eb",
    augment_infocard_footer=augment_infocard_footer,
    build_ports=build_ports,
)


__all__ = [
    "ROLE_PORTS",
    "SourceResolutionResult",
    "build_ports",
    "resolve_fbx_import_sources",
    "augment_infocard_footer",
    "FBX_IMPORT_SPEC",
]
