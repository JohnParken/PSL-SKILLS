"""Explicit, persistent DOCX task cleanup plans; never discover deletion targets."""
import argparse
from contextlib import contextmanager
import errno
import hashlib
import json
import os
import stat
import sys
import uuid

SCHEMA = "docx_cleanup_v1"
MAX_ENTRIES = 2048
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
OUTCOMES = ("success", "failed", "stopped", "retry")
ARCHIVE_STATUSES = ("copied", "already_archived", "skipped", "failed", "completed")


def _parts(value):
    if (not isinstance(value, str) or not value or "\\" in value
            or ":" in value or "\0" in value or value.startswith("/")):
        raise ValueError("Use a task-relative path without links or parent traversal")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Use normalized relative paths, without ./ or ..")
    return tuple(parts)


def _overlap(left, right):
    first, second = _parts(left), _parts(right)
    return first[:len(second)] == second or second[:len(first)] == first


def _identity(info):
    return [info.st_dev, info.st_ino]


class TaskFiles:
    """Pin traversal to directory descriptors; refuse unsupported platforms."""

    def __enter__(self):
        required = (os.open, os.stat, os.unlink, os.rmdir, os.rename, os.link)
        if (not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")
                or any(item not in os.supports_dir_fd for item in required)):
            raise RuntimeError("Descriptor-relative no-follow cleanup is unavailable; no deletion attempted")
        self.root = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.identity = _identity(os.fstat(self.root))
        return self

    def __exit__(self, *_):
        os.close(self.root)

    @contextmanager
    def parent(self, path):
        parts = _parts(path)
        descriptor = os.dup(self.root)
        try:
            for part in parts[:-1]:
                next_descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                          dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            yield descriptor, parts[-1]
        finally:
            os.close(descriptor)

    def inspect(self, path, *, contents=False, limit=MAX_FILE_BYTES):
        with self.parent(path) as (parent, name):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if contents:
                    raise ValueError("Expected a regular file: " + path)
                return {"kind": "directory", "identity": _identity(info)}
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Symlink or special file refused: " + path)
            result = {"kind": "file", "identity": _identity(info), "size": info.st_size,
                      "mtime_ns": info.st_mtime_ns, "links": info.st_nlink}
            if not contents:
                return result
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(descriptor, "rb") as stream:
                start = os.fstat(stream.fileno())
                if not stat.S_ISREG(start.st_mode) or _identity(start) != result["identity"]:
                    raise ValueError("File changed while opening: " + path)
                if start.st_size > limit:
                    raise ValueError("File exceeds cleanup fingerprint limit: " + path)
                digest = hashlib.sha256()
                total = 0
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("File grew beyond fingerprint limit: " + path)
                    digest.update(chunk)
                end = os.fstat(stream.fileno())
                if (start.st_size, start.st_mtime_ns, start.st_nlink) != (
                        end.st_size, end.st_mtime_ns, end.st_nlink):
                    raise ValueError("File changed while fingerprinting: " + path)
                result.update(size=end.st_size, mtime_ns=end.st_mtime_ns,
                              links=end.st_nlink, sha256=digest.hexdigest())
            return result

    def optional(self, path, *, contents=False):
        try:
            return self.inspect(path, contents=contents)
        except FileNotFoundError:
            return None

    def load(self, path):
        with self.parent(path) as (parent, name):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MANIFEST_BYTES:
                    raise ValueError("Manifest must be a bounded regular file")
                raw = stream.read(MAX_MANIFEST_BYTES + 1)
                if len(raw) > MAX_MANIFEST_BYTES:
                    raise ValueError("Manifest is too large")
        return json.loads(raw, object_pairs_hook=_unique_object)

    def save(self, path, data, *, new=False):
        raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError("Manifest exceeds size budget")
        with self.parent(path) as (parent, name):
            temporary = ".docx-cleanup-" + uuid.uuid4().hex + ".tmp"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                if new:
                    # Exclusive destination creation: never replace an existing plan.
                    os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent,
                            follow_symlinks=False)
                else:
                    self.inspect(path, contents=True, limit=MAX_MANIFEST_BYTES)
                    os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest key: " + key)
        result[key] = value
    return result


def _valid_snapshot(value, *, file_only=False):
    if value is None:
        return True
    if not isinstance(value, dict) or value.get("kind") not in ("file", "directory"):
        return False
    identity = value.get("identity")
    if not isinstance(identity, list) or len(identity) != 2 or any(type(i) is not int for i in identity):
        return False
    if value["kind"] == "directory":
        return not file_only
    if any(type(value.get(key)) is not int for key in ("size", "mtime_ns", "links")):
        return False
    if file_only:
        digest = value.get("sha256")
        return isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
    return True


