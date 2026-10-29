"""Unpack and repack DOCX/DOTX files using Python's ZIP support.

Usage:
    python scripts/office/package.py unpack input.docx unpacked/
    python scripts/office/package.py pack unpacked/ output.docx

This checks ZIP integrity and required parts, not XML schemas or page layout.
"""

import argparse
import os
import sys
import zipfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from .helpers import rezip
except ImportError:
    from office.helpers import rezip


REQUIRED_PARTS = ("[Content_Types].xml", "_rels/.rels", "word/document.xml")


def _check_extension(path: Path) -> None:
    if path.suffix.lower() not in {".docx", ".dotx"}:
        raise ValueError("Expected a .docx or .dotx file; format conversion is unsupported")


def _check_parts(directory: Path) -> None:
    missing = [name for name in REQUIRED_PARTS if not (directory / name).is_file()]
    if missing:
        raise ValueError("Missing required package parts: " + ", ".join(missing))


def unpack_document(source: str | Path, destination: str | Path) -> Path:
    """Extract into a new or empty directory, preserving other destinations."""
    source, destination = Path(source), Path(destination)
    _check_extension(source)
    if destination.is_symlink() or (
        destination.exists()
        and (not destination.is_dir() or any(destination.iterdir()))
    ):
        raise ValueError("Unpack destination must be a new or empty directory")
    # This directory is the requested output, never a scratch workspace.
    from office.memory_package import read_package
    root = read_package(source)
    _check_parts(root)
    created_files, created_directories = [], []
    try:
        for member in root.rglob("*"):
            target = destination / member.member
            missing = []
            parent = target.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for parent in reversed(missing):
                parent.mkdir()
                created_directories.append((parent, parent.stat()))
            with target.open("xb") as stream:
                created_files.append((target, os.fstat(stream.fileno())))
                stream.write(member.read_bytes())
    except BaseException:
        for target, expected in reversed(created_files):
            current = target.lstat()
            if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
                raise RuntimeError("Unpack rollback retained a changed output: " + str(target))
            target.unlink()
        for parent, expected in reversed(created_directories):
            current = parent.lstat()
            if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
                raise RuntimeError("Unpack rollback retained a changed directory: " + str(parent))
            parent.rmdir()
        raise
    return destination


def _verify_written_package(path):
    from office.archiveio import open_archive
    from office.helpers import read_part
    with open_archive(path) as archive:
        for member in archive.infolist():
            if not member.is_dir():
                read_part(archive, member.filename)


def pack_document(source: str | Path, destination: str | Path) -> Path:
    """Repack XML and media without launching an archiver or following links."""
    if getattr(source, "is_memory_path", False):
        from office.memory_package import package_bytes
        from office.helpers import atomic_write_bytes
        destination = Path(destination)
        _check_extension(destination)
        _check_parts(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        mask = os.umask(0)
        os.umask(mask)
        atomic_write_bytes(destination, package_bytes(source), new_file_mode=0o666 & ~mask,
                           verify=_verify_written_package)
        return destination
    source, destination = Path(source), Path(destination)
    _check_extension(destination)
    if destination.is_symlink():
        raise ValueError("Symbolic link pack destinations are unsupported")
    if not source.is_dir() or source.is_symlink():
        raise ValueError("Pack source must be a directory, not a symbolic link")
    if destination.resolve().is_relative_to(source.resolve()):
        raise ValueError("Pack destination must be outside the source directory")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are unsupported: {path}")
    _check_parts(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rezip(source, destination)
    return destination


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("unpack", "pack"))
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    try:
        operation = unpack_document if args.operation == "unpack" else pack_document
        output = operation(args.source, args.destination)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
