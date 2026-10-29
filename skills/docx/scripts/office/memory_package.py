"""Bounded package workspace in memory; no filesystem workspace directories."""
from contextlib import contextmanager
import fnmatch
import io
from pathlib import PurePosixPath
import stat
from types import SimpleNamespace
import zipfile

from .archiveio import open_archive
from .helpers import MAX_MEMBERS, MAX_PACKAGE_BYTES, MAX_PART_BYTES, read_part, _check_entries


class MemoryPath:
    """Internal member view, deliberately not an OS path or a public Path API."""
    is_memory_path = True

    def __init__(self, files, name=""):
        self.files = files
        self.member = name

    def __truediv__(self, name):
        name = str(name)
        path = PurePosixPath(self.member) / name
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Unsafe in-memory member path")
        return MemoryPath(self.files, path.as_posix())

    def __str__(self):
        return self.member or "<in-memory-package>"

    @property
    def name(self):
        return PurePosixPath(self.member).name

    @property
    def suffix(self):
        return PurePosixPath(self.member).suffix

    @property
    def parent(self):
        parent = PurePosixPath(self.member).parent.as_posix()
        return MemoryPath(self.files, "" if parent == "." else parent)

    def relative_to(self, root):
        if not isinstance(root, MemoryPath) or self.files is not root.files:
            raise ValueError("Different package workspaces")
        return PurePosixPath(self.member).relative_to(root.member or ".")

    def is_file(self):
        return self.member in self.files

    def is_dir(self):
        return not self.member or any(n.startswith(self.member + "/") for n in self.files)

    def exists(self):
        return self.is_file() or self.is_dir()

    def is_symlink(self):
        return False

    def stat(self):
        if not self.exists():
            raise FileNotFoundError(self.member)
        return SimpleNamespace(st_mode=(stat.S_IFREG | 0o600) if self.is_file() else (stat.S_IFDIR | 0o700),
                               st_size=len(self.files[self.member]) if self.is_file() else 0)

    lstat = stat

    def read_bytes(self):
        if not self.is_file():
            raise FileNotFoundError(self.member)
        return self.files[self.member]

    def write_bytes(self, data):
        if not self.member or not isinstance(data, bytes):
            raise ValueError("Expected a member and bytes")
        size = sum(map(len, self.files.values())) - len(self.files.get(self.member, b"")) + len(data)
        if (len(data) > MAX_PART_BYTES or size > MAX_PACKAGE_BYTES
                or len(self.files) + (self.member not in self.files) > MAX_MEMBERS):
            raise ValueError("Package exceeds size or member limits")
        self.files[self.member] = data
        return len(data)

    def write_text(self, text, encoding="utf-8"):
        return self.write_bytes(text.encode(encoding))

    def open(self, mode="rb", **kwargs):
        if mode != "rb":
            raise ValueError("Memory member streams are read-only binary")
        return io.BytesIO(self.read_bytes())

    def mkdir(self, **kwargs):
        # Logical directories are implicit; no filesystem side effects.
        if self.is_file():
            raise FileExistsError(self.member)

    def rglob(self, pattern):
        prefix = self.member + "/" if self.member else ""
        for name in sorted(self.files):
            if name.startswith(prefix) and (pattern == "*" or fnmatch.fnmatch(PurePosixPath(name).name, pattern)):
                yield MemoryPath(self.files, name)


def read_package(source):
    files = {}
    with open_archive(source) as archive:
        for member in archive.infolist():
            if not member.is_dir():
                files[member.filename] = read_part(archive, member.filename)
    return MemoryPath(files)


@contextmanager
def package_workspace(source):
    """Read every member through CRC checks before any mutation or publication."""
    root = read_package(source)
    try:
        yield root
    finally:
        root.files.clear()


def package_bytes(root):
    entries = []
    for name, data in root.files.items():
        member = zipfile.ZipInfo(name)
        member.file_size = len(data)
        entries.append(member)
    _check_entries(entries)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for member in entries:
            archive.writestr(member, root.files[member.filename], compress_type=(
                zipfile.ZIP_STORED if member.filename == "[Content_Types].xml" else zipfile.ZIP_DEFLATED))
    return stream.getvalue()


def directory_package(source):
    """Snapshot a trusted unpacked input, refusing links and special files."""
    root = MemoryPath({})
    for path in source.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("Only regular package files and directories are supported")
        if path.stat().st_size > MAX_PART_BYTES:
            raise ValueError("Package member exceeds size limit")
        with path.open("rb") as stream:
            data = stream.read(MAX_PART_BYTES + 1)
        (root / path.relative_to(source).as_posix()).write_bytes(data)
    return root
