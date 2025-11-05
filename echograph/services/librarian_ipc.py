from pathlib import Path
import os, json, time, subprocess
from ..constants import script_dir
from ..qt_compat import QtWidgets
from nodes.loader import bootstrap_plugins  # if needed
from nodes import core

def _lib_root() -> Path:
    env_root = (os.getenv("LIBRARIAN_ROOT","") or "").strip().strip('"').strip("'")
    base = Path(env_root).resolve() if env_root else script_dir()
    if (base / "ipc").exists():
        return base
    if (base / "nodes" / "librarian").exists():
        return base / "nodes" / "librarian"
    return base

def inbox_dir() -> Path:
    p = _lib_root() / "ipc" / "inbox"
    p.mkdir(parents=True, exist_ok=True)
    return p

def outbox_dir() -> Path:
    p = _lib_root() / "ipc" / "outbox"
    p.mkdir(parents=True, exist_ok=True)
    return p

def enqueue(cmd: dict) -> Path:
    ctype = (cmd.get("type") or cmd.get("action") or "").strip().lower()
    if not ctype:
        if "query" in cmd: ctype = "search"
        elif "question" in cmd: ctype = "analyze"
        else: ctype = "summarize"
    cmd["type"] = cmd["action"] = ctype
    cmd.setdefault("from", "EchoGraph")
    cmd.setdefault("ts", int(time.time() * 1000))
    fn = inbox_dir() / f"cmd_{int(time.time()*1000)}_{os.getpid()}.json"
    fn.write_text(json.dumps(cmd, ensure_ascii=False, indent=2), encoding="utf-8")
    return fn

def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

def render_hits_text(payload: dict) -> str:
    if not isinstance(payload, dict):
        return "No result data."
    lines = []
    if payload.get("summary"): lines.append(str(payload["summary"]).strip())
    if payload.get("answer"):  lines.append(str(payload["answer"]).strip())
    for key in ("results","hits","items"):
        arr = payload.get(key)
        if isinstance(arr, (list, tuple)) and arr:
            lines.append("")
            lines.append(f"Top {min(len(arr),5)} results:")
            for i, it in enumerate(arr[:5], 1):
                if isinstance(it, dict):
                    title = it.get("title") or it.get("name") or it.get("id") or f"Result {i}"
                    snippet = it.get("snippet") or it.get("summary") or it.get("text") or ""
                    s = (snippet or "").replace("\r"," ").replace("\n"," ")
                    if len(s) > 240: s = s[:240] + "…"
                    lines.append(f"{i}. {title}")
                    if s: lines.append(f"   {s}")
                else:
                    s = str(it)
                    if len(s) > 240: s = s[:240] + "…"
                    lines.append(f"{i}. {s}")
            break
    if not lines:
        lines = [json.dumps(payload, ensure_ascii=False, indent=2)]
    return "\n".join(lines).strip()

def load_output_text_by_ts(ts: int) -> str:
    p = outbox_dir() / f"result_{int(ts)}.json"
    payload = _read_json(p) if p.exists() else None
    return render_hits_text(payload) if payload else ""

# single-instance launcher
_LIB_PROC = None
_LAST_LAUNCH = 0.0

def ensure_running(focus_hint: bool=True):
    global _LIB_PROC, _LAST_LAUNCH
    try:
        if _LIB_PROC and _LIB_PROC.poll() is None:
            if focus_hint:
                try: enqueue({"type":"focus"})
                except Exception: pass
            return _LIB_PROC
    except Exception:
        _LIB_PROC = None
    now = time.time()
    if (now - _LAST_LAUNCH) < 1.0:
        return _LIB_PROC
    _LAST_LAUNCH = now
    # import launch file flexibly
    try:
        from nodes.librarian import launch_librarian as L
    except Exception:
        import importlib.util
        lib_file = script_dir() / "nodes" / "librarian" / "launch_librarian.py"
        spec = importlib.util.spec_from_file_location("librarian_launcher", str(lib_file))
        if not spec or not spec.loader:
            QtWidgets.QMessageBox.critical(None, "EchoGraph", "Cannot import launch_librarian.py")
            return None
        L = importlib.util.module_from_spec(spec)  # type: ignore
        spec.loader.exec_module(L)                 # type: ignore
    try:
        _LIB_PROC = L.launch(verbose=False)
        if focus_hint:
            try: enqueue({"type":"focus"})
            except Exception: pass
        return _LIB_PROC
    except Exception as e:
        _LIB_PROC = None
        QtWidgets.QMessageBox.critical(None, "EchoGraph", f"Failed to launch Librarian:\n{e}")
        return None
