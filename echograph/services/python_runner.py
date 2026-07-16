from __future__ import annotations

import contextlib
import io
import os
import runpy
import sys
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List

from echograph.qt_compat import QtCore, QtGui, QtWidgets


_CWD_LOCK = threading.RLock()


@dataclass
class PythonExecutionRequest:
    source_kind: str
    code: str = ""
    script_path: str = ""
    working_dir: str = ""
    args: List[str] = field(default_factory=list)
    params: List[Dict[str, Any]] = field(default_factory=list)
    node: Any = None
    graph_scene: Any = None
    parent_widget: Any = None
    thread_mode: str = "worker"
    show_output: bool = True


@dataclass
class PythonExecutionResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    traceback: str = ""
    output_value: Any = None
    namespace: Dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str:
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr)
        if self.traceback:
            parts.append(self.traceback)
        if not parts and self.output_value is not None:
            parts.append(str(self.output_value))
        return "\n".join(part for part in parts if part).strip()


def _host_namespace() -> Dict[str, Any]:
    ns: Dict[str, Any] = {
        "__name__": "__echograph_exec__",
        "QtWidgets": QtWidgets,
        "QtCore": QtCore,
        "QtGui": QtGui,
    }
    try:
        from maya import cmds as _cmds  # type: ignore
        ns["cmds"] = _cmds
    except Exception:
        ns["cmds"] = None
    try:
        import hou as _hou  # type: ignore
        ns["hou"] = _hou
    except Exception:
        ns["hou"] = None
    return ns


def _params_map(params: List[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for idx, param in enumerate(params or []):
        if not isinstance(param, dict):
            continue
        name = str(param.get("name") or f"param{idx + 1}")
        out[name] = str(param.get("value", "") or "")
    return out


def _resolve_inputs(graph_scene: Any, node: Any) -> tuple[Dict[str, str], str]:
    inputs: Dict[str, str] = {}
    primary_input = ""
    if graph_scene is None or node is None or not hasattr(graph_scene, "_node_items"):
        return inputs, primary_input
    try:
        python_item = graph_scene._node_items.get(node.name)
    except Exception:
        python_item = None
    if python_item is None:
        return inputs, primary_input
    try:
        in_edges = graph_scene._ordered_in_edges(python_item)
    except Exception:
        try:
            in_edges = graph_scene._in_edges(python_item)
        except Exception:
            in_edges = []
    for idx, edge in enumerate(in_edges or []):
        try:
            txt = graph_scene.resolve_text_value(edge.src)
        except Exception:
            txt = ""
        if not txt:
            continue
        if not primary_input:
            primary_input = txt
        try:
            key = edge.src.model.name
        except Exception:
            key = f"in{idx + 1}"
        inputs.setdefault(key, txt)
        inputs.setdefault(f"in{idx + 1}", txt)
    return inputs, primary_input


def build_namespace(request: PythonExecutionRequest) -> Dict[str, Any]:
    ns = _host_namespace()
    params = list(request.params or [])
    if not params and request.node is not None:
        try:
            params = list(getattr(request.node, "params", None) or [])
        except Exception:
            params = []
    inputs, primary_input = _resolve_inputs(request.graph_scene, request.node)
    ns.update(
        {
            "node": request.node,
            "params": _params_map(params),
            "raw_params": params,
            "inputs": inputs,
            "primary_input": primary_input,
            "graph_scene": request.graph_scene,
        }
    )
    return ns


def _working_dir_for_request(request: PythonExecutionRequest) -> str:
    raw = str(request.working_dir or "").strip()
    if raw:
        return raw
    if request.source_kind == "script" and request.script_path:
        try:
            return str(Path(request.script_path).expanduser().resolve().parent)
        except Exception:
            return str(Path(request.script_path).parent)
    return ""


def _derive_output_value(ns: Dict[str, Any], stdout_text: str) -> Any:
    if "output_text" in ns:
        return ns.get("output_text")
    if "result" in ns:
        return ns.get("result")
    if stdout_text:
        return stdout_text
    return None


def execute_python_sync(request: PythonExecutionRequest) -> PythonExecutionResult:
    source_kind = str(request.source_kind or "").strip().lower()
    ns = build_namespace(request)
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    old_argv = None
    old_cwd = None
    working_dir = _working_dir_for_request(request)
    lock = _CWD_LOCK if working_dir else contextlib.nullcontext()

    try:
        with lock:
            if working_dir:
                old_cwd = os.getcwd()
                os.chdir(working_dir)
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                if source_kind == "code":
                    exec(str(request.code or ""), ns, ns)
                elif source_kind == "script":
                    script_path = Path(str(request.script_path or "")).expanduser()
                    if not script_path.exists():
                        raise FileNotFoundError(f"Missing Python script: {script_path}")
                    old_argv = list(sys.argv)
                    sys.argv = [str(script_path)] + [str(arg) for arg in (request.args or [])]
                    result_globals = runpy.run_path(
                        str(script_path),
                        init_globals=ns,
                        run_name="__echograph_exec__",
                    )
                    ns.update(result_globals or {})
                else:
                    raise ValueError(f"Unsupported Python source kind: {request.source_kind!r}")
    except Exception:
        stdout_text = stdout_buf.getvalue().strip()
        stderr_text = stderr_buf.getvalue().strip()
        return PythonExecutionResult(
            ok=False,
            stdout=stdout_text,
            stderr=stderr_text,
            traceback=traceback.format_exc(),
            namespace=ns,
        )
    finally:
        if old_argv is not None:
            sys.argv = old_argv
        if old_cwd is not None:
            try:
                os.chdir(old_cwd)
            except Exception:
                pass

    stdout_text = stdout_buf.getvalue().strip()
    stderr_text = stderr_buf.getvalue().strip()
    output_value = _derive_output_value(ns, stdout_text)
    return PythonExecutionResult(
        ok=True,
        stdout=stdout_text,
        stderr=stderr_text,
        output_value=output_value,
        namespace=ns,
    )


def run_python_async(
    request: PythonExecutionRequest,
    on_finished: Callable[[PythonExecutionResult], None],
) -> threading.Thread | None:
    parent = request.parent_widget
    thread_mode = str(request.thread_mode or "worker").strip().lower()

    def _finish(result: PythonExecutionResult) -> None:
        if parent is not None:
            try:
                QtCore.QTimer.singleShot(0, parent, lambda r=result: on_finished(r))
                return
            except TypeError:
                pass
            except Exception:
                pass
        try:
            QtCore.QTimer.singleShot(0, lambda r=result: on_finished(r))
        except Exception:
            on_finished(result)

    def _run() -> None:
        _finish(execute_python_sync(request))

    if thread_mode == "main_thread":
        if parent is not None:
            try:
                QtCore.QTimer.singleShot(0, parent, _run)
                return None
            except TypeError:
                pass
            except Exception:
                pass
        QtCore.QTimer.singleShot(0, _run)
        return None

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    return worker
