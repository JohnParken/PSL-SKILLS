"""Run allowlisted XLSX file-producing CLIs with pre-registered stdin JSON configs."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _artifact_runtime.controlled_cleanup import TaskFiles, plan_cleanup, seal_cleanup, cleanup_task, finalize_failed_cleanup, _unique_object
from _artifact_runtime.archive_deliverables import validate_output_path, choose_delivery_output

SCRIPTS = {'stdlib_xlsx.py', 'xlsx_tabular.py', 'xlsx_result_export.py', 'xlsx_management.py',
           'xlsx_dashboard.py', 'xlsx_deposits.py', 'xlsx_deposit_structure.py',
           'xlsx_customers.py', 'xlsx_reconcile.py', 'xlsx_management_policy.py'}
MAX_SPEC_BYTES = 2 * 1024 * 1024


def _stream_evidence(value, *, structured=False):
    """Do not echo arbitrary child diagnostics, which may contain task data or secrets."""
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    value = value or ''
    return {'text': '' if not value else None, 'original_characters': len(value),
            'omitted': bool(value), 'truncated': False,
            'reason': 'structured_result_returned' if structured else 'sensitive_child_output'}


def _run_child(script, arguments):
    """Preserve process outcome and explicit stream evidence without raw log leakage."""
    invocation = uuid.uuid4().hex
    try:
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name(script)), *arguments],
                               capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        return 1, {'status': 'failed', 'task_status': 'recovery_pending',
                   'error_code': 'child_timeout', 'child_execution': {
                       'invocation_id': invocation, 'state': 'timeout', 'exit_code': None,
                       'stdout': _stream_evidence(exc.stdout), 'stderr': _stream_evidence(exc.stderr)}}
    except OSError:
        return 1, {'status': 'failed', 'task_status': 'recovery_pending',
                   'error_code': 'child_not_started', 'child_execution': {
                       'invocation_id': invocation, 'state': 'not_started', 'exit_code': None,
                       'stdout': _stream_evidence(None), 'stderr': _stream_evidence(None)}}
    valid = False
    try:
        result = json.loads(child.stdout)
        valid = isinstance(result, dict)
    except (ValueError, RecursionError):
        result = None
    if not valid:
        result = {'status': 'failed', 'task_status': 'recovery_pending',
                  'error_code': 'child_argument_error' if child.returncode == 2 and 'usage:' in child.stderr
                  else 'child_invalid_result'}
    result['child_execution'] = {
        'invocation_id': invocation, 'state': 'signaled' if child.returncode < 0 else 'exited',
        'exit_code': child.returncode if child.returncode >= 0 else None,
        'signal': -child.returncode if child.returncode < 0 else None,
        'stdout': _stream_evidence(child.stdout, structured=valid),
        'stderr': _stream_evidence(child.stderr)}
    return (child.returncode if child.returncode > 0 else 0 if valid and child.returncode == 0 else 1), result


def output_argument_index(script, arguments):
    if script == 'stdlib_xlsx.py':
        positions = {'create': 2, 'edit': 3, 'calculate': 2}
        if not arguments or arguments[0] not in positions:
            raise ValueError('Only create/edit/calculate are task writes')
        return positions[arguments[0]]
    if script == 'xlsx_tabular.py':
        if not arguments or arguments[0] not in ('import', 'export'):
            raise ValueError('Only import/export are task writes')
        return 2
    if script == 'xlsx_result_export.py':
        return 1
    if arguments.count('--xlsx') != 1:
        raise ValueError('Report task requires exactly one --xlsx output')
    return arguments.index('--xlsx') + 1


def final_argument(script, arguments):
    return arguments[output_argument_index(script, arguments)]


def _run_task(specification):
    if (not isinstance(specification, dict) or set(specification) - {'script', 'arguments', 'configs', 'output', 'inputs'}
            or not {'script', 'arguments', 'output'} <= set(specification)):
        raise ValueError('Expected script, arguments, output and optional configs/inputs')
    script, arguments = specification['script'], specification['arguments']
    configs, inputs = specification.get('configs', {}), specification.get('inputs', [])
    if (not isinstance(script, str) or script not in SCRIPTS or not isinstance(arguments, list)
            or not all(isinstance(x, str) and '\0' not in x for x in arguments)
            or not isinstance(configs, dict) or len(configs) > 16
            or not all(isinstance(k, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', k) for k in configs)
            or not isinstance(inputs, list) or not all(isinstance(x, str) for x in inputs)):
        raise ValueError('Invalid task script, arguments, config names or inputs')
    output = validate_output_path(Path(specification['output']))
    try:
        if final_argument(script, arguments) != str(output):
            raise ValueError('Declared output must match the command output exactly')
    except IndexError as exc:
        raise ValueError('Missing command arguments') from exc
    # Existing CLI arguments are inputs, never inferred disposable files.
    protected = list(inputs)
    output_index = output_argument_index(script, arguments)
    for index, item in enumerate(arguments):
        if index == output_index:
            continue
        if not item.startswith(('-', '@')) and Path(item).is_file():
            validate_output_path(Path(item))
            protected.append(item)
    for item in protected:
        validate_output_path(Path(item))
    output = choose_delivery_output(output, protected_inputs=protected)
    arguments = list(arguments)
    arguments[output_index] = output.as_posix()
    workspace = 'xlsx-task-' + uuid.uuid4().hex
    manifest = workspace + '.cleanup.json'
    paths = {key: workspace + '.' + key + '.json' for key in configs}
    resolved = []
    for item in arguments:
        if item.startswith('@'):
            if item[1:] not in paths:
                raise ValueError('Unknown config reference: ' + item)
            resolved.append(paths[item[1:]])
        else:
            resolved.append(item)
    if set(paths) - {item[1:] for item in arguments if item.startswith('@')}:
        raise ValueError('Unused task config')
    if not paths:
        code, result = _run_child(script, resolved)
        result.setdefault('task_status', 'no_intermediates')
        result['output_may_exist'] = output.exists()
        return code, result
    plan_cleanup(manifest, files=list(paths.values()), directories=(),
                 protected=list(dict.fromkeys(protected)), deliverables=[output.as_posix()])
    result = None
    primary_complete = False
    try:
        with TaskFiles() as task:
            for key, path in paths.items():
                payload = json.dumps(configs[key], ensure_ascii=False, allow_nan=False).encode('utf-8')
                with task.parent(path) as (parent, name):
                    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                    with os.fdopen(descriptor, 'wb') as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
        code, result = _run_child(script, resolved)
        if result.get('task_status') == 'recovery_pending':
            result.update(manifest=manifest, output_may_exist=output.exists())
            return code, result
        delivery = result.get('delivery')
        if code == 0 and output.is_file() and isinstance(delivery, dict):
            primary_complete = True
            with TaskFiles() as task:
                expected = task.inspect(output.as_posix(), contents=True)
                if delivery.get('temporary_residuals'):
                    raise ValueError('Archive temporary cleanup is incomplete; inspect exact residual paths')
                seal_cleanup(manifest, outcome='success', verified=True, archive_status=delivery['archive_status'])
                cleanup = cleanup_task(manifest)
                if task.inspect(output.as_posix(), contents=True) != expected:
                    primary_complete = False
                    raise ValueError('Final output changed during cleanup; inspect before delivery')
                result.update(task_status='completed' if cleanup['cleanup_complete'] else 'cleanup_incomplete',
                              manifest=manifest, cleanup=cleanup)
                if cleanup['cleanup_complete']:
                    fingerprint = task.inspect(manifest, contents=True)
                    if fingerprint['links'] != 1:
                        raise ValueError('Manifest is not a single owned file')
                    with task.parent(manifest) as (parent, name):
                        if task.inspect(manifest, contents=True) != fingerprint:
                            raise ValueError('Manifest changed before retirement')
                        os.unlink(name, dir_fd=parent)
                    cleanup.update(manifest_retained=None, manifest_removed=True)
                    cleanup['deleted'].append({'path':manifest,'reason':'removed'})
                    result['manifest'] = None
                return (0 if cleanup['cleanup_complete'] else 3), result
        seal_cleanup(manifest, outcome='retry')
        result.update(task_status='retained_for_review', manifest=manifest,
                      next_action='inspect_original_task',
                      cleanup=cleanup_task(manifest))
        return code or 1, result
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        if primary_complete:
            result.update(task_status='cleanup_incomplete', manifest=manifest,
                          cleanup={'cleanup_complete': False, 'status': 'failed', 'message': str(exc)})
            return 3, result
        # Do not regenerate or infer success after interruption. Retain helpers for review.
        return 1, {'status': 'failed', 'task_status': 'recovery_pending', 'manifest': manifest,
                   'message': str(exc), 'output_may_exist': output.exists()}


def run_task(spec):
    code, result = _run_task(spec)
    manifest = result.get('manifest')
    delivery = result.get('delivery')
    if isinstance(delivery, dict) and delivery.get('temporary_residuals'):
        # A failed archive unlink is still unfinished even for partial results
        # or direct commands without runner-owned JSON configuration files.
        result.update(task_status='cleanup_incomplete', cleanup={
            'cleanup_complete': False, 'status': 'failed',
            'message': 'Archive temporary cleanup is incomplete',
            'temporary_residuals': delivery['temporary_residuals']})
        return code or 3, result
    if code and manifest and result.get('task_status') != 'cleanup_incomplete':
        try:
            report = finalize_failed_cleanup(manifest)
            result.update(cleanup=report, manifest=None if report.get('manifest_removed') else manifest,
                          task_status='failed_cleaned' if report['cleanup_complete'] else 'cleanup_incomplete',
                          next_action='correct_request_and_start_new_task')
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            result.update(task_status='cleanup_incomplete', cleanup={
                'cleanup_complete': False, 'status': 'failed', 'message': str(exc)})
    return code, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_SPEC_BYTES + 1)
        if len(raw) > MAX_SPEC_BYTES:
            raise ValueError('Task specification exceeds 2 MiB')
        spec = json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonstandard JSON constant')))
        code, result = run_task(spec)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, RecursionError, subprocess.TimeoutExpired) as exc:
        code, result = 1, {'status': 'failed', 'message': str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
