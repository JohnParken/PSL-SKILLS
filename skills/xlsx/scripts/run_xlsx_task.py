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
from _artifact_runtime.controlled_cleanup import TaskFiles, plan_cleanup, seal_cleanup, cleanup_task, _unique_object
from _artifact_runtime.archive_deliverables import validate_output_path

SCRIPTS = {'stdlib_xlsx.py', 'xlsx_tabular.py', 'xlsx_result_export.py', 'xlsx_management.py',
           'xlsx_dashboard.py', 'xlsx_deposits.py', 'xlsx_deposit_structure.py',
           'xlsx_customers.py', 'xlsx_reconcile.py', 'xlsx_management_policy.py'}
MAX_SPEC_BYTES = 2 * 1024 * 1024


def final_argument(script, arguments):
    if script == 'stdlib_xlsx.py':
        positions = {'create': 2, 'edit': 3, 'calculate': 2}
        if not arguments or arguments[0] not in positions:
            raise ValueError('Only create/edit/calculate are task writes')
        return arguments[positions[arguments[0]]]
    if script == 'xlsx_tabular.py':
        if not arguments or arguments[0] not in ('import', 'export'):
            raise ValueError('Only import/export are task writes')
        return arguments[2]
    if script == 'xlsx_result_export.py':
        return arguments[1]
    if arguments.count('--xlsx') != 1:
        raise ValueError('Report task requires exactly one --xlsx output')
    return arguments[arguments.index('--xlsx') + 1]


def run_task(specification):
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
    if output.exists():
        raise ValueError('Output already exists; task will not adopt it')
    try:
        if final_argument(script, arguments) != str(output):
            raise ValueError('Declared output must match the command output exactly')
    except IndexError as exc:
        raise ValueError('Missing command arguments') from exc
    # Existing CLI arguments are inputs, never inferred disposable files.
    protected = list(inputs)
    for item in arguments:
        if not item.startswith(('-', '@')) and Path(item).is_file():
            validate_output_path(Path(item))
            protected.append(item)
    for item in protected:
        validate_output_path(Path(item))
    workspace = 'xlsx-task-' + uuid.uuid4().hex
    manifest = workspace + '.cleanup.json'
    paths = {key: workspace + '/' + key + '.json' for key in configs}
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
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name(script)), *resolved],
                               capture_output=True, text=True, timeout=120)
        result = json.loads(child.stdout)
        if not isinstance(result, dict):
            raise ValueError('Child result must be JSON object')
        result['task_status'] = 'no_intermediates'
        return child.returncode, result
    plan_cleanup(manifest, files=list(paths.values()), directories=[workspace] if paths else [],
                 protected=list(dict.fromkeys(protected)), deliverables=[output.as_posix()])
    result = None
    primary_complete = False
    try:
        if paths:
            os.mkdir(workspace, 0o700)
        with TaskFiles() as task:
            for key, path in paths.items():
                payload = json.dumps(configs[key], ensure_ascii=False, allow_nan=False).encode('utf-8')
                with task.parent(path) as (parent, name):
                    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                    with os.fdopen(descriptor, 'wb') as handle:
                        handle.write(payload)
        child = subprocess.run([sys.executable, '-I', '-S', '-B', str(Path(__file__).with_name(script)), *resolved],
                               capture_output=True, text=True, timeout=120)
        result = json.loads(child.stdout)
        if not isinstance(result, dict):
            raise ValueError('Child result must be JSON object')
        delivery = result.get('delivery')
        if child.returncode == 0 and output.is_file() and isinstance(delivery, dict):
            primary_complete = True
            seal_cleanup(manifest, outcome='success', verified=True, archive_status=delivery['archive_status'])
            cleanup = cleanup_task(manifest)
            result.update(task_status='completed' if cleanup['cleanup_complete'] else 'cleanup_incomplete',
                          manifest=manifest, cleanup=cleanup)
            return (0 if cleanup['cleanup_complete'] else 3), result
        seal_cleanup(manifest, outcome='retry')
        result.update(task_status='retained_for_review', manifest=manifest,
                      cleanup=cleanup_task(manifest))
        return child.returncode or 1, result
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        if primary_complete:
            result.update(task_status='cleanup_incomplete', manifest=manifest,
                          cleanup={'cleanup_complete': False, 'status': 'failed', 'message': str(exc)})
            return 3, result
        # Do not regenerate or infer success after interruption. Retain helpers for review.
        return 1, {'status': 'failed', 'task_status': 'recovery_pending', 'manifest': manifest,
                   'message': str(exc), 'output_may_exist': output.exists()}


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
