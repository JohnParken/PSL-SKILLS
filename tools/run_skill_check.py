"""Repository integration adapter for known checks, not a general command executor.

Invoke from the harness-confirmed task directory. Emit one JSON envelope, keeping
the child stdout/stderr separate even on nonzero exits. This cannot install or
change an external execute tool or prove that a remote model received its result.
"""
import argparse
from contextlib import redirect_stderr
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_LIMIT = 65536
SKILLS = ('artifact-lifecycle', 'bank-writing', 'docx', 'ofd',
          'skill-development-standards', 'wps', 'xlsx')
CHECKS = {
    'docx-read': ('docx/scripts/read_docx.py', ['--check'], 'read-check-ok'),
    'docx-task': ('docx/scripts/run_docx_task.py', ['check'], 'check-ok'),
    'docx-create': ('docx/scripts/create_docx.py', ['--check'], 'check-ok'),
    'docx-archive': ('docx/scripts/archive_deliverables.py', ['--check'], 'check-ok'),
    'wps': ('wps/scripts/read_wps.py', ['--check'], 'environment-ok'),
    'ofd': ('ofd/scripts/read_ofd.py', ['--check'],
            'environment-ok Python>=3.10 Expat>=2.6.0 stdlib-only'),
    'xlsx': ('xlsx/scripts/stdlib_xlsx.py', ['check'], 'ready'),
    'artifact-lifecycle': ('artifact-lifecycle/scripts/lifecycle.py', ['check'], 'environment-ok'),
}
JSON_CHECKS = frozenset(('xlsx', 'artifact-lifecycle'))


def envelope(check_id, check_kind='environment'):
    return {'schema_version': 'skill_check_execution_v1', 'invocation_id': uuid.uuid4().hex,
            'check_id': check_id, 'check_kind': check_kind, 'artifact_validation': 'not_run',
            'execution_status': 'not_started', 'check_status': 'not_run',
            'argv': [], 'exit_code': None, 'returncode': None,
            'stdout': '', 'stderr': '', 'stdout_bytes': 0, 'stderr_bytes': 0,
            'stdout_truncated': False, 'stderr_truncated': False,
            'stdout_decode_errors': False, 'stderr_decode_errors': False}


def capture_channels(report, stdout, stderr):
    for channel, raw in (('stdout', stdout), ('stderr', stderr)):
        raw = raw or b''
        report[channel + '_bytes'] = len(raw)
        report[channel + '_truncated'] = len(raw) > OUTPUT_LIMIT
        visible = raw[:OUTPUT_LIMIT]
        try:
            report[channel] = visible.decode('utf-8')
        except UnicodeDecodeError:
            report[channel] = visible.decode('utf-8', errors='replace')
            report[channel + '_decode_errors'] = True


def check_passed(check_id, report, marker):
    """Exit 0 alone never establishes that an environment check actually ran."""
    if any(report[key] for key in ('stdout_truncated', 'stderr_truncated',
                                  'stdout_decode_errors', 'stderr_decode_errors')):
        return False
    if check_id == 'skill-structure':
        return report['stdout'].strip() == 'OK ' + report['argv'][-2]
    if check_id in JSON_CHECKS:
        try:
            data = json.loads(report['stdout'])
        except (ValueError, TypeError):
            return False
        return (isinstance(data, dict) and data.get('status') == marker
                and data.get('check_kind') == 'environment'
                and data.get('artifact_validation') == 'not_run')
    lines = report['stdout'].splitlines()
    return marker in lines and 'check_kind=environment artifact_validation=not_run' in lines


def run_check(check_id, *, task_dir='.', skill=None, timeout=30):
    report = envelope(check_id, 'skill_structure' if check_id == 'skill-structure' else 'environment')
    try:
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError('timeout must be greater than 0 and at most 60 seconds')
        directory = Path(task_dir)
        if directory.is_absolute() or '..' in directory.parts:
            raise ValueError('task-dir must stay relative to the harness-confirmed working directory')
        directory = directory.resolve(strict=True)
        directory.relative_to(Path.cwd().resolve())
        if not directory.is_dir():
            raise ValueError('task-dir must be a directory')
        if check_id == 'skill-structure':
            if skill not in SKILLS:
                raise ValueError('skill-structure requires a known --skill')
            script = ROOT / 'skills/skill-development-standards/scripts/check_skill.py'
            args, marker = [str(ROOT / 'skills' / skill), '--strict'], None
        elif check_id in CHECKS:
            if skill is not None:
                raise ValueError('--skill is only valid for skill-structure')
            relative, args, marker = CHECKS[check_id]
            script = ROOT / 'skills' / relative
        else:
            raise ValueError('Unknown check; bank-writing has manual prerequisites, not a script')
        report['argv'] = [sys.executable, '-I', '-S', '-B', str(script), *args]
        report['task_dir'] = str(directory)
        if not script.is_file():
            raise ValueError('Check entry is missing')
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', DOCX_DEBUG_ROUTER_CONFIG='0')
        process = subprocess.run(report['argv'], cwd=directory, env=environment,
                                 stdin=subprocess.DEVNULL, capture_output=True,
                                 timeout=timeout, check=False)
        capture_channels(report, process.stdout, process.stderr)
        report['returncode'] = process.returncode
        if process.returncode < 0:
            report.update(execution_status='signaled', check_status='unconfirmed',
                          signal=-process.returncode)
        else:
            report.update(execution_status='completed', exit_code=process.returncode,
                          check_status='failed' if process.returncode else
                          'passed' if check_passed(check_id, report, marker) else 'unconfirmed')
    except subprocess.TimeoutExpired as exc:
        capture_channels(report, exc.stdout, exc.stderr)
        report.update(execution_status='timed_out', check_status='unconfirmed',
                      message='Child timed out; it was killed and waited for; no normal exit code available')
    except (OSError, ValueError, TypeError) as exc:
        report['message'] = str(exc)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('check_id', choices=(*CHECKS, 'skill-structure'))
    parser.add_argument('--task-dir', required=True, help='confirmed relative task directory')
    parser.add_argument('--skill', choices=SKILLS)
    parser.add_argument('--timeout', type=float, default=30)
    diagnostics = io.StringIO()
    try:
        with redirect_stderr(diagnostics):
            args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:  # --help is documentation, not a check result.
            return 0
        report = envelope(None)
        report['message'] = diagnostics.getvalue()
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 2
    report = run_check(args.check_id, task_dir=args.task_dir, skill=args.skill, timeout=args.timeout)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    # The envelope's exit_code is the child's; this adapter has its own status.
    return 0 if report['check_status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
