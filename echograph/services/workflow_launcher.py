from __future__ import annotations

from pathlib import Path
from typing import Sequence


OPEN_WORKFLOW_FLAG = "--open-workflow"
SKIP_RECENT_FLAG = "--skip-recent"


def arg_value(args: Sequence[str], flag: str) -> str:
    try:
        idx = list(args).index(flag)
    except ValueError:
        return ""
    try:
        return str(args[idx + 1] or "").strip()
    except Exception:
        return ""


def build_workflow_launch_args(
    python_executable: str,
    launcher_script: str | Path,
    workflow_path: str | Path | None = None,
) -> list[str]:
    args = [str(python_executable or "python"), str(launcher_script), SKIP_RECENT_FLAG]
    if workflow_path:
        args.extend([OPEN_WORKFLOW_FLAG, str(workflow_path)])
    return args
