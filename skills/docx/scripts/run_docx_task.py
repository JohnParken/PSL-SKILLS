"""Own generation helpers from creation through child exit and verified cleanup."""
import argparse
import hashlib
from contextlib import contextmanager, redirect_stdout
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

from cleanup_task import TaskFiles, plan_cleanup, seal_cleanup, cleanup_task, _unique_object
from create_docx import _delivery_output_path, _check_cli_runtime
from archive_deliverables import validate_output_path
from office.runtime import check_runtime
from docx_task_state import MAX_ATTEMPTS, validate_manifest, validate_receipt, validate_config

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


def check_task_runtime():
    try:
        _runner_environment()
        with TaskFiles() as task:
            fcntl.flock(task.root, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(task.root, fcntl.LOCK_UN)
        with tempfile.TemporaryFile(dir='.') as probe:
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
    except (OSError, ValueError, RuntimeError) as exc:
        print('auto-cleanup-check-failed: ' + str(exc), file=sys.stderr)
        return False
    print('auto-cleanup=available')
    return _check_cli_runtime()


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
        if task.inspect(runner['workspace']) != runner.get('workspace_identity'):
            raise ValueError('Workspace identity changed; helpers retained')
        created = runner.get('created', {})
        names = [runner['workspace'] + '/' + n for n in ('worker.py', 'spec.json', 'task.lock')]
        if set(created) != set(names):
            raise ValueError('Helper creation was interrupted; ownership unconfirmed')
        for path, expected in created.items():
            if pending and path == runner['workspace'] + '/spec.json':
                continue
            current = task.inspect(path, contents=True)
            if current != expected or current.get('links') != 1:
                raise ValueError('Created helper changed or became a hard link; retained: ' + path)


@contextmanager
def _locked(data):
    _check_created(data, pending=data['runner'].get('spec_update') is not None)
    with TaskFiles() as task, task.parent(data['runner']['workspace'] + '/task.lock') as (parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            expected = data['runner']['created'][data['runner']['workspace'] + '/task.lock']
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
    path = runner['workspace'] + '/spec.json'
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
                os.replace(source, 'spec.json', src_dir_fd=parent, dst_dir_fd=parent)
        elif current != update['next'] or staged is not None:
            raise ValueError('Pending specification differs from recorded old/new identities; retained')
        runner['created'][path] = update['next']
        runner.pop('spec_update')
        task.save(manifest, data)
    _check_created(data)


def _update_spec(manifest, data, spec):
    runner = data['runner']
    path = runner['workspace'] + '/spec.json'
    stage = runner['workspace'] + '/retry-spec.json'
    with TaskFiles() as task:
        config = validate_config(task.load(path), runner, [p['path'] for p in data['protected']])
        config['spec'] = spec
        raw = (json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        if len(raw) > MAX_TASK_BYTES:
            raise ValueError('Corrected specification exceeds task budget')
        if task.optional(stage) is not None:
            raise ValueError('Retry staging file already exists; not adopted')
        if runner.get('retry_stage') is None:
            # Older manifests gain only a newly checked absent path, never an existing file.
            data['entries'].append({'path': stage, 'kind': 'file', 'absent_at_plan': True})
            runner['retry_stage'] = stage
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
    return {'status': 'failed', 'task_status': state, 'message': message,
            'task_manifest': manifest, 'cleanup': {'status': 'retained', 'cleanup_complete': False}}


def _finish_locked(manifest, data):
    runner = data['runner']
    _check_created(data)
    receipt_path = runner['workspace'] + '/receipt.json'
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
    # Save the delivery separately before deletion, so a controller can resume after any deletion.
    runner.update(state='delivered', delivery=result)
    _save(manifest, data)
    seal_cleanup(manifest, outcome='success', verified=True, archive_status=result['archive_status'])
    report = cleanup_task(manifest)
    return {**result, 'task_manifest': manifest, 'task_status': 'completed' if report['cleanup_complete'] else 'cleanup_incomplete',
            'cleanup': report}


def resume_task(manifest):
    """Recover an existing owned task; never rerun generation or adopt unknown files."""
    manifest = Path(manifest).as_posix()
    try:
        with _controlled(manifest) as (data, control_fd):
            if data['phase'] == 'sealed':
                result = data['runner']['delivery']
                report = cleanup_task(manifest)
                return {**result, 'task_manifest': manifest,
                        'task_status': 'completed' if report['cleanup_complete'] else 'cleanup_incomplete', 'cleanup': report}
            with _locked(data):
                _recover_spec(manifest, data)
                return _finish_locked(manifest, data)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        return _retained(manifest, str(exc), state='recovery_pending')


def _execute(manifest, data, lock_fd):
    runner = data['runner']
    _check_created(data)
    if Path(runner['output']).exists() or Path(runner['output']).is_symlink():
        return _retained(manifest, 'Output already exists without a completed receipt; do not regenerate', state='recovery_pending')
    runner['attempts'] += 1
    runner['state'] = 'running'
    _save(manifest, data)
    command = [sys.executable, '-I', '-S', '-B', runner['workspace'] + '/worker.py',
               str(Path(__file__).resolve().parent), manifest]
    try:
        process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 pass_fds=lock_fd if isinstance(lock_fd, tuple) else (lock_fd,), timeout=120, check=False)
    except subprocess.TimeoutExpired:
        runner['state'] = 'recovery_pending' if os.path.lexists(runner['output']) else 'retry_pending'
        _save(manifest, data)
        return _retained(manifest, 'Generation timed out; worker terminated and waited for; helpers retained', state=runner['state'])
    if process.stderr:
        sys.stderr.buffer.write(process.stderr) if hasattr(sys.stderr, 'buffer') else sys.stderr.write(process.stderr.decode('utf-8', 'replace'))
    if process.returncode:
        runner['state'] = 'recovery_pending' if os.path.lexists(runner['output']) else 'retry_pending'
        _save(manifest, data)
        return _retained(manifest, process.stdout.decode('utf-8', 'replace').strip() or 'Generation worker failed', state=runner['state'])
    return _finish_locked(manifest, data)


def create_task(spec, output, *, protected_inputs=(), workspace=None):
    """Create both helper files exclusively, run fixed code, then finish outside its process."""
    with redirect_stdout(sys.stderr):
        if not check_runtime():
            return _retained(None, 'Runtime check failed', state='environment_failed')
    _runner_environment()
    output = _delivery_output_path(Path.cwd(), str(output)).as_posix()
    protected = [Path(p).as_posix() for p in protected_inputs]
    validate_output_path(Path(output), protected_inputs=protected)
    if os.path.lexists(output):
        raise ValueError('Automatic task refuses an existing final filename; choose an authorized new name')
    workspace = workspace or 'docx-task-' + uuid.uuid4().hex
    if not isinstance(workspace, str) or '/' in workspace or '\\' in workspace or workspace in ('', '.', '..'):
        raise ValueError('Workspace must be a new single task-relative directory name')
    manifest = workspace + '.cleanup.json'
    config = {'spec': spec, 'output': output, 'protected_inputs': protected}
    raw = (json.dumps(config, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    if len(raw) > MAX_TASK_BYTES:
        raise ValueError('Automatic task JSON exceeds 2 MiB')
    files = [workspace + '/' + n for n in ('worker.py', 'spec.json', 'receipt.json', 'task.lock', 'retry-spec.json')]
    plan_cleanup(manifest, files=files, directories=[workspace], protected=protected, deliverables=[output])
    data = None
    try:
        with TaskFiles() as task:
            data = task.load(manifest)
            data['runner'] = {'schema': 'docx_auto_task_v1', 'workspace': workspace, 'output': output,
                              'created': {}, 'attempts': 0, 'state': 'initializing',
                              'retry_stage': workspace + '/retry-spec.json'}
            task.save(manifest, data)
            with task.parent(workspace) as (parent, name):
                os.mkdir(name, 0o700, dir_fd=parent)
            data['runner']['workspace_identity'] = task.inspect(workspace)
            for name, content in (('worker.py', WORKER_SOURCE.encode()), ('spec.json', raw), ('task.lock', b'')):
                path = workspace + '/' + name
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
                return _execute(manifest, data, (control_fd, descriptor))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        return _retained(manifest, str(exc), state='recovery_pending')


def retry_task(manifest, spec):
    """Correct only this task's owned JSON, retaining the original plan and fixed worker."""
    manifest = Path(manifest).as_posix()
    try:
        with _controlled(manifest) as (data, control_fd):
            runner = data['runner']
            if data['phase'] != 'planned' or runner['attempts'] >= MAX_ATTEMPTS:
                raise ValueError('Task is sealed or its three generation attempts are exhausted')
            with _locked(data) as descriptor:
                _recover_spec(manifest, data)
                with TaskFiles() as task:
                    receipt = task.optional(runner['workspace'] + '/receipt.json')
                    if receipt is not None or os.path.lexists(runner['output']):
                        raise ValueError('Delivery may already exist; resume instead of regenerating')
                _update_spec(manifest, data, spec)
                return _execute(manifest, data, (control_fd, descriptor))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        return _retained(manifest, str(exc))


def worker_main(manifest):
    try:
        data = _load(manifest)
        _check_created(data)
        runner = data['runner']
        with TaskFiles() as task:
            config = validate_config(task.load(runner['workspace'] + '/spec.json'), runner,
                                     [p['path'] for p in data['protected']])
        from create_docx import create_document_delivery
        result = create_document_delivery(config['spec'], config['output'], verify=True,
                                          require_no_placeholders=True, protected_inputs=config['protected_inputs'])
        with TaskFiles() as task:
            fingerprint = task.inspect(config['output'], contents=True)
            task.save(runner['workspace'] + '/receipt.json', {
                'schema': 'docx_auto_receipt_v1', 'manifest': manifest, 'created': runner['created'],
                'result': result, 'output_fingerprint': fingerprint}, new=True)
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'message': str(exc)}, ensure_ascii=True))
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
        return 1 if result['status'] == 'failed' else 0 if result['cleanup']['cleanup_complete'] else 3
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'failed', 'task_status': 'not_started', 'message': str(exc)}, ensure_ascii=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
