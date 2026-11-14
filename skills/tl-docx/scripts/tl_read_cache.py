"""Immutable task-local JSON caches for parsed TL Word reports, not deliverables.

Tokens prove accidental integrity and bind source/options/version, not authority
against an actor who can rewrite both the task files and the supplied token.
"""

from hashlib import sha256
import json
import os
import re
import stat
from uuid import uuid4

PARSER_VERSION = 'tl-report-5'
MAX_BYTES = 64 * 1024 * 1024
TOKEN_PATTERN = re.compile(r'tlc1\.([0-9a-f]{32})\.([0-9a-f]{64})\.([0-9a-f]{64})\.([0-9a-f]{64})\Z')


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _options(options):
    return sha256(_encode([PARSER_VERSION, options])).hexdigest()


def _identity(info):
    return [info.st_dev, info.st_ino]


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _supported():
    if (not hasattr(os, 'geteuid') or not hasattr(os, 'O_NOFOLLOW')
            or not {os.open, os.stat, os.unlink} <= os.supports_dir_fd
            or os.stat not in os.supports_follow_symlinks):
        raise ValueError('Read cache requires safe descriptor-relative filesystem operations')


def _private(info):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
            and info.st_nlink == 1 and not stat.S_IMODE(info.st_mode) & 0o077)


def _reference(token):
    match = TOKEN_PATTERN.fullmatch(token) if isinstance(token, str) else None
    if match is None:
        raise ValueError('Invalid TL read cache token')
    session, digest, source, option_hash = match.groups()
    return f'.tl-docx-read-{session}.cache', digest, source, option_hash


def _registration(root, name, info, source, option_hash):
    return {'owner': 'tl-docx-read-cache', 'parser_version': PARSER_VERSION,
            'task': _identity(os.fstat(root)), 'file': _identity(info), 'name': name,
            'source': source, 'options': option_hash, 'absent_at_plan': True}


def _validate_report(data):
    if (not isinstance(data, (list, tuple)) or len(data) != 3
            or not isinstance(data[0], list) or not isinstance(data[1], list)
            or not isinstance(data[2], dict)
            or not all(isinstance(item, dict) for item in data[0] + data[1])):
        raise ValueError('Invalid TL read cache report schema')
    return tuple(data)


def _remove(root, name, info):
    try:
        current = os.stat(name, dir_fd=root, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not _private(current) or _fingerprint(current) != _fingerprint(info):
        raise ValueError('Read cache cleanup incomplete: registered file changed')
    os.unlink(name, dir_fd=root)
    return True


def store(root, source_sha256, options, report_data):
    """Register then exclusively create one private cache in the pinned task root.

    Raises on failure, never replaces an existing file. Caller may retain a
    stateless page when caching fails, but must report cleanup failures.
    """
    _supported()
    if not isinstance(source_sha256, str) or re.fullmatch('[0-9a-f]{64}', source_sha256) is None:
        raise ValueError('Read cache source requires a SHA-256 digest')
    _validate_report(report_data)
    option_hash = _options(options)
    session = uuid4().hex
    name = f'.tl-docx-read-{session}.cache'
    # Record intent and absence before exclusive creation; no directory scan.
    try:
        os.stat(name, dir_fd=root, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise ValueError('Read cache filename collision')
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root)
    try:
        registration = _registration(root, name, os.fstat(descriptor), source_sha256, option_hash)
        raw = _encode({'registration': registration, 'report': report_data})
        if len(raw) > MAX_BYTES:
            raise ValueError('Parsed read cache exceeds the 64 MiB cache limit')
        with os.fdopen(descriptor, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(descriptor)
        if not _private(os.fstat(descriptor)):
            raise ValueError('Read cache file identity or permissions changed')
        return f'tlc1.{session}.{sha256(raw).hexdigest()}.{source_sha256}.{option_hash}'
    except BaseException:
        _remove(root, name, os.fstat(descriptor))
        raise
    finally:
        os.close(descriptor)


def _read(root, token):
    _supported()
    name, digest, source, option_hash = _reference(token)
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(descriptor)
        if not _private(info) or info.st_size > MAX_BYTES:
            raise ValueError('Read cache is not a bounded private regular file')
        raw = stream.read(MAX_BYTES + 1)
        if (_fingerprint(info) != _fingerprint(os.fstat(descriptor)) or len(raw) != info.st_size
                or sha256(raw).hexdigest() != digest):
            raise ValueError('Read cache content or identity changed; cache retained')
    try:
        payload = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ValueError('Read cache JSON is invalid; cache retained') from exc
    expected = _registration(root, name, info, source, option_hash)
    if not isinstance(payload, dict) or payload.get('registration') != expected:
        raise ValueError('Read cache task, parser version or ownership does not match; cache retained')
    return name, info, _validate_report(payload.get('report'))


def load(root, token, source_sha256, options):
    """Load validated JSON only; return None for a released/missing cache.

    A changed source or reading option is rejected before opening the cache.
    Missing caches may be rebuilt from the source. Corrupt caches are retained
    with a specific error rather than deleting an unverified file.
    """
    _, _, source, option_hash = _reference(token)
    if source_sha256 != source or _options(options) != option_hash:
        raise ValueError('Read cache source, parser version or reading options changed')
    cached = _read(root, token)
    return cached[2] if cached is not None else None


def release(root, token):
    """Remove only the token's validated private cache, never source/output files."""
    cached = _read(root, token)
    removed = cached is not None and _remove(root, cached[0], cached[1])
    return {'status': 'released' if removed else 'already_absent', 'archive_status': 'not_required'}


def cached_report(root, package, *, revisions=None, include_related=True, token=None):
    """Return (report_tuple, cache_metadata) for explicitly requested caching.

    This caches raw content records only, not XML/structure projections. A
    missing file is explicitly reported as rebuilt; malformed or changed cache
    files raise and remain untouched. The caller releases the token at terminal
    pagination or when abandoning the continuation.
    """
    options = {'revisions': revisions, 'include_related': include_related}
    if token is not None:
        data = load(root, token, package.sha256, options)
        if data is not None:
            return data, {'status': 'reused', 'token': token}
    from tl_read import report  # pylint: disable=import-outside-toplevel
    data = report(package, revisions=revisions, include_related=include_related)
    saved = store(root, package.sha256, options, data)
    return data, {'status': 'rebuilt_missing' if token is not None else 'stored', 'token': saved}
