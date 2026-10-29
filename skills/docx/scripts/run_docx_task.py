"""Own generation helpers from creation through child exit and verified cleanup."""
import argparse
import hashlib
import io
from contextlib import contextmanager, redirect_stdout, redirect_stderr
try:
    import fcntl
except ImportError:
    fcntl = None
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from docx_outcome import failure, spec_identity
from cleanup_task import TaskFiles, plan_cleanup, seal_cleanup, cleanup_task, retire_cleanup_manifest, _unique_object
from controlled_cleanup import finalize_failed_cleanup
from create_docx import _delivery_output_path
from archive_deliverables import validate_output_path
from office.runtime import check_runtime
from docx_task_state import (
    MAX_ATTEMPTS,
    RUNNER_SCHEMA,
    LEGACY_RUNNER_SCHEMA,
    RECEIPT_MODE,
    helper_path,
    validate_manifest,
    validate_receipt,
    validate_config,
)

MAX_TASK_BYTES = 2 * 1024 * 1024
WORKER_SOURCE = '''import sys
sys.path.insert(0, sys.argv[1])
from run_docx_task import worker_main
raise SystemExit(worker_main(sys.argv[2]))
'''


def _runner_environment():
    if fcntl is None or not all(hasattr(fcntl, name) for name in ('flock', 'LOCK_EX', 'LOCK_NB', 'LOCK_UN')):
        raise RuntimeError('Automatic task requires standard-library POSIX file locks')
    with TaskFiles():
        pass  # Check no-follow descriptor-relative operations before planning any files.