def _validate(data, manifest, task):
    if (not isinstance(data, dict) or data.get("schema") != SCHEMA
            or data.get("task_identity") != task.identity
            or data.get("manifest") != manifest
            or data.get("phase") not in ("planned", "sealed")):
        raise ValueError("Invalid manifest or different task directory")
    entries = data.get("entries")
    protected = data.get("protected")
    outputs = data.get("deliverables")
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError("Invalid cleanup entries")
    if not isinstance(protected, list) or not isinstance(outputs, list):
        raise ValueError("Invalid protected paths or deliverables")
    if len(protected) + len(outputs) > MAX_ENTRIES:
        raise ValueError("Too many protected paths")
    protected_paths = [item["path"] for item in protected]
    if any(not _valid_snapshot(item.get("initial")) for item in protected):
        raise ValueError("Invalid protected identity")
    for path in protected_paths + outputs + [manifest]:
        _parts(path)
    seen = set()
    for entry in entries:
        path = entry["path"]
        _parts(path)
        if (path in seen or entry.get("kind") not in ("file", "directory")
                or entry.get("absent_at_plan") is not True):
            raise ValueError("Invalid/duplicate owned entry")
        if any(_overlap(path, other) for other in protected_paths + outputs + [manifest]):
            raise ValueError("Cleanup entry overlaps a protected path: " + path)
        if data["phase"] == "sealed" and not _valid_snapshot(
                entry.get("fingerprint"), file_only=entry["kind"] == "file"):
            raise ValueError("Invalid entry fingerprint")
        seen.add(path)
    if data["phase"] == "sealed":
        if data.get("outcome") not in OUTCOMES or not isinstance(data.get("output_fingerprints"), dict):
            raise ValueError("Invalid sealed state")
        if data["outcome"] == "success" and (
                not outputs or data.get("verified") is not True
                or data.get("archive_status") not in ARCHIVE_STATUSES
                or set(data["output_fingerprints"]) != set(outputs)
                or any(value is None for value in data["output_fingerprints"].values())):
            raise ValueError("Successful cleanup requires verified deliverables and an archive result")
        if (set(data["output_fingerprints"]) != set(outputs)
                or any(not _valid_snapshot(value, file_only=True)
                       for value in data["output_fingerprints"].values())):
            raise ValueError("Invalid output fingerprints")
    return data


def plan_cleanup(manifest, *, files=(), directories=(), protected=(), deliverables=()):
    """Register absent targets BEFORE the caller creates its intermediate files."""
    _parts(manifest)
    with TaskFiles() as task:
        entries = [{"path": path, "kind": kind, "absent_at_plan": True}
                   for kind, paths in (("file", files), ("directory", directories)) for path in paths]
        data = {"schema": SCHEMA, "manifest": manifest, "task_identity": task.identity,
                "phase": "planned", "entries": entries,
                "protected": [{"path": path} for path in protected],
                "deliverables": list(deliverables)}
        _validate(data, manifest, task)
        for entry in entries:
            if task.optional(entry["path"]) is not None:
                raise ValueError("Cannot claim a pre-existing path: " + entry["path"])
        for item in data["protected"]:
            item["initial"] = task.optional(item["path"])
        task.save(manifest, data, new=True)
        return {"status": "planned", "manifest": manifest, "entries": len(entries)}


def seal_cleanup(manifest, *, outcome, verified=False, archive_status=None, retain=None):
    """Fingerprint after validation/archive; explicit retain reasons survive cleanup."""
    retain = {} if retain is None else retain
    with TaskFiles() as task:
        data = _validate(task.load(manifest), manifest, task)
        if outcome not in OUTCOMES:
            raise ValueError("Unknown outcome")
        if outcome == "success" and (not verified or archive_status not in ARCHIVE_STATUSES):
            raise ValueError("Verify final files and obtain archive result before sealing success")
        if not isinstance(retain, dict) or not set(retain).issubset({e["path"] for e in data["entries"]}):
            raise ValueError("Retain paths must belong to this plan")
        if any(not isinstance(reason, str) or not reason.strip() for reason in retain.values()):
            raise ValueError("A retained file requires a reason")
        if data["phase"] == "sealed" and data["outcome"] != "retry":
            raise ValueError("Final plans cannot be resealed; use their recorded state")
        fingerprints = {}
        for path in data["deliverables"]:
            snapshot = task.optional(path, contents=True)
            if outcome == "success" and (snapshot is None or snapshot["size"] == 0):
                raise ValueError("Final deliverable missing or empty: " + path)
            fingerprints[path] = snapshot
        for entry in data["entries"]:
            entry["retain_reason"] = retain.get(entry["path"])
            try:
                snapshot = task.optional(entry["path"], contents=entry["kind"] == "file")
                if snapshot is not None and snapshot["kind"] != entry["kind"]:
                    raise ValueError("Entry type changed")
                entry["fingerprint"] = snapshot
                entry.pop("capture_error", None)
            except (OSError, ValueError) as exc:
                entry["fingerprint"] = None
                entry["capture_error"] = str(exc)
        data.update(phase="sealed", outcome=outcome, verified=verified,
                    archive_status=archive_status, output_fingerprints=fingerprints)
        _validate(data, manifest, task)
        task.save(manifest, data)
        return {"status": "sealed", "manifest": manifest, "outcome": outcome}


