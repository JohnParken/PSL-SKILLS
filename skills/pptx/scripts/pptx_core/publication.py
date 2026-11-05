"""Publish verified bytes without ever exposing a partially written final file.

The caller supplies a validated directory descriptor. Each attempt predeclares
its journal and staging names before creation; the journal is durable before
staging starts. A hard link is the exclusive commit operation. This is a small
producer transaction, not a historical-residue discovery or recovery service.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from typing import Callable


_CHUNK_BYTES = 1024 * 1024
_DIR_FD_OPERATIONS = (os.open, os.stat, os.unlink, os.link)
_NOFOLLOW_OPERATIONS = (os.stat, os.link)


@dataclass(frozen=True)
class PublicationReceipt:
    """The final-file state and the independent cleanup result of one attempt."""

    identity: tuple[int, int] | None = None
    published: bool = False
    cleanup_complete: bool = True
    journal: str | None = None
    retained: tuple[str, ...] = ()
    cleanup_errors: tuple[str, ...] = ()
    run_id: str | None = None
    final: str | None = None
    expected_bytes: int = 0
    sha256: str | None = None
    journal_identity: tuple[int, int] | None = None
    staging_identity: tuple[int, int] | None = None

    @property
    def temporary_files(self) -> int:
        return len(self.retained)

    def as_dict(self) -> dict:
        return {
            "published": self.published, "cleanup_complete": self.cleanup_complete,
            "journal": self.journal, "retained": list(self.retained),
            "temporary_files": self.temporary_files, "cleanup_errors": list(self.cleanup_errors),
            "run_id": self.run_id, "final": self.final, "expected_bytes": self.expected_bytes,
            "sha256": self.sha256,
            "final_identity": list(self.identity) if self.identity is not None else None,
            "journal_identity": list(self.journal_identity) if self.journal_identity is not None else None,
            "staging_identity": list(self.staging_identity) if self.staging_identity is not None else None,
        }


class PublicationError(ValueError):
    """A publication failure carrying any preserved final and owned residue."""

    def __init__(self, message: str, code: str, receipt: PublicationReceipt | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.receipt = receipt or PublicationReceipt()
        self.details = {"stage": "publication", "publication": self.receipt.as_dict()}


@dataclass
class _OwnedFile:
    name: str
    descriptor: int | None = None
    identity: tuple[int, int] | None = None
    signature: tuple[int, int] | None = None
    attempted: bool = False
    created: bool = False


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _entry(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _check_capabilities() -> None:
    supported = (
        os.name == "posix" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
        and all(operation in os.supports_dir_fd for operation in _DIR_FD_OPERATIONS)
        and all(operation in os.supports_follow_symlinks for operation in _NOFOLLOW_OPERATIONS)
    )
    if not supported:
        raise PublicationError("Safe exclusive publication is unavailable on this platform.", "publication_unsupported")


def _refresh(owned: _OwnedFile) -> None:
    if owned.descriptor is not None:
        info = os.fstat(owned.descriptor)
        owned.signature = info.st_size, info.st_mtime_ns


def _create(parent_fd: int, owned: _OwnedFile) -> None:
    owned.attempted = True
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    owned.descriptor = os.open(owned.name, flags, 0o600, dir_fd=parent_fd)
    owned.created = True
    info = os.fstat(owned.descriptor)
    owned.identity = _identity(info)
    owned.signature = info.st_size, info.st_mtime_ns
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PublicationError("New publication item is not an exclusive regular file.", "publication_identity_changed")


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        written = os.write(descriptor, view[offset:offset + _CHUNK_BYTES])
        if written <= 0:
            raise OSError("Publication write made no progress.")
        offset += written


def _write_owned(owned: _OwnedFile, data: bytes) -> None:
    try:
        _write_all(owned.descriptor, data)
    finally:
        _refresh(owned)


def _record(journal: _OwnedFile, record: dict) -> None:
    encoded = (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
    _write_owned(journal, encoded)
    os.fsync(journal.descriptor)


def _verify_name(parent_fd: int, owned: _OwnedFile, name: str | None = None) -> None:
    info = _entry(parent_fd, name or owned.name)
    if info is None or not stat.S_ISREG(info.st_mode) or _identity(info) != owned.identity:
        raise PublicationError("Publication item identity changed.", "publication_identity_changed")
    if owned.signature != (info.st_size, info.st_mtime_ns):
        raise PublicationError("Publication item changed after writing.", "publication_identity_changed")


def _verify_contents(owned: _OwnedFile, data: bytes) -> None:
    os.lseek(owned.descriptor, 0, os.SEEK_SET)
    offset = 0
    while offset < len(data):
        actual = os.read(owned.descriptor, min(_CHUNK_BYTES, len(data) - offset))
        if not actual or actual != data[offset:offset + len(actual)]:
            raise PublicationError("Staged publication bytes did not verify.", "publication_failed")
        offset += len(actual)
    if os.read(owned.descriptor, 1):
        raise PublicationError("Staged publication has unexpected trailing bytes.", "publication_failed")


def _remove_owned(parent_fd: int, owned: _OwnedFile, max_links: int) -> str | None:
    """Remove only an unchanged current-attempt item; return a bounded reason."""
    if not owned.attempted:
        return None
    try:
        info = _entry(parent_fd, owned.name)
        if info is None:
            return None
        if not owned.created or owned.identity is None:
            return "ownership_unknown"
        if not stat.S_ISREG(info.st_mode) or _identity(info) != owned.identity:
            return "identity_changed"
        if info.st_nlink > max_links or (info.st_size, info.st_mtime_ns) != owned.signature:
            return "state_changed"
        os.unlink(owned.name, dir_fd=parent_fd)
        return None
    except OSError:
        return "remove_failed"


def _cleanup(parent_fd: int, staging: _OwnedFile, journal: _OwnedFile, published: bool, protect_staging: bool) -> tuple[tuple[str, ...], tuple[str, ...]]:
    retained = []
    errors = []
    reason = "published_identity_changed" if protect_staging else _remove_owned(parent_fd, staging, 2 if published else 1)
    if reason is not None:
        retained.append(staging.name)
        errors.append("staging:" + reason)
    else:
        try:
            os.fsync(parent_fd)
        except OSError:
            errors.append("staging:directory_sync_failed")
    if errors and journal.created:
        retained.append(journal.name)
    else:
        reason = _remove_owned(parent_fd, journal, 1)
        if reason is not None:
            retained.append(journal.name)
            errors.append("journal:" + reason)
        else:
            try:
                os.fsync(parent_fd)
            except OSError:
                errors.append("journal:directory_sync_failed")
    return tuple(retained), tuple(errors)


def publish_bytes(parent_fd: int, name: str, data: bytes, *, register: Callable[[dict], None] | None = None) -> PublicationReceipt:
    """Publish complete bytes using a caller-validated, already open parent.

    ``register`` receives a fresh bounded plan before either temporary item is
    created. A host can flush that event to stderr to preserve registration even
    when a signal prevents a final result. The self-describing journal is then
    written and synced before staging. No old journal is opened or adopted.

    The journal's initial creation is the bootstrap boundary: interruption of
    its first write can leave an incomplete control record, but no stage/final.
    Same-user hostile directory writers are outside this serial-controller
    contract; pathname identity is checked before publication and cleanup.
    """
    if not isinstance(name, str) or not name or name in {".", ".."} or any(value in name for value in ("/", "\\", "\x00")) or len(os.fsencode(name)) > 255:
        raise PublicationError("Publication requires one bounded filename.", "publication_failed")
    if not isinstance(data, bytes):
        raise PublicationError("Publication data must be immutable bytes.", "publication_failed")
    run_id = uuid.uuid4().hex
    journal = _OwnedFile("_pptx_publish_" + run_id + ".jsonl")
    staging = _OwnedFile("_pptx_stage_" + run_id + ".tmp")
    digest = hashlib.sha256(data).hexdigest()
    published = False
    protect_staging = False
    failure = None
    failure_code = "publication_failed"
    retained = ()
    cleanup_errors = ()
    try:
        _check_capabilities()
        if name in {journal.name, staging.name}:
            raise PublicationError("Final name conflicts with a planned publication item.", "publication_identity_changed")
        parent_info = os.fstat(parent_fd)
        if not stat.S_ISDIR(parent_info.st_mode):
            raise PublicationError("Publication parent is not a directory.", "publication_failed")
        if _entry(parent_fd, name) is not None:
            raise PublicationError("Output already exists; publication will not overwrite it.", "output_exists")
        if _entry(parent_fd, journal.name) is not None or _entry(parent_fd, staging.name) is not None:
            raise PublicationError("A planned publication item already exists.", "publication_identity_changed")
        os.fsync(parent_fd)
        plan = {
            "schema": "psl.pptx.publication.v1", "event": "planned", "run_id": run_id,
            "journal": journal.name, "staging": staging.name, "final": name,
            "parent_identity": list(_identity(parent_info)), "expected_bytes": len(data), "sha256": digest,
        }
        if register is not None:
            register(dict(plan))
        _create(parent_fd, journal)
        _record(journal, {**plan, "journal_identity": list(journal.identity)})
        os.fsync(parent_fd)
        _create(parent_fd, staging)
        _record(journal, {"event": "staging_created", "staging_identity": list(staging.identity)})
        os.fsync(parent_fd)
        _write_owned(staging, data)
        os.fsync(staging.descriptor)
        _verify_contents(staging, data)
        _verify_name(parent_fd, staging)
        _record(journal, {"event": "verified", "expected_bytes": len(data), "sha256": digest})
        _verify_name(parent_fd, journal)
        _verify_name(parent_fd, staging)
        try:
            os.link(staging.name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
        except BaseException:
            final_info = _entry(parent_fd, name)
            published = final_info is not None and _identity(final_info) == staging.identity
            raise
        published = True
        try:
            _verify_name(parent_fd, staging, name)
        except PublicationError:
            protect_staging = True
            raise
        os.fsync(parent_fd)
        _record(journal, {"event": "published", "final_identity": list(staging.identity)})
    except BaseException as error:
        failure = error
        if isinstance(error, PublicationError):
            failure_code = error.code
        elif isinstance(error, FileExistsError):
            failure_code = "output_exists" if staging.created else "publication_identity_changed"
    finally:
        if journal.attempted or staging.attempted:
            retained, cleanup_errors = _cleanup(parent_fd, staging, journal, published, protect_staging)
        close_errors = []
        for owned in (staging, journal):
            if owned.descriptor is not None:
                try:
                    os.close(owned.descriptor)
                except OSError:
                    close_errors.append("descriptor:close_failed")
        cleanup_errors += tuple(close_errors)
    receipt = PublicationReceipt(
        identity=staging.identity if published else None, published=published,
        cleanup_complete=not retained and not cleanup_errors, journal=journal.name if journal.name in retained else None,
        retained=retained, cleanup_errors=cleanup_errors, run_id=run_id, final=name,
        expected_bytes=len(data), sha256=digest, journal_identity=journal.identity, staging_identity=staging.identity,
    )
    if failure is not None:
        if not isinstance(failure, Exception):
            raise failure
        raise PublicationError(str(failure), failure_code, receipt) from failure
    return receipt
