"""Shared OFD limits, errors, path confinement and XML/text primitives."""
import os
import posixpath
import re
import stat
try:
    import zlib
except ImportError:
    zlib = None
import sys
import zipfile
from xml.parsers import expat

NS = 'http://www.ofdspec.org/2016'
KNOWN_VERSIONS = ('1.0', '1.1', '1.2')
ZLIB_ERROR = getattr(zlib, 'error', RuntimeError)
LIMITS = dict(input_bytes=64*1024*1024, entries=4096, member_bytes=16*1024*1024,
              total_bytes=128*1024*1024, xml_bytes=2*1024*1024, xml_total=32*1024*1024,
              nodes=100000, depth=64, text_chars=8*1024*1024, pages=2000,
              references=10000, ratio=200, output_chars=64*1024*1024, composite_depth=16,
              xml_events=300000, xml_name_chars=8*1024*1024,
              xml_namespace_chars=4096, xml_name_length=8192)


class ReadError(Exception):
    """A deterministic failure, never a partial success."""


def environment():
    version = tuple(map(int, expat.EXPAT_VERSION.removeprefix('expat_').split('.')))
    if sys.version_info < (3, 10) or version < (2, 6, 0):
        raise ReadError('environment: Python >=3.10 and Expat >=2.6.0 required')
    if zlib is None or zipfile.zlib is None:
        raise ReadError('environment: working zlib/ZIP deflate support required')
    try:
        probe = b'ofd-environment-probe'
        if zlib.decompress(zlib.compress(probe)) != probe:
            raise ReadError('environment: zlib roundtrip failed')
    except ZLIB_ERROR as exc:
        raise ReadError('environment: zlib roundtrip failed') from exc


def local(tag):
    return tag.rsplit('}', 1)[-1]


def q(name):
    return '{' + NS + '}' + name


def is_ofd(tag):
    return tag.rpartition('}')[0] == '{' + NS


def ofd_nodes(root):
    stack = [root]
    while stack:
        node = stack.pop()
        if not is_ofd(node.tag):
            continue
        yield node
        stack.extend(reversed(list(node)))


def text_of(element):
    return (element.text or '').strip()


def single_child(root, name, *, required=False):
    """Reject ambiguity in the single-value containers this reader relies on."""
    children = root.findall(q(name))
    if len(children) > 1:
        raise ReadError('duplicate ' + name)
    if not children:
        if required:
            raise ReadError('missing ' + name)
        return None
    return children[0]


def confined_environment():
    """Require the standard-library platform primitives used by confined reads."""
    if (os.open not in os.supports_dir_fd or not hasattr(os, 'O_NOFOLLOW')
            or not hasattr(os, 'O_DIRECTORY')):
        raise ReadError('unsupported: confined input requires dir_fd, O_NOFOLLOW and O_DIRECTORY')


def confined_descriptor(path, task_root):
    """Walk an explicitly trusted task root with directory-relative nofollow opens."""
    confined_environment()
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise ReadError('input must stay within task directory')
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
    descriptor = os.open(task_root, directory_flags)
    try:
        for component in path.parts[:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        flags = (os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0)
                 | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_CLOEXEC', 0))
        return os.open(path.parts[-1], flags, dir_fd=descriptor)
    finally:
        os.close(descriptor)


def read_input(path, *, task_root=None):
    """Read one regular descriptor and reject detected concurrent byte changes.

    The default API trusts its path. A supplied task_root confines relative input
    through nofollow descriptor walks; CLI passes '.' to anchor the current task.
    This is not a filesystem sandbox against privileged concurrent writers.
    """
    if task_root is None:
        flags = (os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
                 | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_CLOEXEC', 0))
        descriptor = os.open(path, flags)
    else:
        descriptor = confined_descriptor(path, task_root)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReadError('input must be a regular file')
        if before.st_size > LIMITS['input_bytes']:
            raise ReadError('limit: input bytes')
        with os.fdopen(descriptor, 'rb') as handle:
            descriptor = None
            data = handle.read(before.st_size + 1)
            after = os.fstat(handle.fileno())
        identity = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (len(data) != before.st_size
                or any(getattr(before, key) != getattr(after, key) for key in identity)):
            raise ReadError('input changed during read')
        return data
    finally:
        if descriptor is not None:
            os.close(descriptor)


def safe_name(name):
    if (not name or len(name) > 1024 or '\\' in name or '\x00' in name
            or any(ord(c) < 32 for c in name) or ':' in name or '%' in name
            or '?' in name or '#' in name or name.startswith('/')):
        raise ReadError('unsafe package path')
    parts = name.rstrip('/').split('/')
    if any(p in ('', '.', '..') for p in parts):
        raise ReadError('noncanonical package path')
    return name


def resolve(source, value):
    value = value.strip()
    if not value or len(value) > 1024:
        raise ReadError('empty/oversized package reference')
    raw = value[1:] if value.startswith('/') else value
    parts = [] if value.startswith('/') else posixpath.dirname(source).split('/')
    if parts == ['']:
        parts = []
    for part in raw.split('/'):
        if part == '.':
            continue
        if part == '..':
            if not parts:
                raise ReadError('package reference escapes root')
            parts.pop()
        else:
            safe_name(part)
            parts.append(part)
    return safe_name('/'.join(parts))


def decode_text(value):
    issues = []
    def replace(match):
        codepoint = int(match.group(1), 16)
        if 0xD800 <= codepoint <= 0xDFFF:
            issues.append('surrogate_text_escape')
            return match.group(0)
        return chr(codepoint)
    decoded = re.sub(r'\\([0-9A-Fa-f]{4})', replace, value)
    if re.search(r'\\(?![0-9A-Fa-f]{4})', value):
        issues.append('uninterpreted_text_escape')
    if '\u00a4' in decoded:
        issues.append('text_placeholder_unresolved')
    return decoded, issues
