"""Byte-budgeted DOCX pages and task-local reuse of parsed report results."""

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import os
import re
import stat
import uuid


HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
HEX_SESSION = re.compile(r"[0-9a-f]{32}")
PAGE_POSITION = re.compile(r"[1-9][0-9]{0,8}")
CACHE_VERSION = 1
MAX_CACHE_BYTES = 34 * 1024 * 1024


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def serialize_docx_page(data):
    """Canonical UTF-8 page representation, including the final LF."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"


@dataclass(frozen=True)
class CacheReference:
    """Identity carried by a cursor for one private, immutable cache file."""

    session: str
    sha256: str

    @property
    def filename(self):
        """The only path a cache operation may touch."""
        return f".docx-report-{self.session}.cache"


@dataclass(frozen=True)
class PagePosition:
    """Source, view and starting block of a requested page."""

    source: str
    options: str
    start: int
    cache: CacheReference | None = None

    def cursor(self, position):
        """Encode a continuation in the stateless or cached wire format."""
        return page_cursor(self.source, self.options, position, self.cache)


def page_cursor(source, options, position, cache=None):
    """Encode an opaque token with an integrity check for accidental damage."""
    if cache is None:
        payload = f"1.{source}.{options}.{position}"
    else:
        payload = f"2.{source}.{options}.{position}.{cache.session}.{cache.sha256}"
    return payload + "." + _digest(payload.encode("ascii"))


def _decode_cursor(cursor):
    if not isinstance(cursor, str) or len(cursor) > 320:
        raise ValueError("invalid continuation cursor")
    fields = cursor.split(".")
    if (len(fields), fields[0]) not in ((5, "1"), (7, "2")):
        raise ValueError("invalid continuation cursor")
    _, source, options, position, *tail = fields
    if not all(HEX_DIGEST.fullmatch(value) for value in (source, options, tail[-1])) or not PAGE_POSITION.fullmatch(position):
        raise ValueError("invalid continuation cursor")
    cache = None
    if len(tail) == 3:
        if not HEX_SESSION.fullmatch(tail[0]) or not HEX_DIGEST.fullmatch(tail[1]):
            raise ValueError("invalid continuation cursor cache reference")
        cache = CacheReference(tail[0], tail[1])
    result = PagePosition(source, options, int(position), cache)
    if cursor != result.cursor(result.start):
        raise ValueError("continuation cursor checksum mismatch")
    return result


def bind_page(source, options, cursor):
    """Match the original bytes and normalized view before reusing a cursor."""
    option_hash = _digest(json.dumps(options, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if cursor is None:
        return PagePosition(source, option_hash, 0)
    result = _decode_cursor(cursor)
    if source != result.source:
        raise ValueError("continuation source bytes changed")
    if option_hash != result.options:
        raise ValueError("continuation reading options changed")
    return result


def bounded_page(data, position, max_output_bytes, **coverage):
    """Select complete blocks within a serialized byte budget."""
    blocks = data["blocks"]
    total, start = len(blocks), position.start
    if start and start >= total:
        raise ValueError("continuation cursor position is out of range")
    base = {key: value for key, value in data.items() if key != "blocks"}
    base.update(page_version=1, source_sha256=position.source, total_blocks=total, start_block=start)

    def page(stop, items):
        more = stop < total
        return dict(base, blocks=items, truncated=more, next_block=stop if more else None,
                    has_more=more, next_cursor=position.cursor(stop) if more else None,
                    coverage={"block_start": start, "block_end_exclusive": stop, "selected_total_blocks": total,
                              "selected_view_complete": start == 0 and not more, "document_complete": False, **coverage})

    if max_output_bytes is None:
        return page(total, blocks[start:])

    first_envelope = len(serialize_docx_page(page(start, [])).encode("utf-8"))
    last_envelope = len(serialize_docx_page(page(total, [])).encode("utf-8"))
    minimum_envelope = min(first_envelope, last_envelope)
    # The terminal page has no cursor. Test it even if the preceding page
    # would be too large. Stop sizing once neither envelope can fit.
    sizes = [0]
    for index in range(start, total):
        block_bytes = len(serialize_docx_page(blocks[index]).encode("utf-8"))
        sizes.append(sizes[-1] + block_bytes)
        if sizes[-1] - 1 + minimum_envelope > max_output_bytes:
            break

    def page_size(stop):
        envelope = len(serialize_docx_page(page(stop, [])).encode("utf-8"))
        return envelope + max(0, sizes[stop - start] - 1)

    if start + len(sizes) - 1 == total and page_size(total) <= max_output_bytes:
        return page(total, blocks[start:])
    if first_envelope > max_output_bytes:
        raise ValueError("--max-output-bytes is too small for complete page metadata/diagnostics")
    accepted = start
    low, high = start + 1, min(total - 1, start + len(sizes) - 1)
    while low <= high:
        stop = (low + high) // 2
        if page_size(stop) > max_output_bytes:
            high = stop - 1
        else:
            accepted = stop
            low = stop + 1
    if accepted == start and start < total:
        raise ValueError("single block exceeds --max-output-bytes with complete metadata; increase budget or narrow selection")
    return page(accepted, blocks[start:accepted])


def _cache_supported():
    return (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY") and hasattr(os, "geteuid")
            and all(operation in os.supports_dir_fd for operation in (os.open, os.stat, os.unlink))
            and os.stat in os.supports_follow_symlinks)


def _open_task():
    return os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _identity(info):
    return [info.st_dev, info.st_ino]


def _private_cache(info):
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1
            and not stat.S_IMODE(info.st_mode) & 0o077)


def _remove_cache(directory, name, info):
    try:
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (not _private_cache(current) or _identity(current) != _identity(info)
                or (current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (info.st_size, info.st_mtime_ns, info.st_ctime_ns)):
            raise ValueError("report page cache cleanup incomplete: registered file changed")
        os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("report page cache cleanup incomplete") from exc
    return True


def _cache_registration(directory, position):
    return {"version": CACHE_VERSION, "task": _identity(os.fstat(directory)), "absent_at_plan": True,
            "name": position.cache.filename, "source": position.source, "options": position.options}


def _save_cache(position, data):
    try:
        directory = _open_task()
    except OSError:
        return None
    try:
        # Register a single absent path before writing. Exclusive creation
        # protects a file that appears after the absence check.
        registration = _cache_registration(directory, position)
        name = registration["name"]
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            try:
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            except OSError:
                return None
        except OSError:
            return None
        else:
            return None
        try:
            registration["file"] = _identity(os.fstat(descriptor))
            raw = serialize_docx_page({"registration": registration, "report": data}).encode("utf-8")
            if len(raw) > MAX_CACHE_BYTES:
                _remove_cache(directory, name, os.fstat(descriptor))
                return None
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            return _digest(raw)
        except BaseException as exc:
            _remove_cache(directory, name, os.fstat(descriptor))
            if isinstance(exc, OSError):
                return None
            raise
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


@dataclass(frozen=True)
class _CachedReport:
    """Parsed data and the already verified file identity for cleanup."""

    data: dict
    directory: int
    name: str
    info: os.stat_result

    def remove(self):
        """Remove the same cache file or accept that another reader closed it."""
        return _remove_cache(self.directory, self.name, self.info)


@contextmanager
def _open_cache(position):
    if not _cache_supported():
        yield None
        return
    directory = _open_task()
    name = position.cache.filename
    try:
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            yield None
            return
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if info.st_nlink == 0:
                yield None
                return
            if not _private_cache(info) or info.st_size > MAX_CACHE_BYTES:
                raise ValueError("invalid report page cache file")
            raw = stream.read(MAX_CACHE_BYTES + 1)
            if len(raw) != info.st_size or _digest(raw) != position.cache.sha256:
                raise ValueError("report page cache content changed")
            try:
                payload = json.loads(raw)
            except (ValueError, RecursionError) as exc:
                raise ValueError("invalid report page cache data") from exc
            record = dict(_cache_registration(directory, position), file=_identity(info))
            if not isinstance(payload, dict) or payload.get("registration") != record:
                raise ValueError("report page cache does not match the registered task or file")
            data = payload.get("report")
            if (not isinstance(data, dict) or data.get("format") != "docx-report" or data.get("schema_version") != 1
                    or not isinstance(data.get("blocks"), list) or not isinstance(data.get("issues"), dict)):
                raise ValueError("invalid report page cache data")
            yield _CachedReport(data, directory, name, info)
    finally:
        os.close(directory)


def _report_page(data, position, budget):
    return bounded_page(data, position, budget, issues_present=bool(data["issues"]))


def report_page(position, budget, parse):
    """Reuse a validated report on continuation, otherwise parse the source."""
    if position.cache is not None:
        with _open_cache(position) as cached:
            if cached is not None:
                try:
                    page = _report_page(dict(cached.data, page_cache="reused"), position, budget)
                except ValueError:
                    # Retain the cursor on a genuine budget error. If only the
                    # cache token is too large, return the legacy-size page.
                    page = _report_page(cached.data, replace(position, cache=None), budget)
                    cached.remove()
                    return page
                if not page["has_more"]:
                    cached.remove()
                return page

    data = parse()
    position = replace(position, cache=None)
    page = _report_page(data, position, budget)
    if not page["has_more"] or not _cache_supported():
        return page
    pending = replace(position, cache=CacheReference(uuid.uuid4().hex, "0" * 64))
    try:
        saved = _report_page(dict(data, page_cache="stored"), pending, budget)
    except ValueError:
        # The stateless page already fits. The extra cache token must not make
        # an existing byte budget unusable.
        return page
    digest = _save_cache(pending, data)
    if digest is None:
        return page
    bound = replace(pending, cache=replace(pending.cache, sha256=digest))
    saved["next_cursor"] = bound.cursor(saved["next_block"])
    return saved


def release_cursor(cursor):
    """Close a discarded continuation without touching the original DOCX."""
    position = _decode_cursor(cursor)
    if position.cache is None:
        return {"status": "not_required"}
    if not _cache_supported():
        raise ValueError("report page cache cleanup requires safe descriptor-relative file access")
    with _open_cache(position) as cached:
        removed = cached is not None and cached.remove()
    return {"status": "released" if removed else "already_absent"}
