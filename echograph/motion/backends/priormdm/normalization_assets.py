"""Fetch and cache the official HumanML3D inference statistics, without a dataset clone.

Resolve the published pair at one Git revision, check Git blob hashes and NumPy
contents, then install only missing files. Existing user data is never replaced.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
from urllib.error import URLError
from urllib.request import Request, urlopen

SOURCE = "EricGuo5513/HumanML3D"
API_URL = f"https://api.github.com/repos/{SOURCE}"
NAMES = ("Mean.npy", "Std.npy")


def _fetch(url: str, limit: int) -> bytes:
    request = Request(url, headers={"User-Agent": "QubitMCP-PriorMDM-Setup"})
    try:
        with urlopen(request, timeout=30) as response:
            content = response.read(limit + 1)
    except (OSError, URLError) as exc:
        raise OSError(f"HumanML3D download failed: {exc}. Check your connection and retry setup; completed downloads are retained.") from exc
    if len(content) > limit:
        raise ValueError(f"Unexpectedly large HumanML3D download: {url}")
    return content


def _validate(content: bytes, name: str):
    import numpy as np
    try:
        values = np.load(io.BytesIO(content), allow_pickle=False)
        valid = (isinstance(values, np.ndarray) and values.shape == (263,)
                 and values.dtype.kind == "f" and np.isfinite(values).all()
                 and (name != "Std.npy" or (values > 0).all()))
    except (ValueError, OSError, EOFError) as exc:
        raise ValueError(f"Invalid HumanML3D {name}: expected a NumPy statistics file.") from exc
    if not valid:
        raise ValueError(f"Invalid HumanML3D {name}: expected 263 finite values and positive standard deviations.")
    return values


def _write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".priormdm-stats-", delete=False) as stream:
            partial = Path(stream.name)
            stream.write(content)
        if path.exists():
            raise FileExistsError(f"Refusing to replace {path}")
        partial.rename(path)
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


def _manifest(cache: Path) -> dict:
    path = cache / "source.json"
    if path.is_file():
        manifest = json.loads(path.read_text(encoding="utf-8"))
    else:
        print("Locating the official HumanML3D normalization files...", flush=True)
        reference = json.loads(_fetch(f"{API_URL}/git/ref/heads/main", 65536))
        revision = reference.get("object", {}).get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Could not resolve the official HumanML3D source revision.")
        tree = json.loads(_fetch(f"{API_URL}/git/trees/{revision}?recursive=1", 10 * 1024 * 1024))
        if tree.get("truncated"):
            raise ValueError("The official HumanML3D file listing is incomplete. Retry setup later.")
        entries = {entry["path"]: entry for entry in tree.get("tree", []) if entry.get("type") == "blob"}
        # Only the original dataset statistics: never evaluator t2m_mean/std files.
        prefix = next((prefix for prefix in ("HumanML3D/", "")
                       if all(prefix + name in entries for name in NAMES)), None)
        if prefix is None:
            raise ValueError("The official HumanML3D repository no longer exposes the expected Mean.npy/Std.npy pair. Setup needs an updated asset source.")
        manifest = {"schema_version": 1, "source": SOURCE, "revision": revision,
                    "files": {name: {"path": prefix + name, "sha": entries[prefix + name]["sha"]} for name in NAMES}}
    if (manifest.get("schema_version") != 1 or manifest.get("source") != SOURCE
            or not re.fullmatch(r"[0-9a-f]{40}", manifest.get("revision", ""))):
        raise ValueError(f"Invalid HumanML3D cache manifest: {path}")
    parents = set()
    for name in NAMES:
        entry = manifest.get("files", {}).get(name, {})
        if entry.get("path") not in (name, "HumanML3D/" + name) or not re.fullmatch(r"[0-9a-f]{40}", entry.get("sha", "")):
            raise ValueError(f"Invalid HumanML3D cache source for {name}: {path}")
        parents.add(str(Path(entry["path"]).parent))
    if len(parents) != 1:
        raise ValueError(f"HumanML3D statistics must come from the same source folder: {path}")
    if not path.exists():
        _write_new(path, json.dumps(manifest, indent=2).encode("utf-8"))
    return manifest


def ensure_normalization(dataset: Path, cache: Path) -> None:
    """Reuse installed data or cached downloads; fetch just two small files if needed."""
    import numpy as np
    existing = {}
    for name in NAMES:
        path = dataset / name
        if path.exists():
            existing[name] = _validate(path.read_bytes(), name)
    if len(existing) == len(NAMES):
        print(f"HumanML3D normalization files already available: {dataset}", flush=True)
        return

    manifest = _manifest(cache)
    contents = {}
    for name in NAMES:
        target = cache / name
        entry = manifest["files"][name]
        cached = target.is_file()
        if cached:
            content = target.read_bytes()
        else:
            print(f"Downloading HumanML3D {name}...", flush=True)
            content = _fetch(f"https://raw.githubusercontent.com/{SOURCE}/{manifest['revision']}/{entry['path']}", 65536)
        blob = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
        if hashlib.sha1(blob).hexdigest() != entry["sha"]:
            raise ValueError(f"HumanML3D {name} does not match its published checksum. No installed data was changed.")
        values = _validate(content, name)
        if name in existing and not np.array_equal(existing[name], values):
            raise ValueError(f"Existing {dataset / name} differs from the official statistics; it was preserved. A custom dataset needs its matching Mean.npy and Std.npy pair in Advanced settings.")
        if not cached:
            _write_new(target, content)
        contents[name] = content
    # Both downloads must validate before installing either member of the pair.
    for name in NAMES:
        if name not in existing:
            _write_new(dataset / name, contents[name])
    print(f"HumanML3D normalization ready: {dataset} (shared across projects).", flush=True)
