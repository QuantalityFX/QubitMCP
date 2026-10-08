"""Standalone pattern files with strict parsing, conflict detection and atomic saves."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .document import PatternDocument, PatternError, validate_integrity

MAX_FILE_BYTES = 16 * 1024 * 1024


class PatternConflictError(PatternError):
    pass


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise PatternError(f"Duplicate JSON field: {key}")
        obj[key] = value
    return obj


def _invalid_constant(value):
    raise PatternError(f"Non-finite JSON number: {value}")


def _read_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise PatternError("Pattern file exceeds the 16 MB document limit.")
    return data


def file_hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(_read_bytes(path)).hexdigest()
    except FileNotFoundError:
        return None


def load_document(path: str | Path) -> tuple[PatternDocument, str]:
    data = _read_bytes(Path(path))
    try:
        parsed = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                            parse_constant=_invalid_constant)
        document = PatternDocument.from_dict(parsed)
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError, KeyError) as exc:
        raise PatternError(f"Invalid pattern document: {exc}") from exc
    return document, hashlib.sha256(data).hexdigest()


@contextmanager
def _write_lock(path: Path):
    """OS-released lock prevents two cooperating editors racing the hash check.

    The small lock file stays in place: removing it would let waiters lock a
    different inode on POSIX. A crashed process cannot leave the OS lock held.
    """
    lock_path = path.with_name("." + path.name + ".lock")
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise PatternConflictError("Another editor is saving this pattern. Try again shortly.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def save_document(path: str | Path, document: PatternDocument, *, expected_hash: str | None = None) -> str:
    """Create a new file, or replace exactly the previously loaded version.

    Missing/externally changed files never get recreated/overwritten implicitly.
    Schema-valid drafts can be saved even when not ready for meshing.
    """
    validate_integrity(document)
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() != ".json":
        raise PatternError("Choose a .pattern.json filename.")
    data = (json.dumps(document.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise PatternError("Pattern document exceeds the 16 MB limit.")
    with _write_lock(path):
        if file_hash(path) != expected_hash:
            raise PatternConflictError("This pattern changed or was moved outside this editor. Reload it, or Save As to keep your edits in another file.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix="." + path.name + ".", suffix=".tmp",
                                             dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest()


def resolve_pattern_path(value: str, *, workflow_path: str | Path | None = None) -> Path:
    if not value.strip():
        raise PatternError("No pattern file selected. Create a pattern or open an existing one.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        if not workflow_path:
            raise PatternError("A relative pattern path needs a saved workflow location. Locate the pattern file.")
        path = Path(workflow_path).expanduser().resolve().parent / path
    return path.resolve()
