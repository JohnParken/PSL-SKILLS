"""Explicit dependency loading and bounded task-file access for table workflows."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import uuid

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_REQUEST_BYTES = 2 * 1024 * 1024


class DependencyCheckError(ValueError):
    """Retain real dependency process streams on failed self-checks."""

    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(value, required, optional=()):
    require(isinstance(value, dict) and set(required) <= set(value)
            and not set(value) - set(required) - set(optional),
            'Expected fields: ' + ', '.join(required) + '; optional: ' + ', '.join(optional))


def text_field(value, label):
    require(isinstance(value, str) and 0 < len(value.strip()) <= 1000, label + ' must be nonempty text within 1000 characters')
    return value


def task_path(value, *, output=False):
    require(isinstance(value, str) and bool(value) and '\\' not in value and '\0' not in value, 'Invalid task path')
    path = Path(value)
    require(not path.is_absolute() and '..' not in path.parts and bool(path.parts), 'Use task-relative paths without parent traversal')
    current = Path('.')
    for part in path.parts:
        current = current / part
        require(not current.is_symlink(), 'Symlink task paths are refused')
    require(path.parent.is_dir(), 'Task parent directory is missing')
    if output:
        require(not path.exists(), 'Output already exists; choose a new name')
    else:
        require(path.is_file(), 'Input must be an accessible regular file')
    return path


def snapshot(value):
    path = task_path(value)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_FILE_BYTES, 'Input is not regular or exceeds 64 MiB')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(before.st_size + 1)
        after = os.fstat(descriptor)
        require(len(raw) == before.st_size and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'Input changed while reading')
    finally:
        os.close(descriptor)
    return raw, hashlib.sha256(raw).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key: ' + key)
        result[key] = value
    return result


def read_request(stream):
    from decimal import Decimal
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    require(len(raw) <= MAX_REQUEST_BYTES, 'Request exceeds 2 MiB')
    def reject(value):
        raise ValueError('Nonfinite JSON value: ' + value)
    result = json.loads(raw, object_pairs_hook=unique_object, parse_float=Decimal, parse_constant=reject)
    pending = [(result, 0)]
    while pending:
        value, depth = pending.pop()
        require(depth <= 32, 'Request nesting exceeds 32')
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    return result


def probe(directory):
    path = Path(directory) / ('workflow-probe-' + uuid.uuid4().hex)
    # Explicitly register this unique, nonexistent probe before exclusive creation.
    task_path(path.as_posix(), output=True)
    created = False
    try:
        with path.open('xb') as stream:
            created = True
            stream.write(b'workflow-write-check')
        require(path.read_bytes() == b'workflow-write-check', 'Write probe readback failed')
    finally:
        if created:
            path.unlink()


class Dependencies:
    """Roots are supplied from actual SKILL.md reads, never sibling discovery."""

    def __init__(self, xlsx_root, et_root=None):
        self.roots = {}
        self.checks = {}
        for name, value, entry in [('xlsx', xlsx_root, 'stdlib_xlsx.py'), ('et', et_root, 'read_et.py')]:
            if value is None:
                continue
            root = Path(value)
            require(root.is_absolute() and (root / 'SKILL.md').is_file(), 'Supply the confirmed absolute ' + name + ' Skill root')
            require(('name: ' + name + '\n') in (root / 'SKILL.md').read_text(encoding='utf-8'), 'Dependency Skill identity mismatch')
            script = root / 'scripts' / entry
            require(script.is_file(), 'Dependency script missing: ' + name)
            command = 'check' if name == 'xlsx' else '--check'
            try:
                child = subprocess.run([sys.executable, '-I', '-S', '-B', str(script), command], capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired as exc:
                def decoded(value):
                    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
                raise DependencyCheckError(name + ' self-check timed out', {
                    'dependency': name, 'state': 'timed_out', 'exit_code': None,
                    'stdout': decoded(exc.stdout), 'stderr': decoded(exc.stderr)}) from exc
            except OSError as exc:
                raise DependencyCheckError(name + ' self-check could not start', {
                    'dependency': name, 'state': 'not_started', 'exit_code': None, 'stdout': '', 'stderr': '', 'reason': str(exc)}) from exc
            evidence = {'dependency': name, 'exit_code': child.returncode if child.returncode >= 0 else None,
                        'stdout': child.stdout, 'stderr': child.stderr}
            if child.returncode < 0:
                evidence.update(state='signaled', signal=-child.returncode)
            if name == 'xlsx':
                try:
                    payload = json.loads(child.stdout)
                except (ValueError, TypeError):
                    payload = None
                ready = (isinstance(payload, dict) and payload.get('status') == 'ready'
                         and payload.get('check_kind') == 'environment' and payload.get('artifact_validation') == 'not_run')
            else:
                ready = ('environment-ok' in child.stdout.splitlines()
                         and 'check_kind=environment artifact_validation=not_run' in child.stdout.splitlines())
            if child.returncode != 0 or not ready:
                raise DependencyCheckError(name + ' environment check failed or missing success marker', evidence)
            if name == 'et':
                if 'ooxml-sheet=available' not in child.stdout.splitlines():
                    raise DependencyCheckError('ET OOXML requires Expat >=2.6', evidence)
            self.checks[name] = {'exit_code': child.returncode, 'stdout': child.stdout, 'stderr': child.stderr,
                                 'skill_sha256': hashlib.sha256((root / 'SKILL.md').read_bytes()).hexdigest()}
            self.roots[name] = root
            sys.path.insert(0, str(root / 'scripts'))
        self.xlsx = importlib.import_module('stdlib_xlsx')
        self.analysis = importlib.import_module('xlsx_analysis')
        self.headers = importlib.import_module('xlsx_headers')
        self.wps = importlib.import_module('xlsx_wps_result')
        self.match = importlib.import_module('xlsx_match')
        self.math = importlib.import_module('xlsx_advanced')
        self.formula = importlib.import_module('formula_engine')
        require(hasattr(self.analysis, 'region_from_book') and hasattr(self.analysis, 'summarize_region')
                and hasattr(self.wps, 'load_result_bytes'), 'XLSX dependency lacks the checked in-memory workflow API')
        self.et = importlib.import_module('read_et') if et_root is not None else None

    def load(self, path, raw):
        if Path(path).suffix.lower() in ('.et', '.ett'):
            require(self.et is not None, 'ET dependency required; supply its confirmed root')
            result = self.et.read_bytes(raw, source=path)
            # Only the original, bounded, typed extraction JSON is accepted here.
            payload = self.et.encode_result(result).encode('utf-8')
            return self.wps.load_result_bytes(payload, self.xlsx.address)
        require(Path(path).suffix.lower() in ('.xlsx', '.xlsm'), 'Unsupported source format')
        return self.xlsx.Workbook.from_bytes(raw, path)

    def check(self, write=False):
        require(sys.version_info >= (3, 12), 'Python >=3.12 required; reference validation uses 3.12.11')
        require(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_NONBLOCK'), 'Safe file capabilities unavailable')
        for root in [Path(__file__).resolve().parents[1], *self.roots.values()]:
            require(not Path.cwd().resolve().is_relative_to(root.resolve()), 'Task working directory must be outside Skill installation')
        if write:
            probe('.')
        return {'status': 'environment-ok', 'check_kind': 'environment', 'artifact_validation': 'not_run',
                'python': sys.version.split()[0], 'task_writable': True if write else 'not_checked', 'dependencies': self.checks}
