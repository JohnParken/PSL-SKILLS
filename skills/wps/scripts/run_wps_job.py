"""Optional owned-JSON job runner; direct stdin CLIs need no extra manifest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _wps_artifact_runtime.archive_deliverables import validate_input_path, validate_output_path
from _wps_artifact_runtime.controlled_cleanup import TaskFiles, plan_cleanup, seal_cleanup, finalize_cleanup, finalize_failed_cleanup, _unique_object

SCRIPTS = ('run_wps_task.py', 'run_wps_edit.py', 'export_wps.py')
MAX_JOB_BYTES = 2 * 1024 * 1024


def check_environment():
    if sys.version_info < (3, 10):
        raise RuntimeError('Python >=3.10 required')
    import read_wps
    import wps_rtf_builder
    import docx_to_wps_rtf
    path = 'wps-lifecycle-probe-' + uuid.uuid4().hex + '.json'
    with TaskFiles() as task:
        with task.parent(path) as (parent, name):
            descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            owned = os.fstat(descriptor)
            try:
                with os.fdopen(descriptor, 'w+b') as stream:
                    stream.write(b'{}')
                    stream.flush()
                    stream.seek(0)
                    if stream.read() != b'{}':
                        raise ValueError('Lifecycle probe readback failed')
            finally:
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (owned.st_dev, owned.st_ino):
                    raise ValueError('Probe ownership changed; unknown entry retained')
                os.unlink(name, dir_fd=parent)
    return {'status': 'environment-ok', 'task_writable': True, 'check_kind': 'environment', 'artifact_validation': 'not_run'}


def _command(spec):
    if (not isinstance(spec, dict) or set(spec) - {'script', 'arguments', 'output', 'inputs', 'configs'}
            or not {'script', 'arguments', 'output'} <= set(spec)):
        raise ValueError('Expected script, arguments, output and optional inputs/configs')
    script, arguments = spec['script'], spec['arguments']
    if (script not in SCRIPTS or not isinstance(arguments, list) or not arguments
            or not all(isinstance(item, str) and '\0' not in item for item in arguments)):
        raise ValueError('Unsupported job script or arguments')
    if script == SCRIPTS[0] and arguments[0] == 'create' and len(arguments) >= 2:
        output_index, input_index = 1, None
    elif script == SCRIPTS[1] and arguments[0] in ('text', 'structure', 'table', 'batch') and len(arguments) >= 3:
        output_index, input_index = 2, 1
    elif script == SCRIPTS[2] and arguments[0] == 'convert' and len(arguments) >= 4:
        output_aliases = [item for item in arguments if item.startswith('--')
                          and '--output'.startswith(item.split('=', 1)[0]) and item != '--output']
        if arguments.count('--output') != 1 or output_aliases:
            raise ValueError('Export jobs require exactly one unabbreviated --output argument')
        output_index, input_index = arguments.index('--output') + 1, 1
    else:
        raise ValueError('Only create, edit and explicit-output conversion jobs are supported')
    if output_index >= len(arguments) or Path(arguments[output_index]) != Path(spec['output']):
        raise ValueError('Declared output must match the actual CLI output')
    inputs = spec.get('inputs', [])
    if not isinstance(inputs, list) or not all(isinstance(item, str) for item in inputs):
        raise ValueError('inputs must be explicit task-relative filenames')
    protected = list(inputs)
    if input_index is not None:
        protected.append(arguments[input_index])
    if '--spec' in arguments:
        index = arguments.index('--spec') + 1
        if index < len(arguments) and not arguments[index].startswith(('@', '{', '[')):
            protected.append(arguments[index])
    protected = list(dict.fromkeys(protected))
    for item in protected:
        validate_input_path(Path(item))
    output = validate_output_path(Path(spec['output']), protected_inputs=protected)
    if output.exists():
        raise ValueError('Output already exists; choose a new filename')
    configs = spec.get('configs', {})
    if (not isinstance(configs, dict) or len(configs) > 8
            or not all(isinstance(key, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', key) for key in configs)):
        raise ValueError('Invalid owned config names')
    references = {item[1:] for item in arguments if item.startswith('@')}
    if references != set(configs):
        raise ValueError('Config references must exactly match explicitly supplied configs')
    return script, arguments, output, protected, configs


def run_job(spec):
    script, arguments, output, protected, configs = _command(spec)
    prefix = 'wps-job-' + uuid.uuid4().hex
    manifest = prefix + '.cleanup.json' if configs else None
    paths = {key: prefix + '.' + key + '.json' for key in configs}
    arguments = [paths[item[1:]] if item.startswith('@') else item for item in arguments]
    planned = False
    result = None
    code = 1
    stage = 'configs'
    owned = {}
    try:
        if manifest:
            plan_cleanup(manifest, files=list(paths.values()), protected=protected, deliverables=[output.as_posix()])
            planned = True
            with TaskFiles() as task:
                for key, path in paths.items():
                    data = json.dumps(configs[key], ensure_ascii=False, allow_nan=False).encode('utf-8')
                    with task.parent(path) as (parent, name):
                        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                        info = os.fstat(descriptor)
                        owned[path] = {'identity': [info.st_dev, info.st_ino]}
                        with os.fdopen(descriptor, 'wb') as stream:
                            stream.write(data)
                            stream.flush()
                            os.fsync(stream.fileno())
                            info = os.fstat(stream.fileno())
                            owned[path].update(size=info.st_size, mtime_ns=info.st_mtime_ns, sha256=hashlib.sha256(data).hexdigest())
                for path, expected in owned.items():
                    current = task.inspect(path, contents=True)
                    if any(current.get(key) != value for key, value in expected.items()) or current.get('links') != 1:
                        raise ValueError('Owned configuration changed before worker launch')
            # Persist the creation fingerprints before the producer starts.
            seal_cleanup(manifest, outcome='retry', retain={path: 'producer_not_confirmed_stopped' for path in paths.values()})
        stage = 'child'
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name(script)), *arguments],
                               capture_output=True, text=True, encoding='utf-8', timeout=120, check=False)
        code = child.returncode
        try:
            result = json.loads(child.stdout or child.stderr)
            if not isinstance(result, dict):
                raise ValueError('Child result must be an object')
        except (ValueError, json.JSONDecodeError):
            result = {'status': 'failed', 'error': 'Child returned no complete JSON result; inspect before retry'}
            code = 1
        result['child_execution'] = {'exit_code': child.returncode, 'state': 'exited' if child.returncode >= 0 else 'signaled',
                                     'stdout_characters': len(child.stdout), 'stderr_characters': len(child.stderr),
                                     'raw_streams_omitted': True}
        if child.returncode < 0:
            result.update(job_status='recovery_pending', task_manifest=manifest,
                          job_cleanup={'cleanup_complete': False, 'reason': 'Confirm producer termination before original-manifest cleanup'})
            return 1, result
    except subprocess.TimeoutExpired as exc:
        return 1, {'status': 'failed', 'job_status': 'recovery_pending', 'task_manifest': manifest,
                   'child_execution': {'state': 'timeout', 'exit_code': None, 'raw_streams_omitted': True,
                                       'stdout_bytes': len(exc.stdout) if isinstance(exc.stdout, bytes) else None,
                                       'stderr_bytes': len(exc.stderr) if isinstance(exc.stderr, bytes) else None},
                   'job_cleanup': {'cleanup_complete': False, 'reason': 'Inspect output and confirm producer termination'}}
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        result = {'status': 'failed', 'error': str(exc),
                  'child_execution': {'state': 'not_started' if stage == 'child' else 'not_attempted', 'exit_code': None}}
        code = 1
    if not planned:
        result.update(job_status='finished', job_cleanup={'cleanup_complete': True, 'cleanup_scope': 'no_job_intermediates'})
        if result.get('delivery', {}).get('temporary_residuals'):
            result['job_status'] = 'cleanup_incomplete'
            code = 1
        return code, result
    try:
        delivery = result.get('delivery', {})
        retain = {}
        with TaskFiles() as task:
            for path in paths.values():
                current = task.optional(path, contents=True)
                expected = owned.get(path)
                if current is not None and (not expected or 'sha256' not in expected
                                            or any(current.get(key) != value for key, value in expected.items()) or current.get('links') != 1):
                    retain[path] = 'creation_ownership_or_fingerprint_unconfirmed'
        if retain:
            seal_cleanup(manifest, outcome='failed', retain=retain)
            cleanup = finalize_cleanup(manifest)
        elif (code in (0, 3) and result.get('status') in ('success', 'partial')
                and result.get('publication_verification') == 'identity_and_bytes_verified'
                and delivery.get('archive_status') in ('copied', 'failed', 'skipped') and not delivery.get('temporary_residuals')):
            seal_cleanup(manifest, outcome='success', verified=True, archive_status=delivery['archive_status'])
            cleanup = finalize_cleanup(manifest)
        else:
            cleanup = finalize_failed_cleanup(manifest)
        result.update(job_cleanup=cleanup, task_manifest=None if cleanup.get('manifest_removed') else manifest,
                      job_status='finished' if cleanup['cleanup_complete'] else 'cleanup_incomplete')
        if not cleanup['cleanup_complete']:
            code = 1
        if delivery.get('temporary_residuals'):
            result['job_status'] = 'cleanup_incomplete'
            code = 1
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result.update(job_status='cleanup_incomplete', task_manifest=manifest,
                      job_cleanup={'cleanup_complete': False, 'error': str(exc)})
        code = 1
    return code, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        if args.check:
            print(json.dumps(check_environment()), flush=True)
            return 0
        raw = sys.stdin.buffer.read(MAX_JOB_BYTES + 1)
        if len(raw) > MAX_JOB_BYTES:
            raise ValueError('Job exceeds 2 MiB')
        spec = json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonstandard JSON number')))
        code, result = run_job(spec)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        code, result = 1, {'status': 'failed', 'error': str(exc)}
        if args.check:
            result.update(check_kind='environment', artifact_validation='not_run')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
