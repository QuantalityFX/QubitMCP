"""Portable, non-destructive Rig Limiter repairs beside their source BVHs."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import tempfile
from uuid import uuid4

VERSION = 1


def options_signature(options):
    return hashlib.sha256(json.dumps([VERSION, options], sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=256)
def _digest(path, size, modified, changed):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def file_signature(path):
    path = Path(path).resolve()
    stat = path.stat()
    return _digest(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def metadata_path(source):
    return Path(source).with_suffix('.bvh.rig_limiter.json')


def read_records(source):
    try:
        data = json.loads(metadata_path(source).read_text(encoding='utf-8'))
        if data.get('version') == VERSION and isinstance(data.get('repairs'), list):
            return [r for r in data['repairs'] if isinstance(r, dict)]
    except (OSError, ValueError, AttributeError):
        pass
    return []


def repair_path(source, record):
    name = record.get('file', '')
    # Metadata may move with its folder, but must only refer to a sibling BVH.
    if not isinstance(name, str) or not name or Path(name).name != name or '/' in name or '\\' in name or ':' in name:
        return None
    target = Path(source).parent / name
    if target.suffix.lower() != '.bvh' or target.resolve() == Path(source).resolve():
        return None
    return target


def find_repair(source, options=None):
    try:
        source_hash = file_signature(source)
        for record in reversed(read_records(source)):
            if record.get('source_sha') != source_hash or (options is not None and record.get('options_sha') != options):
                continue
            path = repair_path(source, record)
            if path is not None and isinstance(record.get('report'), dict) and path.is_file():
                if file_signature(path) == record.get('output_sha'):
                    return record
    except OSError:
        pass
    return None


def save_repair(source, options, report, export):
    """Publish metadata only after the complete BVH has been written."""
    source = Path(source).resolve()
    source_hash = file_signature(source)
    target = source.with_name(source.stem + '_rig_limited_' + uuid4().hex + '.bvh')
    if target.exists():
        raise FileExistsError(target)
    partial = None
    try:
        export(target)
        if file_signature(source) != source_hash:
            raise ValueError('The source BVH changed during repair; retry with the new animation.')
        record = dict(file=target.name, source_sha=source_hash, options_sha=options,
                      output_sha=file_signature(target), report=report)
        records = read_records(source)
        records.append(record)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=source.parent,
                                         prefix='.rig-limiter-', suffix='.tmp', delete=False) as stream:
            partial = Path(stream.name)
            json.dump(dict(version=VERSION, repairs=records), stream, indent=2)
        partial.replace(metadata_path(source))
        return record
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


def generated_files(folder):
    """Folder scans should list original clips, not their derived repair copies."""
    files = set()
    for metadata in Path(folder).glob('*.bvh.rig_limiter.json'):
        source = metadata.with_name(metadata.name[:-len('.rig_limiter.json')])
        for record in read_records(source):
            path = repair_path(source, record)
            if path is not None:
                files.add(path.name.casefold())
    return files
