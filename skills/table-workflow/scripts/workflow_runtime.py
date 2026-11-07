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
    if output:
        require('/' not in value and ':' not in value and value not in ('.', '..'),
                'Output must be a filename only in the task working directory')
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


class LegacyDependencies:
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


    def open_snapshot(self, raw, source):
        return self.xlsx.Workbook.from_bytes(raw, source)

    def is_native(self, book):
        return isinstance(book, self.xlsx.Workbook)

    def merges(self, book, name):
        if hasattr(book, 'wps_merges'):
            return [node['ref'] for node in book.wps_merges[name]]
        tree = self.xlsx.xml(book.parts[book.sheets[name]])
        return [node.get('ref') for node in tree.findall(self.xlsx.NS + 'mergeCells/' + self.xlsx.NS + 'mergeCell')]

    def target_headers(self, book, sheet, bounds, header_rows, read_cell):
        return self.headers.reconstruct(book, sheet, bounds, header_rows, read_cell)

    def lookup(self, left, right, config):
        from xlsx_lookup import lookup_regions
        return lookup_regions(left, right, config)

    def addresses(self, refs):
        return [self.xlsx.address(ref) for ref in refs]

    def typed_values(self, cells):
        return [self.analysis.typed_value(cell) for cell in cells]

    def metadata(self, path, raw):
        book = self.load(path, raw)
        return {'schema_version': 'workbook_metadata_v1', 'sheet_names': list(book.cells),
                'native': self.is_native(book), 'source_status': getattr(book, 'wps_source_status', 'ok'),
                'source_issues': getattr(book, 'wps_issues', []), 'source_coverage': getattr(book, 'wps_source_coverage', {}),
                'input_schema': getattr(book, 'wps_input_schema', None)}

    def evaluate_bound(self, expression, sheet, bindings):
        def resolve(actual_sheet, ref):
            require(actual_sheet == sheet and ref in bindings, 'Formula reference outside declared bindings')
            return bindings[ref]
        return self.formula.evaluate(expression, sheet, resolve)

    def evaluate_many(self, expression, sheet, bindings):
        return [self.evaluate_bound(expression, sheet, item) for item in bindings]

    def select_rows(self, rows, excluded, filters):
        return [(number, row) for number, row in rows if number not in excluded and self.analysis.matches(row, filters)]

    def verify_preserved(self, original, candidate, writes):
        from xlsx_cli import verify
        return verify(original, candidate, writes)

    def assert_written(self, candidate, output):
        require(self.xlsx.Workbook.load(output).parts == candidate.parts, 'Written package does not match validated package')

    def finish_delivery(self, result, path, protected):
        from xlsx_delivery import finish_delivery
        finish_delivery(result, path, protected)

    def close(self):
        pass