def _check_outputs(task, data):
    for path, expected in data["output_fingerprints"].items():
        if task.optional(path, contents=True) != expected:
            raise ValueError("Final deliverable missing or changed: " + path)


def cleanup_task(manifest):
    """Delete only unchanged owned entries; retain the plan as a resumable receipt."""
    with TaskFiles() as task:
        data = _validate(task.load(manifest), manifest, task)
        if data["phase"] != "sealed":
            raise ValueError("Seal the plan after the task outcome is known; no deletion attempted")
        report = {"status": "retained", "deleted": [], "retained": [], "failed": [],
                  "manifest_retained": manifest, "deliverables_unchanged": False,
                  "cleanup_complete": False, "outcome": data["outcome"],
                  "archive_status": data["archive_status"]}
        if data["outcome"] == "retry":
            report["retained"] = [{"path": e["path"], "reason": "retry_pending"} for e in data["entries"]]
            return report
        # Validate every protection and final output before the first deletion.
        protected_ids = []
        try:
            _check_outputs(task, data)
            for item in data["protected"]:
                for snapshot in (item.get("initial"), task.optional(item["path"])):
                    if snapshot is not None:
                        protected_ids.append(snapshot["identity"])
            protected_ids.extend(value["identity"] for value in data["output_fingerprints"].values()
                                 if value is not None)
        except (OSError, ValueError) as exc:
            report["status"] = "blocked"
            report["failed"].append({"path": manifest, "reason": str(exc)})
            return report
        entries = sorted(data["entries"], key=lambda e: (e["kind"] == "directory", -len(_parts(e["path"]))))
        for entry in entries:
            path = entry["path"]
            retained = entry.get("retain_reason") or entry.get("capture_error")
            if retained:
                report["retained"].append({"path": path, "reason": retained})
                continue
            try:
                expected = entry.get("fingerprint")
                current = task.optional(path, contents=entry["kind"] == "file")
                if current is None:
                    report["deleted"].append({"path": path, "reason": "already_absent"})
                    continue
                if current != expected or current["identity"] in protected_ids or current.get("links", 1) != 1:
                    report["retained"].append({"path": path, "reason": "changed_unowned_or_protected_alias"})
                    continue
                with task.parent(path) as (parent, name):
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    expected_type = stat.S_ISREG if entry["kind"] == "file" else stat.S_ISDIR
                    if _identity(info) != current["identity"] or not expected_type(info.st_mode):
                        raise ValueError("Entry replaced before deletion")
                    if entry["kind"] == "file":
                        if (info.st_size, info.st_mtime_ns, info.st_nlink) != (
                                current["size"], current["mtime_ns"], current["links"]):
                            raise ValueError("File changed before deletion")
                        os.unlink(name, dir_fd=parent)
                    else:
                        os.rmdir(name, dir_fd=parent)
                report["deleted"].append({"path": path, "reason": "removed"})
            except OSError as exc:
                category = "retained" if exc.errno in (errno.ENOTEMPTY, errno.EEXIST, errno.ELOOP, errno.ENOTDIR) else "failed"
                report[category].append({"path": path, "reason": str(exc)})
            except ValueError as exc:
                report["retained"].append({"path": path, "reason": str(exc)})
        try:
            _check_outputs(task, data)
            report["deliverables_unchanged"] = True
        except (OSError, ValueError) as exc:
            report["failed"].append({"path": manifest, "reason": str(exc)})
        report["cleanup_complete"] = not report["retained"] and not report["failed"]
        report["status"] = "failed" if report["failed"] else "retained" if report["retained"] else "cleaned"
        # Keep the original fingerprints for safe retries after partial cleanup.
        data["last_cleanup"] = report
        try:
            task.save(manifest, data)
        except (OSError, ValueError) as exc:
            # The earlier sealed plan remains usable if receipt publication fails.
            report["failed"].append({"path": manifest, "reason": "receipt_write_failed: " + str(exc)})
            report["status"] = "failed"
            report["cleanup_complete"] = False
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("manifest")
    for name in ("file", "directory", "protect", "deliverable"):
        plan.add_argument("--" + name, action="append", default=[])
    seal = commands.add_parser("seal")
    seal.add_argument("manifest")
    seal.add_argument("--outcome", choices=OUTCOMES, required=True)
    seal.add_argument("--verified", action="store_true")
    seal.add_argument("--archive-status", choices=ARCHIVE_STATUSES)
    seal.add_argument("--retain", nargs=2, action="append", default=[], metavar=("PATH", "REASON"))
    clean = commands.add_parser("clean")
    clean.add_argument("manifest")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = plan_cleanup(args.manifest, files=args.file, directories=args.directory,
                                  protected=args.protect, deliverables=args.deliverable)
        elif args.command == "seal":
            result = seal_cleanup(args.manifest, outcome=args.outcome, verified=args.verified,
                                  archive_status=args.archive_status, retain=dict(args.retain))
        else:
            result = cleanup_task(args.manifest)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["status"] in ("failed", "blocked") else 3 if result["status"] == "retained" else 0
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
