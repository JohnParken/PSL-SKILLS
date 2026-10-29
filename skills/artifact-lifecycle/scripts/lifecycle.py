"""Explicit artifact lifecycle CLI; format validation belongs to the calling skill."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from archive_deliverables import archive_outputs
from controlled_cleanup import TaskFiles
from cleanup_task import main as cleanup_main


def environment_check():
    if sys.version_info < (3, 10):
        raise RuntimeError('Python >=3.10 required')
    with TaskFiles():
        with tempfile.NamedTemporaryFile(mode='w+b', prefix='lifecycle-probe-', dir='.') as probe:
            probe.write(b'lifecycle-check')
            probe.flush()
            probe.seek(0)
            if probe.read() != b'lifecycle-check':
                raise RuntimeError('task probe roundtrip failed')
    return {'status': 'environment-ok', 'task_writable': True,
            'check_kind': 'environment', 'artifact_validation': 'not_run'}


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] in ('plan', 'seal', 'clean'):
        return cleanup_main(arguments)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    archive = commands.add_parser('archive')
    archive.add_argument('outputs', nargs='+', type=Path)
    archive.add_argument('--protect-input', action='append', default=[], type=Path)
    args = parser.parse_args(arguments)
    try:
        if args.command == 'check':
            result = environment_check()
            code = 0
        else:
            result = archive_outputs(args.outputs, args.protect_input)
            code = {'copied': 0, 'skipped': 3, 'failed': 1}.get(result['archive_status'], 1)
        print(json.dumps(result, ensure_ascii=True), flush=True)
        return code
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {'status': 'error', 'message': str(exc)}
        if args.command == 'check':
            result.update(check_kind='environment', artifact_validation='not_run')
        print(json.dumps(result, ensure_ascii=True), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
