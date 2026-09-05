from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_REPO_ID = "Wan-AI/Wan2.2-TI2V-5B"
MANIFEST_NAME = "qubitmcp_wan22_ti2v_manifest.json"


def _app_home_from_env() -> Path:
    raw = (os.environ.get("QUBITMCP_HOME") or os.environ.get("QUBITFIELD_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        return Path(local).expanduser() / "QubitMCP"
    return Path.home() / ".qubitmcp"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Wan2.2 TI2V-5B model weights for QubitMCP.")
    parser.add_argument("--repo-id", default=os.environ.get("QUBITMCP_WAN22_REPO", DEFAULT_REPO_ID))
    parser.add_argument("--revision", default=os.environ.get("QUBITMCP_WAN22_REVISION", "main"))
    parser.add_argument("--app-home", default="", help="Writable QubitMCP app home. Defaults to QUBITMCP_HOME.")
    parser.add_argument("--local-dir", default="", help="Download destination. Defaults to <app-home>/models/Wan2.2-TI2V-5B.")
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved download plan without downloading.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    app_home = Path(args.app_home).expanduser() if str(args.app_home or "").strip() else _app_home_from_env()
    local_dir = Path(args.local_dir).expanduser() if str(args.local_dir or "").strip() else app_home / "models" / "Wan2.2-TI2V-5B"

    print(f"[wan2.2] Repository: {args.repo_id}")
    print(f"[wan2.2] Revision: {args.revision}")
    print(f"[wan2.2] Destination: {local_dir}")

    if args.dry_run:
        return 0

    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        print("[wan2.2] ERROR: huggingface_hub is not installed.", file=sys.stderr)
        print("[wan2.2] Run: python -m pip install huggingface_hub[hf_xet]", file=sys.stderr)
        print(f"[wan2.2] Import detail: {exc}", file=sys.stderr)
        return 1

    local_dir.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN") or None
    try:
        snapshot_download(
            repo_id=args.repo_id,
            revision=args.revision or None,
            local_dir=str(local_dir),
            local_dir_use_symlinks=False,
            token=token,
        )
    except Exception as exc:
        print(f"[wan2.2] ERROR: download failed: {exc}", file=sys.stderr)
        print("[wan2.2] If Hugging Face requires authentication, set HF_TOKEN and retry.", file=sys.stderr)
        return 1

    model_files = []
    for pattern in ("*.safetensors", "*.pt", "*.pth", "*.bin"):
        model_files.extend(str(path.relative_to(local_dir)) for path in local_dir.rglob(pattern))
    manifest = {
        "repo_id": args.repo_id,
        "revision": args.revision,
        "local_dir": str(local_dir),
        "model_file_count": len(model_files),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = local_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    if not model_files:
        print("[wan2.2] WARNING: no model weight files were found after download.")
    print(f"[wan2.2] Ready: {local_dir}")
    print(f"[wan2.2] Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
