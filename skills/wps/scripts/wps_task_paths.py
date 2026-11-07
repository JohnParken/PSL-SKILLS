"""Task-root access and public delivery filename validation."""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps
import json
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
import uuid

ACTIVE = ContextVar('wps_task_paths', default=None)
WRITES = ContextVar('wps_candidate_writes', default=None)


class TaskPaths:
    def __init__(self):
        if sys.version_info < (3, 10):
            raise ValueError('Python >=3.10 required')
        needed = (os.open, os.stat)
        if (not all(call in os.supports_dir_fd for call in needed)
                or os.stat not in os.supports_follow_symlinks
                or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY')):
            raise ValueError('Safe task-relative file operations unavailable')
        self.root = Path.cwd()
        self.descriptor = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.read_stats = {}

    def parts(self, path, *, argument=False):
        value = os.fspath(path)
        if '\0' in value or '\\' in value or '..' in Path(value).parts:
            raise ValueError('Task paths must not contain parent components or backslashes')
        path = Path(value)
        if path.is_absolute():
            if argument:
                raise ValueError('CLI data paths must be relative to the task directory')
            try:
                path = path.relative_to(self.root)
            except ValueError as exc:
                raise ValueError('Data path is outside the task directory') from exc
        if not path.parts:
            return ()
        return path.parts

    @contextmanager
    def parent(self, path, *, create=False):
        parts = self.parts(path)
        if not parts:
            raise ValueError('A task filename is required')
        descriptor = os.dup(self.descriptor)
        try:
            for component in parts[:-1]:
                if create:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                next_descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            yield descriptor, parts[-1]
        finally:
            os.close(descriptor)

    def validate(self, path, *, output=False):
        if output:
            write_capabilities()
        self.parts(path, argument=True)
        try:
            with self.parent(path) as (parent, name):
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            if output:
                return
            raise
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            from wps_access import ParseError
            raise ParseError('unsupported', {'stage': 'input'}, 'Task data must be a non-linked regular file')
        if output:
            raise ValueError('CLI output already exists; choose an unused filename')

    def read(self, path, maximum):
        from wps_access import ParseError
        with self.parent(path) as (parent, name):
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0), dir_fd=parent)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ParseError('unsupported', {'stage': 'input'}, 'Task input must be a non-linked regular file')
            if before.st_size > maximum:
                raise ParseError('resource_limit', {'stage': 'input'}, 'Task input exceeds the byte budget')
            data = stream.read(before.st_size + 1)
            after = os.fstat(stream.fileno())
            if (len(data) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ParseError('io_error', {'stage': 'input'}, 'Task input changed during read')
            self.read_stats[tuple(self.parts(path))] = after
        return data


def cli_boundary(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        arguments = args[0] if args and isinstance(args[0], (list, tuple)) else sys.argv[1:]
        kind = Path(function.__globals__.get('__file__', '')).name
        options = arguments[:arguments.index('--')] if '--' in arguments else arguments
        if ((kind == 'read_wps.py' and '--check' in options)
                or (kind in ('run_wps_task.py', 'export_wps.py') and arguments and arguments[0] == 'check')):
            # Keep each existing self-check's version, channels and exit codes.
            return function(*args, **kwargs)
        access = None
        token = None
        try:
            access = TaskPaths()
            token = ACTIVE.set(access)
            return function(*args, **kwargs)
        except (OSError, ValueError) as exc:
            kind = Path(function.__globals__.get('__file__', '')).name
            if kind == 'read_wps.py':
                payload = function.__globals__['failure'](getattr(exc, 'status', 'io_error'), {'stage': 'input'}, str(exc))
                print(json.dumps(payload, ensure_ascii=False))
            elif kind == 'wps_cfb.py':
                print(json.dumps({'status': 'invalid_or_unsupported', 'support_level': 'none', 'message': str(exc),
                                  'biff_parsed': False, 'native_et_ett_parsed': False, 'cells_decoded': False}, ensure_ascii=False))
            elif kind == 'verify_wps_sample.py':
                report = function.__globals__['_make_report']()
                print(json.dumps(function.__globals__['_failure'](report, 'task_boundary_failed'), ensure_ascii=False))
            else:
                print(json.dumps({'status': 'failed', 'error': str(exc)}, ensure_ascii=False),
                      file=sys.stdout if kind == 'export_wps.py' else sys.stderr)
            return 1
        finally:
            if token is not None:
                ACTIVE.reset(token)
            if access is not None:
                os.close(access.descriptor)
    return guarded


def delivery_filename(value):
    """Validate caller spelling before Path can discard a leading './'."""
    raw = os.fspath(value)
    if (not isinstance(raw, str) or not raw or raw in ('.', '..')
            or any(character in raw for character in ('/', '\\', ':', '\0'))):
        raise ValueError('Output must be a bare filename in the task working directory')
    path = Path(raw)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Output must not be a symbolic link, hard link or non-regular file')
    return path


def validate_argument(path, *, output=False):
    if output:
        delivery_filename(path)
    access = ACTIVE.get()
    if access is not None:
        access.validate(path, output=output)


def write_capabilities():
    if (not all(call in os.supports_dir_fd for call in (os.mkdir, os.link, os.unlink))
            or os.link not in os.supports_follow_symlinks):
        raise ValueError('Safe exclusive task publication unavailable')


def _verify_published(path, identity, expected, *, parent=None, candidate=False):
    options = {} if parent is None else {'dir_fd': parent}
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), **options)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        size, digest = expected
        if (not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != identity
                or before.st_size != size or hashlib.sha256(stream.read(size + 1)).hexdigest() != digest):
            raise ValueError('Candidate bytes changed; publication stopped' if candidate
                             else 'Published file differs from validated candidate; inspect before retry')
        after = os.fstat(stream.fileno())
        named = os.stat(path, follow_symlinks=False, **options)
        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (named.st_dev, named.st_ino) != identity):
            raise ValueError('Published file changed during verification; inspect before retry')