def task_environment():
    """Check mandatory conditions only; optional archival is handled after generation."""
    diagnostics = io.StringIO()
    try:
        with redirect_stdout(diagnostics), redirect_stderr(diagnostics):
            supported = check_runtime()
        if not supported:
            raise RuntimeError('Python/Expat runtime check failed: ' + diagnostics.getvalue().strip())
    except (OSError, ValueError, RuntimeError) as exc:
        return {'status': 'failed', 'errors': [{'check': 'python_expat_runtime', 'reason': str(exc)}],
                'diagnostics': diagnostics.getvalue()}
    try:
        _runner_environment()
    except (OSError, ValueError, RuntimeError) as exc:
        return {'status': 'failed', 'errors': [{'check': 'file_locks_or_safe_file_operations', 'reason': str(exc)}],
                'diagnostics': diagnostics.getvalue()}
    try:
        with TaskFiles() as task:
            fcntl.flock(task.root, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(task.root, fcntl.LOCK_UN)
    except (OSError, ValueError, RuntimeError) as exc:
        return {'status': 'failed', 'errors': [{'check': 'file_locks', 'reason': str(exc)}],
                'diagnostics': diagnostics.getvalue()}
    probe_verified = False
    warnings = []
    try:
        with tempfile.TemporaryFile(mode='w+b', dir='.') as probe:
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            probe.write(b'docx-task-check')
            probe.flush()
            os.fsync(probe.fileno())
            probe.seek(0)
            if probe.read() != b'docx-task-check':
                raise RuntimeError('Task root probe readback differs from written bytes')
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
            probe_verified = True
    except (OSError, ValueError, RuntimeError) as exc:
        if not probe_verified:
            return {'status': 'failed', 'errors': [{'check': 'task_root_write_or_probe_lock', 'reason': str(exc)}],
                    'diagnostics': diagnostics.getvalue()}
        warnings.append('environment-warning: probe cleanup failed after successful checks: ' + str(exc))
    return {'status': 'ready', 'errors': [], 'warnings': warnings, 'diagnostics': diagnostics.getvalue()}


def _environment_failure(report):
    reason = '; '.join(item['check'] + ': ' + item['reason'] for item in report['errors'])
    return {**_retained(None, reason, state='environment_failed'), 'environment': report,
            'error_code': 'environment_failed', 'next_action': 'fix_environment'}


def _with_environment(result, report):
    result['environment'] = report
    result['warnings'] = list(result.get('warnings', [])) + report.get('warnings', [])
    return result


def check_task_runtime():
    """Optional diagnostic command; create/retry already enforce the same mandatory gate."""
    report = task_environment()
    if report['status'] != 'ready':
        print('check_kind=environment artifact_validation=not_run', file=sys.stderr, flush=True)
        print('auto-cleanup-check-failed: ' + _environment_failure(report)['message'], file=sys.stderr, flush=True)
        return False
    print(report['diagnostics'], end='')
    for warning in report.get('warnings', []):
        print(warning, file=sys.stderr)
    print('auto-cleanup=available')
    print('output_dir=. writable=True')
    print('check_kind=environment artifact_validation=not_run')
    print('check-ok', flush=True)
    return True


def _load(manifest):
    with TaskFiles() as task:
        data = task.load(manifest)
        return validate_manifest(data, manifest, task)


def _save(manifest, data):
    with TaskFiles() as task:
        task.save(manifest, data)


def _check_created(data, *, pending=False):
    runner = data['runner']
    with TaskFiles() as task:
        if runner['schema'] == LEGACY_RUNNER_SCHEMA:
            if task.inspect(runner['workspace']) != runner.get('workspace_identity'):
                raise ValueError('Workspace identity changed; helpers retained')
        created = runner.get('created', {})
        names = [helper_path(runner, n) for n in ('worker.py', 'spec.json', 'task.lock')]
        if set(created) != set(names):
            raise ValueError('Helper creation was interrupted; ownership unconfirmed')
        for path, expected in created.items():
            if pending and path == helper_path(runner, 'spec.json'):
                continue
            current = task.inspect(path, contents=True)
            if current != expected or current.get('links') != 1:
                raise ValueError('Created helper changed or became a hard link; retained: ' + path)


@contextmanager
def _locked(data):
    runner = data['runner']
    _check_created(data, pending=runner.get('spec_update') is not None)
    lock_path = helper_path(runner, 'task.lock')
    with TaskFiles() as task, task.parent(lock_path) as (parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            expected = runner['created'][lock_path]
            info = os.fstat(descriptor)
            if [info.st_dev, info.st_ino] != expected['identity']:
                raise ValueError('Task lock changed')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError('Task worker or another controller is still active; no cleanup') from exc
            yield descriptor
        finally:
            os.close(descriptor)


@contextmanager
def _controlled(manifest):
    """Stable directory lock survives manifest replacement and helper deletion.

    Controllers in one task directory serialize; children inherit this same lock.
    The state is loaded only after the lock is held, never from a stale snapshot.
    """
    _runner_environment()
    with TaskFiles() as task:
        try:
            fcntl.flock(task.root, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Another task controller or worker is active; no operation') from exc
        yield _load(manifest), task.root


def _recover_spec(manifest, data):
    runner = data['runner']
    update = runner.get('spec_update')
    if update is None:
        _check_created(data)
        return
    _check_created(data, pending=True)
    path = helper_path(runner, 'spec.json')
    stage = runner['retry_stage']
    with TaskFiles() as task:
        current = task.inspect(path, contents=True)
        staged = task.optional(stage, contents=True)
        if update['phase'] == 'preparing':
            if current != update['old'] or staged is not None:
                raise ValueError('Specification preparation interrupted; unconfirmed file retained')
            runner.pop('spec_update')
            task.save(manifest, data)
            return
        if current == update['old'] and staged == update['next']:
            with task.parent(stage) as (parent, source):
                with task.parent(path) as (destination_parent, destination):
                    os.replace(source, destination, src_dir_fd=parent, dst_dir_fd=destination_parent)
        elif current != update['next'] or staged is not None:
            raise ValueError('Pending specification differs from recorded old/new identities; retained')
        runner['created'][path] = update['next']
        runner.pop('spec_update')
        task.save(manifest, data)
    _check_created(data)


def _update_spec(manifest, data, spec):
    runner = data['runner']
    if runner['schema'] != RUNNER_SCHEMA:
        raise ValueError('Legacy task retry is unsupported; existing helpers are retained')
    path = helper_path(runner, 'spec.json')
    stage = helper_path(runner, 'retry-spec.json')
    with TaskFiles() as task:
        config = validate_config(task.load(path), runner, [p['path'] for p in data['protected']])
        config['spec'] = spec
        raw = (json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        if len(raw) > MAX_TASK_BYTES:
            raise ValueError('Corrected specification exceeds task budget')
        if task.optional(stage) is not None:
            raise ValueError('Retry staging file already exists; not adopted')
        if runner.get('retry_stage') != stage:
            raise ValueError('Retry staging path differs from the original flat plan')
        runner['spec_update'] = {'schema': 'docx_spec_update_v1', 'phase': 'preparing',
                                 'old': runner['created'][path], 'sha256': hashlib.sha256(raw).hexdigest()}
        task.save(manifest, data)  # Intent is durable before any helper changes.
        snapshot = None
        stage_identity = None
        try:
            with task.parent(stage) as (parent, leaf):
                descriptor = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                info = os.fstat(descriptor)
                stage_identity = [info.st_dev, info.st_ino]
                with os.fdopen(descriptor, 'wb') as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
            snapshot = task.inspect(stage, contents=True)
            runner['spec_update'].update(phase='prepared', next=snapshot)
            task.save(manifest, data)  # Exact new inode/hash is durable before replace.
        except (OSError, ValueError, RuntimeError):
            # If preparation fails, the old spec is untouched. Remove only the exact
            # stage captured by this live controller; ambiguous/crashed creations stay.
            if stage_identity is not None:
                durable = _load(manifest)['runner'].get('spec_update')
                # A save can raise after committing. Never remove a stage already
                # authorized by the durable prepared record; resume will commit it.
                committed = (isinstance(durable, dict) and durable.get('phase') == 'prepared'
                             and snapshot is not None and durable.get('next') == snapshot)
                current = task.optional(stage, contents=snapshot is not None)
                owned = (current == snapshot if snapshot is not None else
                         isinstance(current, dict) and current.get('identity') == stage_identity
                         and current.get('links') == 1)
                if not committed and owned:
                    with task.parent(stage) as (parent, leaf):
                        os.unlink(leaf, dir_fd=parent)
            raise
    _recover_spec(manifest, data)


def _retained(manifest, message, *, state='retry_pending'):
    return {**failure('task_state_unconfirmed', message, action='inspect_task'), 'task_status': state,
            'task_manifest': manifest, 'cleanup': {'status': 'retained', 'cleanup_complete': False}}


def _finish_delivery(manifest, result, expected, operation):
    """Keep a verified delivery successful when ancillary cleanup cannot finish."""
    try:
        if result.get('temporary_residuals'):
            raise ValueError('Archive temporary cleanup is incomplete; inspect exact residual paths')
        report = operation()
        if not isinstance(report, dict) or type(report.get('cleanup_complete')) is not bool:
            raise ValueError('Cleanup did not return a valid completion report')
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        report = {'status': 'failed', 'cleanup_complete': False, 'deleted': [], 'retained': [],
                  'failed': [{'path': manifest, 'reason': str(exc)}], 'manifest_retained': manifest,
                  'state_unknown': True}
    # Never turn a missing/changed delivery into a warning, even after cleanup errors.
    with TaskFiles() as task:
        current = task.inspect(result['output'], contents=True)
        if not current.get('size') or current != expected:
            raise ValueError('Published document changed or is missing after cleanup; inspect before delivery')
    report['deliverables_unchanged'] = True
    payload = {**result, 'task_manifest': manifest,
               'task_status': 'completed' if report['cleanup_complete'] else 'cleanup_incomplete',
               'cleanup': report, 'warnings': list(result.get('warnings', []))}
    if not report['cleanup_complete']:
        reasons = [item['reason'] for key in ('failed', 'retained') for item in report.get(key, [])]
        payload['warnings'].append('cleanup-warning: ' + ('; '.join(reasons) or 'temporary files retained'))
    return payload


def _finish_locked(manifest, data, *, worker_warning=None):
    runner = data['runner']
    _check_created(data)
    receipt_path = helper_path(runner, 'receipt.json')
    with TaskFiles() as task:
        receipt = task.load(receipt_path)
        validate_receipt(receipt, manifest, runner)
        result = receipt['result']
        current = task.inspect(runner['output'], contents=True)
        if not current.get('size') or current != receipt.get('output_fingerprint'):
            raise ValueError('Published document changed or is missing; helpers retained')
        receipt_fingerprint = task.inspect(receipt_path, contents=True)
        if receipt_fingerprint['links'] != 1:
            raise ValueError('Receipt must not be a hard link')
        if (runner.get('receipt_mode') == RECEIPT_MODE
                and receipt_fingerprint != runner.get('receipt_fingerprint')):
            raise ValueError('Receipt ownership fingerprint is missing or changed; helpers retained')
    warnings = list(result['warnings'])
    for warning in runner.get('delivery', {}).get('warnings', []):
        if warning not in warnings:
            warnings.append(warning)
    if worker_warning is not None and worker_warning not in warnings:
        warnings.append(worker_warning)
    result = {**result, 'warnings': warnings}
    # Save the delivery separately before deletion, so a controller can resume after any deletion.
    def finish():
        runner.update(state='delivered', delivery=result)
        _save(manifest, data)
        seal_cleanup(manifest, outcome='success', verified=True, archive_status=result['archive_status'])
        return cleanup_task(manifest)
    payload = _finish_delivery(manifest, result, receipt['output_fingerprint'], finish)
    payload['attempts'] = runner['attempts']
    return _retire_delivery(manifest, payload)


def _retire_delivery(manifest, payload):
    report = retire_cleanup_manifest(manifest, payload['cleanup'])
    if report.get('manifest_removed'):
        payload['task_manifest'] = None
    if not report['cleanup_complete']:
        payload['task_status'] = 'cleanup_incomplete'
        payload['warnings'].append('cleanup-warning: task intermediates remain; finish cleanup before closing')
    return payload


def resume_task(manifest):
    """Recover an existing owned task; never rerun generation or adopt unknown files."""
    manifest = Path(manifest).as_posix()
    try:
        with _controlled(manifest) as (data, control_fd):
            if data['phase'] == 'sealed':
                result = data['runner']['delivery']
                expected = data['output_fingerprints'][result['output']]
                # Check before any cleanup as well as after it.
                with TaskFiles() as task:
                    if task.inspect(result['output'], contents=True) != expected:
                        raise ValueError('Published document changed or is missing; no cleanup')
                    if data['runner'].get('receipt_mode') == RECEIPT_MODE:
                        receipt = task.optional(helper_path(data['runner'], 'receipt.json'), contents=True)
                        if receipt is not None and receipt != data['runner'].get('receipt_fingerprint'):
                            raise ValueError('Receipt ownership fingerprint changed; no cleanup')
                return _retire_delivery(manifest, _finish_delivery(manifest, result, expected, lambda: cleanup_task(manifest)))
            with _locked(data):
                _recover_spec(manifest, data)
                return _finish_locked(manifest, data)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        return _retained(manifest, str(exc), state='recovery_pending')


def _after_worker_failure(manifest, worker_error):
    """Recover only a durably recorded, unchanged receipt after the child exited."""
    data = _load(manifest)
    runner = data['runner']
    if runner.get('receipt_mode') == RECEIPT_MODE and 'receipt_fingerprint' in runner:
        try:
            warning = 'worker-warning: verified receipt recovered after worker error: ' + worker_error
            result = _finish_locked(manifest, data, worker_warning=warning)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            return _retained(manifest, worker_error + '; receipt recovery refused: ' + str(exc),
                             state='recovery_pending')
        return result
    runner['state'] = 'recovery_pending' if os.path.lexists(runner['output']) else 'retry_pending'
    try:
        detail = json.loads(worker_error)
    except (ValueError, TypeError):
        detail = None
    if (runner['state'] == 'retry_pending' and isinstance(detail, dict)
            and detail.get('error_code') == 'spec_validation_failed'):
        runner['last_spec_failure'] = detail.get('spec_identity')
    else:
        runner.pop('last_spec_failure', None)
    _save(manifest, data)
    result = _retained(manifest, detail.get('message', worker_error) if isinstance(detail, dict) else worker_error, state=runner['state'])
    result['attempts_remaining'] = max(0, MAX_ATTEMPTS - runner['attempts'])
    if runner.get('last_spec_failure'):
        result.update(error_code='spec_validation_failed', next_action='correct_spec')
    return result


def _print_worker_diagnostics(data):
    """Print textual diagnostics without adding a newline or changing valid bytes.

    Undecodable bytes and encoding-mismatched streams retain their original raw
    transport; they are not safely replaceable textual prompts.
    """
    if not data:
        return
    encoding = getattr(sys.stderr, 'encoding', None) or 'utf-8'
    try:
        text = data.decode(encoding, 'strict')
        if text.encode(encoding, 'strict') != data:
            raise UnicodeError('diagnostic roundtrip mismatch')
    except (UnicodeError, LookupError):
        if hasattr(sys.stderr, 'buffer'):
            sys.stderr.buffer.write(data)  # Exact non-text transport, not a prompt.
        else:
            print(data.decode('utf-8', 'replace'), end='', file=sys.stderr)
        return
    print(text, end='', file=sys.stderr)


def _execute(manifest, data, lock_fd):
    runner = data['runner']
    if runner['schema'] != RUNNER_SCHEMA:
        raise ValueError('Legacy v1 tasks can only be resumed; generation is unsupported and existing helpers are retained')
    _check_created(data)
    if 'receipt_fingerprint' in runner:
        return _retained(manifest, 'A receipt proof is already recorded; resume instead of regenerating',
                         state='recovery_pending')
    if Path(runner['output']).exists() or Path(runner['output']).is_symlink():
        return _retained(manifest, 'Output already exists without a completed receipt; do not regenerate', state='recovery_pending')
    runner['attempts'] += 1
    runner['state'] = 'running'
    runner['receipt_mode'] = RECEIPT_MODE
    _save(manifest, data)
    command = [sys.executable, '-I', '-S', '-B', helper_path(runner, 'worker.py'),
               str(Path(__file__).resolve().parent), manifest]
    try:
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 pass_fds=lock_fd if isinstance(lock_fd, tuple) else (lock_fd,), timeout=120, check=False)
    except subprocess.TimeoutExpired:
        return _after_worker_failure(manifest, 'Generation timed out; worker terminated and waited for')
    if process.stderr:
        _print_worker_diagnostics(process.stderr)
    # The child may have committed its receipt proof before a late housekeeping
    # error. Reload only under the controller lock; never overwrite that proof
    # with the controller's pre-worker snapshot.
    if process.returncode:
        worker_error = process.stdout.decode('utf-8', 'replace').strip() or 'Generation worker failed'
        return _after_worker_failure(manifest, worker_error)
    return _finish_locked(manifest, _load(manifest))


def _create_task(spec, output, *, protected_inputs=(), workspace=None):
    """Create both helper files exclusively, run fixed code, then finish outside its process."""
    environment = task_environment()
    if environment['status'] != 'ready':
        return _environment_failure(environment)
    for warning in environment.get('warnings', []):
        print(warning, file=sys.stderr)
    output = _delivery_output_path(Path.cwd(), str(output)).as_posix()
    protected = [Path(p).as_posix() for p in protected_inputs]
    validate_output_path(Path(output), protected_inputs=protected)
    if os.path.lexists(output):
        raise ValueError('Automatic task refuses an existing final filename; choose an authorized new name')
    workspace = workspace or 'docx-task-' + uuid.uuid4().hex
    if not isinstance(workspace, str) or '/' in workspace or '\\' in workspace or workspace in ('', '.', '..'):
        raise ValueError('Workspace must be a new single task-relative filename prefix')
    manifest = workspace + '.cleanup.json'
    config = {'spec': spec, 'output': output, 'protected_inputs': protected}
    raw = (json.dumps(config, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    if len(raw) > MAX_TASK_BYTES:
        raise ValueError('Automatic task JSON exceeds 2 MiB')
    runner_state = {'schema': RUNNER_SCHEMA, 'workspace': workspace}
    files = [helper_path(runner_state, n) for n in
             ('worker.py', 'spec.json', 'receipt.json', 'task.lock', 'retry-spec.json')]
    plan_cleanup(manifest, files=files, protected=protected, deliverables=[output])
    data = None
    try:
        with TaskFiles() as task:
            data = task.load(manifest)
            data['runner'] = {'schema': RUNNER_SCHEMA, 'workspace': workspace, 'output': output,
                              'created': {}, 'attempts': 0, 'state': 'initializing',
                              'receipt_mode': RECEIPT_MODE,
                              'retry_stage': helper_path(runner_state, 'retry-spec.json')}
            task.save(manifest, data)
            for name, content in (('worker.py', WORKER_SOURCE.encode()), ('spec.json', raw), ('task.lock', b'')):
                path = helper_path(data['runner'], name)
                with task.parent(path) as (parent, leaf):
                    descriptor = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                    with os.fdopen(descriptor, 'wb') as handle:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                data['runner']['created'][path] = task.inspect(path, contents=True)
                task.save(manifest, data)
            data['runner']['state'] = 'ready'
            task.save(manifest, data)
        with _controlled(manifest) as (data, control_fd):
            with _locked(data) as descriptor:
                return _with_environment(_execute(manifest, data, (control_fd, descriptor)), environment)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        return _retained(manifest, str(exc), state='recovery_pending')


def finish_failed_task(result):
    """Failure is terminal: discard owned execution inputs after the worker stops."""
    manifest = result.get('task_manifest')
    if result.get('status') != 'failed' or not manifest:
        return result
    try:
        with TaskFiles() as task:
            fcntl.flock(task.root, fcntl.LOCK_EX | fcntl.LOCK_NB)
            data = task.load(manifest)
            # Never recapture a helper that changed after its owned creation.
            for path, expected in data.get('runner', {}).get('created', {}).items():
                current = task.optional(path, contents=True)
                if current is not None and current != expected:
                    raise ValueError('Owned helper changed before failure cleanup')
            runner = data.get('runner')
            if runner:
                receipt_path = helper_path(runner, 'receipt.json')
                receipt_fingerprint = task.optional(receipt_path, contents=True)
                if receipt_fingerprint is not None:
                    if receipt_fingerprint != runner.get('receipt_fingerprint') or receipt_fingerprint['links'] != 1:
                        raise ValueError('Receipt ownership is unconfirmed; no failure cleanup')
                    receipt = task.load(receipt_path)
                    validate_receipt(receipt, manifest, runner)
                    if task.optional(runner['output'], contents=True) != receipt['output_fingerprint']:
                        raise ValueError('Published output changed; no failure cleanup')
            report = finalize_failed_cleanup(manifest)
        result.update(cleanup=report, task_manifest=None if report.get('manifest_removed') else manifest,
                      task_status='failed_cleaned' if report['cleanup_complete'] else 'cleanup_incomplete',
                      next_action='correct_request_and_start_new_task')
        result.pop('attempts_remaining', None)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        result.update(task_status='cleanup_incomplete', cleanup={
            'cleanup_complete': False, 'status': 'failed', 'message': str(exc)})
    return result


def create_task(spec, output, *, protected_inputs=(), workspace=None):
    return finish_failed_task(_create_task(spec, output, protected_inputs=protected_inputs, workspace=workspace))


def retry_task(manifest, spec):
    """Correct only this task's owned JSON, retaining the original plan and fixed worker."""
    environment = task_environment()
    if environment['status'] != 'ready':
        failure = _environment_failure(environment)
        failure['task_manifest'] = Path(manifest).as_posix()
        return failure
    manifest = Path(manifest).as_posix()
    try:
        with _controlled(manifest) as (data, control_fd):
            runner = data['runner']
            if runner['schema'] == LEGACY_RUNNER_SCHEMA:
                raise ValueError('Legacy v1 task retry is unsupported; keep its existing helper files. Inspect the existing task state before starting a new flat v2 task.')
            if data['phase'] != 'planned' or runner['attempts'] >= MAX_ATTEMPTS:
                raise ValueError('Task is sealed or its three generation attempts are exhausted')
            with _locked(data) as descriptor:
                _recover_spec(manifest, data)
                with TaskFiles() as task:
                    receipt = task.optional(helper_path(runner, 'receipt.json'))
                    if ('receipt_fingerprint' in runner or receipt is not None
                            or os.path.lexists(runner['output'])):
                        raise ValueError('Delivery may already exist; resume instead of regenerating')
                if runner.get('last_spec_failure') == spec_identity(spec):
                    return {**_retained(manifest, 'Unchanged specification after deterministic validation failure'),
                            'error_code': 'unchanged_invalid_spec', 'next_action': 'correct_spec',
                            'attempts_remaining': max(0, MAX_ATTEMPTS - runner['attempts'])}
                _update_spec(manifest, data, spec)
                return _with_environment(_execute(manifest, data, (control_fd, descriptor)), environment)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        return _retained(manifest, str(exc))


def _publish_receipt(manifest, data, receipt):
    """Write the registered receipt exclusively; controllers wait for child exit.

    No staging hard link or staging-file deletion is needed. A partial write is
    never accepted: the controller requires the fingerprint committed afterward.
    """
    runner = data['runner']
    if runner.get('receipt_mode') != RECEIPT_MODE or 'receipt_fingerprint' in runner:
        raise ValueError('Receipt publication mode or existing ownership proof is invalid')
    raw = (json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if len(raw) > MAX_TASK_BYTES:
        raise ValueError('Receipt exceeds size budget')
    path = helper_path(runner, 'receipt.json')
    with TaskFiles() as task:
        with task.parent(path) as (parent, name):
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent)
            with os.fdopen(descriptor, 'wb') as stream:
                info = os.fstat(stream.fileno())
                identity = [info.st_dev, info.st_ino]
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        fingerprint = task.inspect(path, contents=True)
        if (fingerprint['identity'] != identity or fingerprint['links'] != 1
                or fingerprint['sha256'] != hashlib.sha256(raw).hexdigest()):
            raise ValueError('Receipt changed during exclusive publication; no ownership proof recorded')
        runner['receipt_fingerprint'] = fingerprint
        task.save(manifest, data)


def worker_main(manifest):
    try:
        data = _load(manifest)
        if data['runner']['schema'] != RUNNER_SCHEMA:
            raise ValueError('Legacy v1 tasks can only be resumed; worker generation is unsupported')
        _check_created(data)
        runner = data['runner']
        with TaskFiles() as task:
            config = validate_config(task.load(helper_path(runner, 'spec.json')), runner,
                                     [p['path'] for p in data['protected']])
        from create_docx import create_document_delivery, _validate_spec, validate_task_resources
        try:
            validate_task_resources(config['spec'])
            _validate_spec(config['spec'], Path.cwd())
        except (ValueError, TypeError) as exc:
            def has_resource(value):
                if isinstance(value, dict):
                    return 'path' in value or any(has_resource(v) for v in value.values())
                return isinstance(value, list) and any(has_resource(v) for v in value)
            deterministic = not has_resource(config['spec'])
            print(json.dumps(failure('spec_validation_failed' if deterministic else 'resource_validation_failed', exc,
                                     action='correct_spec' if deterministic else 'inspect_inputs',
                                     spec_identity=spec_identity(config['spec']) if deterministic else None), ensure_ascii=True))
            return 1
        result = create_document_delivery(config['spec'], config['output'], verify=True,
                                          require_no_placeholders=True, protected_inputs=config['protected_inputs'])
        with TaskFiles() as task:
            fingerprint = task.inspect(config['output'], contents=True)
        _publish_receipt(manifest, data, {
            'schema': 'docx_auto_receipt_v1', 'manifest': manifest, 'created': runner['created'],
            'result': result, 'output_fingerprint': fingerprint})
        return 0
    except Exception as exc:
        print(json.dumps(failure('worker_failed', exc), ensure_ascii=True))
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    create = commands.add_parser('create')
    create.add_argument('output')
    create.add_argument('--protect-input', action='append', default=[])
    for name in ('retry', 'resume'):
        commands.add_parser(name).add_argument('manifest')
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            return 0 if check_task_runtime() else 1
        if args.command in ('create', 'retry'):
            raw = sys.stdin.buffer.read(MAX_TASK_BYTES + 1)
            if len(raw) > MAX_TASK_BYTES:
                raise ValueError('Automatic task JSON exceeds 2 MiB')
            spec = json.loads(raw, object_pairs_hook=_unique_object)
            result = create_task(spec, args.output, protected_inputs=args.protect_input) if args.command == 'create' else retry_task(args.manifest, spec)
        else:
            result = resume_task(args.manifest)
        print(json.dumps(result, ensure_ascii=True))
        return 1 if result['status'] == 'failed' else 3 if result.get('task_status') == 'cleanup_incomplete' else 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps(failure('request_rejected', exc, action='inspect_request', task_status='not_started'), ensure_ascii=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
