from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_REPO_ID = "MiniMaxAI/MiniMax-H3"
MANIFEST_NAME = "qubitmcp_minimax_h3_manifest.json"


def _app_home_from_env() -> Path:
    raw = (os.environ.get("QUBITMCP_HOME") or os.environ.get("QUBITFIELD_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        return Path(local).expanduser() / "QubitMCP"
    return Path.home() / ".qubitmcp"


def _normalize_variant(raw: str) -> str:
    text = str(raw or "").strip().lower().replace("-", "_")
    if text in {"fl2va", "fl", "base_fl2va", "h3_base_fl2va"}:
        return "FL2VA"
    if text in {"ref2va", "ref", "r2va", "base_ref2va", "h3_base_ref2va"}:
        return "Ref2VA"
    if text in {"both", "all"}:
        return "both"
    raise ValueError("variant must be FL2VA, Ref2VA, or both")


def _allow_patterns(variant: str) -> list[str]:
    patterns = ["model_index.json"]
    if variant in {"FL2VA", "both"}:
        patterns.append("FL2VA/*")
    if variant in {"Ref2VA", "both"}:
        patterns.append("Ref2VA/*")
    return patterns


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the local MiniMax H3 Hugging Face checkpoint used by QubitMCP."
    )
    parser.add_argument("--repo-id", default=os.environ.get("QUBITMCP_MINIMAX_H3_REPO", DEFAULT_REPO_ID))
    parser.add_argument("--revision", default=os.environ.get("QUBITMCP_MINIMAX_H3_REVISION", "main"))
    parser.add_argument("--app-home", default="", help="Writable QubitMCP app home. Defaults to QUBITMCP_HOME.")
    parser.add_argument("--local-dir", default="", help="Download destination. Defaults to <app-home>/models/MiniMax-H3.")
    parser.add_argument(
        "--variant",
        default=os.environ.get("QUBITMCP_MINIMAX_H3_VARIANT", "FL2VA"),
        help="Checkpoint family to download: FL2VA, Ref2VA, or both. FL2VA covers text/image-to-video.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved download plan without downloading.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        variant = _normalize_variant(args.variant)
    except ValueError as exc:
        print(f"[minimax-h3] ERROR: {exc}", file=sys.stderr)
        return 2

    app_home = Path(args.app_home).expanduser() if str(args.app_home or "").strip() else _app_home_from_env()
    local_dir = Path(args.local_dir).expanduser() if str(args.local_dir or "").strip() else app_home / "models" / "MiniMax-H3"
    patterns = _allow_patterns(variant)

    print(f"[minimax-h3] Repository: {args.repo_id}")
    print(f"[minimax-h3] Revision: {args.revision}")
    print(f"[minimax-h3] Variant: {variant}")
    print(f"[minimax-h3] Destination: {local_dir}")
    print(f"[minimax-h3] Include: {', '.join(patterns)}")

    if args.dry_run:
        return 0

    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        print("[minimax-h3] ERROR: huggingface_hub is not installed.", file=sys.stderr)
        print("[minimax-h3] Run: python -m pip install huggingface_hub[hf_xet]", file=sys.stderr)
        print(f"[minimax-h3] Import detail: {exc}", file=sys.stderr)
        return 1

    local_dir.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN") or None

    try:
        snapshot_download(
            repo_id=args.repo_id,
            revision=args.revision or None,
            allow_patterns=patterns,
            local_dir=str(local_dir),
            local_dir_use_symlinks=False,
            token=token,
        )
    except Exception as exc:
        print(f"[minimax-h3] ERROR: download failed: {exc}", file=sys.stderr)
        print(
            "[minimax-h3] If the repository requires license approval, accept it on Hugging Face and set HF_TOKEN.",
            file=sys.stderr,
        )
        return 1

    families = []
    for name in ("FL2VA", "Ref2VA"):
        family_dir = local_dir / name
        if family_dir.exists():
            families.append(name)

    manifest = {
        "repo_id": args.repo_id,
        "revision": args.revision,
        "variant": variant,
        "allow_patterns": patterns,
        "local_dir": str(local_dir),
        "families": families,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = local_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    if not (local_dir / "model_index.json").exists():
        print("[minimax-h3] WARNING: root model_index.json was not found after download.")
    if variant in {"FL2VA", "both"} and not (local_dir / "FL2VA").exists():
        print("[minimax-h3] WARNING: FL2VA directory was not found after download.")
    if variant in {"Ref2VA", "both"} and not (local_dir / "Ref2VA").exists():
        print("[minimax-h3] WARNING: Ref2VA directory was not found after download.")

    print(f"[minimax-h3] Ready: {local_dir}")
    print(f"[minimax-h3] Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