@contextmanager
def output_candidate(target, *, prefix='.tmp_wps_', exclusive=False):
    """Own a candidate; preserve publication and cleanup as independent outcomes."""
    access = ACTIVE.get()
    state = {'output_written': False, 'publication_target': str(target), 'publication_outcome': 'not_attempted',
             'publication_verification': 'not_run', 'cleanup_complete': False, 'cleanup_issues': []}
    if access is None:
        parent_path = Path(target).parent
        parent_path.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=prefix, suffix='.rtf', dir=parent_path)
        owned = os.fstat(descriptor)
        os.close(descriptor)
        candidate = Path(temporary)
        parent = None
        target_name = target
        parent_context = nullcontext()
    else:
        write_capabilities()
        parent_context = access.parent(target, create=True)
    with parent_context as scoped_parent:
        if access is not None:
            parent, target_name = scoped_parent
            parent_identity = os.fstat(parent)
            temporary = prefix + uuid.uuid4().hex + '.rtf'
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            owned = os.fstat(descriptor)
            os.close(descriptor)
            candidate = Path(target).parent / temporary
        identity = (owned.st_dev, owned.st_ino)
        key = str(candidate.absolute())
        registration = {'identity': identity, 'expected': None}
        registry = dict(WRITES.get() or {})
        registry[key] = registration
        token = WRITES.set(registry)
        options = {} if parent is None else {'dir_fd': parent}

        def publish():
            expected = registration['expected']
            if expected is None:
                raise ValueError('Candidate payload has not been registered')
            _verify_published(temporary, identity, expected, parent=parent, candidate=True)
            current_context = nullcontext((None, target_name)) if access is None else access.parent(target)
            with current_context as (current, current_name):
                if current is not None:
                    now = os.fstat(current)
                    if (now.st_dev, now.st_ino) != (parent_identity.st_dev, parent_identity.st_ino):
                        raise ValueError('Output parent changed; publication stopped')
                state.update(output_written=None, publication_outcome='publication_uncertain')
                try:
                    if current is None:
                        _plain_publish(candidate, target, exclusive)
                    else:
                        os.link(temporary, current_name, src_dir_fd=parent, dst_dir_fd=current, follow_symlinks=False)
                except FileExistsError:
                    state.update(output_written=False, publication_outcome='not_published_existing_name')
                    raise
                state.update(output_written=True, publication_outcome='published_unverified')
                try:
                    _verify_published(current_name, identity, expected, parent=current)
                except (OSError, ValueError):
                    state.update(publication_outcome='published_verification_failed', publication_verification='failed')
                    raise
                state.update(publication_outcome='published_verified', publication_verification='identity_and_bytes_verified')
                state['published_sha256'] = expected[1]
            return state

        publish.receipt = state
        primary = None
        try:
            yield candidate, publish
        except BaseException as exc:
            primary = exc
            raise
        finally:
            cleanup_error = None
            try:
                remaining = os.stat(temporary, follow_symlinks=False, **options)
                if (remaining.st_dev, remaining.st_ino) != identity:
                    raise ValueError('Candidate replaced; unknown file retained')
                os.unlink(temporary, **options)
                state['cleanup_complete'] = True
            except FileNotFoundError:
                state['cleanup_complete'] = True
            except (OSError, ValueError) as exc:
                cleanup_error = exc
                state['cleanup_issues'].append({'reason': str(exc), 'temporary': str(candidate)})
            finally:
                WRITES.reset(token)
            if primary is not None:
                primary.publication = dict(state)
            elif cleanup_error is not None:
                cleanup_error.publication = dict(state)
                raise cleanup_error


def write_candidate(path, data):
    access = ACTIVE.get()
    registration = (WRITES.get() or {}).get(str(Path(path).absolute()))
    if registration is None:
        raise ValueError('Candidate ownership was not registered')
    flags = os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    if access is None:
        descriptor = os.open(path, flags)
    else:
        with access.parent(path) as (parent, name):
            descriptor = os.open(name, flags, dir_fd=parent)
    with os.fdopen(descriptor, 'wb') as stream:
        info = os.fstat(stream.fileno())
        if info.st_nlink != 1 or (info.st_dev, info.st_ino) != registration['identity']:
            raise ValueError('Candidate ownership changed')
        stream.write(data)
    registration['expected'] = (len(data), hashlib.sha256(data).hexdigest())


def _plain_publish(candidate, target, exclusive):
    if exclusive:
        os.link(candidate, target)
    else:
        os.replace(candidate, target)