class Dependencies(LegacyDependencies):
    """Default CLI transport; legacy in-process adapter remains explicitly opt-in."""

    def __init__(self, xlsx_root, et_root=None):
        # Reuse environment checks without importing any dependency modules.
        # The legacy initializer is intentionally not called.
        self.roots = {}
        self.checks = {}
        for name, value, script, command in [('xlsx', xlsx_root, 'stdlib_xlsx.py', 'check'), ('et', et_root, 'read_et.py', '--check')]:
            if value is None:
                continue
            root = Path(value)
            require(root.is_absolute() and (root / 'SKILL.md').is_file(), 'Supply the confirmed absolute ' + name + ' Skill root')
            require(('name: ' + name + '\n') in (root / 'SKILL.md').read_text(encoding='utf-8'), 'Dependency Skill identity mismatch')
            child = self.run_process(name, [str(root / 'scripts' / script), command])
            if name == 'xlsx':
                try:
                    payload = json.loads(child.stdout)
                except ValueError as exc:
                    raise DependencyCheckError('Malformed XLSX self-check', {'exit_code': child.returncode,
                        'stdout': child.stdout, 'stderr': child.stderr}) from exc
                ready = (isinstance(payload, dict) and payload.get('status') == 'ready'
                         and payload.get('check_kind') == 'environment' and payload.get('artifact_validation') == 'not_run')
            else:
                ready = ('environment-ok' in child.stdout.splitlines() and 'ooxml-sheet=available' in child.stdout.splitlines()
                         and 'check_kind=environment artifact_validation=not_run' in child.stdout.splitlines())
            require(child.returncode == 0 and ready, name + ' environment check failed or missing success marker')
            entry = 'xlsx_cli.py' if name == 'xlsx' else 'et_cli.py'
            capability = self.run_process(name, [str(root / 'scripts' / entry), 'capabilities'])
            advertised = json.loads(capability.stdout)
            from table_workflow_cli_protocol import PROTOCOL
            require(advertised.get('protocol') == PROTOCOL and advertised.get('interface_version') == (2 if name == 'xlsx' else 1)
                    and advertised.get('skill') == name, 'Incompatible dependency capability interface')
            if name == 'xlsx':
                require(advertised.get('delivery_filename_only') is True, 'Dependency lacks filename-only delivery capability')
            needed = ({'open', 'create', 'extract', 'region', 'target_headers', 'resolve_column', 'address', 'column_name',
                       'address_many', 'typed_value', 'typed_values', 'open_metadata', 'release', 'validate_query', 'matches', 'index_rows', 'summarize', 'lookup', 'evaluate',
                       'patch', 'calculate', 'verify', 'publish', 'delivery', 'evaluate_many', 'select_rows'} if name == 'xlsx' else {'read', 'metadata'})
            require(needed <= set(advertised.get('operations', [])), 'Dependency lacks required capabilities')
            self.roots[name] = root
            self.checks[name] = {'exit_code': child.returncode, 'stdout': child.stdout, 'stderr': child.stderr,
                                 'skill_sha256': hashlib.sha256((root / 'SKILL.md').read_bytes()).hexdigest(),
                                 'interface': advertised}
        from workflow_cli_client import Client, bind_api
        self.client = Client(self.roots['xlsx'])
        bind_api(self)
        # Compatibility-facing ET adapter; bytes still cross only the CLI boundary.
        from types import SimpleNamespace
        self.et = SimpleNamespace(read_bytes=self.read_et, encode_result=lambda value: json.dumps(value, ensure_ascii=False)) if et_root else None

    @staticmethod
    def run_process(name, arguments, payload=None, allowed=(0,)):
        try:
            child = subprocess.run([sys.executable, '-I', '-S', '-B', *arguments], input=payload, capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DependencyCheckError(name + ' process failed', {'dependency': name,
                'state': 'timed_out' if isinstance(exc, subprocess.TimeoutExpired) else 'not_started',
                'exit_code': None, 'stdout': str(getattr(exc, 'stdout', '') or ''), 'stderr': str(getattr(exc, 'stderr', '') or '')}) from exc
        def decoded(value):
            return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
        if child.returncode not in allowed:
            raise DependencyCheckError(name + ' process failed', {'dependency': name, 'state': 'signaled' if child.returncode < 0 else 'failed',
                'exit_code': child.returncode if child.returncode >= 0 else None, 'stdout': decoded(child.stdout),
                'stderr': decoded(child.stderr)})
        return subprocess.CompletedProcess(child.args, child.returncode,
                                           decoded(child.stdout), decoded(child.stderr))

    def et_request(self, operation, raw, source):
        from table_workflow_cli_protocol import PROTOCOL, dumps, loads
        require('et' in self.roots, 'ET dependency required; supply its confirmed root')
        request = dumps({'protocol': PROTOCOL, 'id': 1, 'operation': operation, 'parameters': {'raw': raw, 'source': source}})
        child = self.run_process('et', [str(self.roots['et'] / 'scripts' / 'et_cli.py'), operation], request, allowed=(0, 1, 3))
        response = loads(child.stdout.encode('utf-8'))
        require(response.get('protocol') == PROTOCOL and response.get('id') == 1, 'ET response contract failed')
        if response.get('ok') is not True:
            from workflow_cli_client import CapabilityError
            raise CapabilityError(response['error'])
        result = response['result']
        require(result['exit_code'] == child.returncode, 'ET exit code mismatch')
        return result

    def read_et(self, raw, source):
        return json.loads(self.et_request('read', raw, source)['extraction'])

    def metadata(self, path, raw):
        native = Path(path).suffix.lower() in ('.xlsx', '.xlsm')
        if not native:
            require(Path(path).suffix.lower() in ('.et', '.ett'), 'Unsupported source format')
            result = self.et_request('metadata', raw, path)
            metadata = result['metadata']
            require(result['exit_code'] == (0 if metadata.get('source_status') == 'ok' else 3), 'ET metadata status mismatch')
        else:
            metadata = self.client.call('open_metadata', raw=raw, source=path)
            handle = metadata.get('handle')
            require(type(handle) is int and handle >= 0, 'Invalid metadata handle')
            # Discovery does not retain parsed workbooks or accidentally hydrate cells.
            require(self.client.call('release', handle=handle) is True, 'Metadata handle release failed')
        require(metadata.get('schema_version') == 'workbook_metadata_v1' and metadata.get('native') is native,
                'Invalid workbook metadata version or source kind')
        names = metadata.get('sheet_names')
        require(isinstance(names, list) and all(isinstance(name, str) and bool(name) for name in names)
                and len(set(names)) == len(names), 'Invalid worksheet names metadata')
        require(metadata.get('source_status') in (('ok',) if native else ('ok', 'partial'))
                and isinstance(metadata.get('source_issues'), list) and isinstance(metadata.get('source_coverage'), dict)
                and (metadata.get('input_schema') is None if native else
                     metadata.get('input_schema') in ('biff8_readonly_v1', 'ooxml_spreadsheet_readonly_v1')),
                'Incomplete source metadata diagnostics')
        return metadata

    def typed_values(self, cells):
        result = []
        for offset in range(0, len(cells), 10000):
            batch = cells[offset:offset + 10000]
            values = self.client.call('typed_values', cells=batch)
            require(isinstance(values, list) and len(values) == len(batch), 'Incomplete typed value results')
            require(all(isinstance(value, tuple) and len(value) == 2
                        and value[0] in ('blank', 'date_text', 'boolean', 'number', 'text') for value in values),
                    'Invalid typed value results')
            result.extend(values)
        return result

    def load(self, path, raw):
        if Path(path).suffix.lower() in ('.et', '.ett'):
            require(self.et is not None, 'ET dependency required; supply its confirmed root')
            result = self.et.read_bytes(raw, source=path)
            payload = self.et.encode_result(result).encode('utf-8')
            return self.wps.load_result_bytes(payload, self.xlsx.address)
        require(Path(path).suffix.lower() in ('.xlsx', '.xlsm'), 'Unsupported source format')
        return self.xlsx.Workbook.from_bytes(raw, path)

    def is_native(self, book):
        return book.native

    def merges(self, book, name):
        return book.merges[name]

    def target_headers(self, book, sheet, bounds, header_rows, read_cell):
        return self.client.call('target_headers', handle=book.handle, sheet=sheet, bounds=bounds, header_rows=header_rows)

    def lookup(self, left, right, config):
        return self.client.call('lookup', left=left, right=right, config=config)

    def verify_preserved(self, original, candidate, writes):
        return self.client.call('verify', original=original.handle, candidate=candidate.handle, writes=writes)

    def assert_written(self, candidate, output):
        require(candidate.written and candidate.written['verification'] == 'written_package_passed', 'Publication verification missing')
        require(snapshot(str(output))[1] == candidate.written['sha256'], 'Written package changed after verification')

    def finish_delivery(self, result, path, protected):
        from workflow_cli_client import Client
        # Delivery owns no workbook handles. A fresh invocation observes the
        # current task environment without mutating an existing worker session.
        client = Client(self.roots['xlsx'])
        try:
            result.update(client.call('delivery', output=path, protected_inputs=protected))
        finally:
            client.close()

    def close(self):
        self.client.close()


    def addresses(self, refs):
        result = []
        for offset in range(0, len(refs), 10000):
            batch = refs[offset:offset + 10000]
            values = self.client.call('address_many', refs=batch)
            require(isinstance(values, list) and len(values) == len(batch), 'Incomplete address results')
            result.extend(values)
        return result

    def evaluate_bound(self, expression, sheet, bindings):
        return self.client.call('evaluate', formula=expression, sheet=sheet, bindings=bindings)

    def evaluate_many(self, expression, sheet, bindings):
        result = self.client.call('evaluate_many', formula=expression, sheet=sheet, bindings=bindings)
        require(isinstance(result, list) and len(result) == len(bindings), 'Formula batch result count mismatch')
        return result

    def select_rows(self, rows, excluded, filters):
        return self.client.call('select_rows', rows=rows, excluded=sorted(excluded), filters=filters)
