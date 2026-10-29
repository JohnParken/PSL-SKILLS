"""One controlled entry for five existing edit APIs; never discover disposable files."""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cleanup_task import TaskFiles, plan_cleanup, seal_cleanup, cleanup_task, _unique_object
from run_docx_task import task_environment, _finish_delivery, MAX_TASK_BYTES
from archive_deliverables import validate_output_path
from docx_outcome import failure

OPERATIONS = ('text', 'structure', 'tables', 'image', 'comments')


def edit_task(kind, source, output, specification, *, replacement=None):
    environment = task_environment()
    if environment['status'] != 'ready':
        return failure('environment_failed', 'Required edit environment is unavailable', action='fix_environment', environment=environment)
    if kind not in OPERATIONS or (kind == 'comments' and isinstance(specification, dict) and specification.get('action') == 'list'):
        return failure('edit_request_rejected', 'Use existing read-only list entry for comments listing', action='inspect_request')
    source, output = Path(source), Path(output)
    protected = [source] + ([Path(replacement)] if replacement is not None else [])
    validate_output_path(source)
    for path in protected:
        validate_output_path(path)
        if not path.is_file():
            raise ValueError('Required input is not a regular file')
    validate_output_path(output, protected_inputs=protected)
    if output.exists():
        raise ValueError('Output already exists; choose a new authorized filename')
    raw = json.dumps(specification, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(raw) > MAX_TASK_BYTES:
        raise ValueError('Edit specification exceeds 2 MiB')
    prefix = 'docx-edit-' + uuid.uuid4().hex
    config, manifest = prefix + '.spec.json', prefix + '.cleanup.json'
    plan_cleanup(manifest, files=[config], protected=[p.as_posix() for p in protected], deliverables=[output.as_posix()])
    result = None
    try:
        with TaskFiles() as task, task.parent(config) as (parent, name):
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        # A stable root lock serializes this controller with create/retry/resume.
        from run_docx_task import fcntl
        with TaskFiles() as task:
            fcntl.flock(task.root, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                # Recheck after acquiring the lock, before any edit writes.
                if output.exists() or output.is_symlink():
                    raise ValueError('Output appeared while acquiring task lock')
                if kind == 'text':
                    from edit_docx import replace_text_delivery
                    result = replace_text_delivery(source, output, specification, protected_inputs=[config])
                elif kind == 'structure':
                    from edit_structure import edit_structure_delivery
                    result = edit_structure_delivery(source, output, specification, protected_inputs=[config])
                elif kind == 'tables':
                    from edit_tables import edit_tables_delivery
                    result = edit_tables_delivery(source, output, specification, protected_inputs=[config])
                elif kind == 'image':
                    if replacement is None:
                        raise ValueError('Image replacement input is required')
                    from replace_image import replace_image_delivery
                    result = replace_image_delivery(source, replacement, output, specification, protected_inputs=[config])
                else:
                    from manage_comments import manage_comments_delivery
                    result = manage_comments_delivery(source, specification, output, protected_inputs=[config])
                expected = task.inspect(output.as_posix(), contents=True)
                def finish():
                    seal_cleanup(manifest, outcome='success', verified=True, archive_status=result['archive_status'])
                    return cleanup_task(manifest)
                return _finish_delivery(manifest, result, expected, finish)
            finally:
                fcntl.flock(task.root, fcntl.LOCK_UN)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        # No blind regeneration or cleanup after an uncertain edit result.
        return failure('edit_state_unconfirmed' if output.exists() else 'edit_rejected', exc,
                       action='inspect_task', task_manifest=manifest, output_may_exist=output.exists(),
                       cleanup={'cleanup_complete': False, 'status': 'retained'})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=OPERATIONS)
    parser.add_argument('input')
    parser.add_argument('output')
    parser.add_argument('--replacement')
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read(MAX_TASK_BYTES + 1)
        if len(raw) > MAX_TASK_BYTES:
            raise ValueError('Edit specification exceeds 2 MiB')
        spec = json.loads(raw, object_pairs_hook=_unique_object)
        result = edit_task(args.operation, args.input, args.output, spec, replacement=args.replacement)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        result = failure('edit_request_rejected', exc, action='inspect_request')
    print(json.dumps(result, ensure_ascii=True))
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
