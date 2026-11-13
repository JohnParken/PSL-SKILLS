"""Task-relative input reads and exclusive publication of verified document bytes."""

from contextlib import contextmanager
import os
from pathlib import Path, PureWindowsPath
import stat
import unicodedata

from archive_delivery import validate_delivery_output
from tl_package import MAX_INPUT


def path_parts(value):
    """Validate a task-relative filename before opening any component."""
    if (not isinstance(value, str) or '\\' in value or ':' in value or PureWindowsPath(value).drive
            or any(ord(char) < 32 for char in value)):
        raise ValueError('Expected a task-relative path')
    parts = value.split('/')
    if any(item in ('', '.', '..') for item in parts) or parts[0].casefold() == 'output':
        raise ValueError('Expected a task-relative path outside the archive')
    return parts


def output_name(value, extension='.docx'):
    """Validate a bare Word filename without mutating the task directory."""
    accepted = (extension,) if extension else ('.docx', '.dotx', '.json', '.txt', '.xml')
    if len(path_parts(value)) != 1 or Path(value).suffix.lower() not in accepted:
        raise ValueError('The output must be a bare Word filename with the required extension')
    validate_delivery_output(value)
    return value


def suggested_name(title, suffix, fallback, extension):
    """Create a valid filename from the supplied topic without inventing metadata."""
    stem = ''.join(' ' if char in '/\\<>:"|?*' or unicodedata.category(char).startswith('C') else char for char in title)
    stem = ' '.join(stem.split()).strip(' .')
    while stem and len((stem + suffix + extension).encode('utf-8')) > 220:
        stem = stem[:-1].rstrip(' .')
    candidate = (stem + suffix + extension) if stem else fallback + extension
    try:
        return output_name(candidate, extension)
    except ValueError:
        return output_name(fallback + extension, extension)


def check_platform(write=False):
    """Check filesystem operations needed for safe input or output access."""
    required = {os.open}
    if write:
        required.update((os.stat, os.link, os.unlink))
    if (not required <= os.supports_dir_fd or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY')
            or write and not {os.stat, os.link} <= os.supports_follow_symlinks):
        raise RuntimeError('This platform lacks safe task-relative file operations')


@contextmanager
def task_root():
    """Pin the current task directory for relative descriptor operations."""
    check_platform()
    fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def fingerprint(info):
    """Describe the identity and change times of an opened file."""
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_task(root, value, limit=MAX_INPUT):
    """Read one bounded regular file without following links or blocking on FIFOs."""
    parts = path_parts(value)
    folder = os.dup(root)
    try:
        for item in parts[:-1]:
            next_folder = os.open(item, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
            os.close(folder)
            folder = next_folder
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('Input must be a bounded regular task file')
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if len(data) > limit or fingerprint(before) != fingerprint(after) or len(data) != before.st_size:
                raise ValueError('Input changed during the read or exceeds the size limit')
            return data
    finally:
        os.close(folder)
