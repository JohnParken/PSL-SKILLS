"""Archive an explicit list of this turn's completed deliverables, never scan folders."""
import argparse
import json
import os
import re
from pathlib import Path
import sys
import unicodedata

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from create_docx import (_check_delivery_path, _copy_main_to_archive,
                         _paths_alias, _check_cli_runtime)
from output_location import resolve_output_location, read_router_config_from_environment


def validate_output_path(path: Path, protected_inputs=()) -> Path:
    """Enforce task-relative paths before an editing CLI writes its output."""
    path = Path(path)
    _check_delivery_path(Path.cwd(), path)
    if not path.name:
        raise ValueError("output must name a file")
    name = path.name
    if (len(name.encode('utf-8')) > 255 or name.endswith((' ', '.'))
            or any(c in '/\\<>:"|?*' or unicodedata.category(c).startswith('C') for c in name)):
        raise ValueError('invalid deliverable filename')
    device = name.split('.', 1)[0].upper()
    if device in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} or re.fullmatch(r'(?:COM|LPT)[1-9¹²³]', device):
        raise ValueError('reserved deliverable filename')
    if any(_paths_alias(path, Path(item)) for item in protected_inputs):
        raise ValueError('output must not overwrite an input file')
    return path


def archive_outputs(outputs: list[Path], protected_inputs: list[Path]) -> dict:
    """Copy only supplied completed files; preserve inputs and report partial failures."""
    summary = {'status': 'completed', 'archive_status': 'skipped',
               'used_fallback': True, 'fallback_reason': 'archive_config_error',
               'files': [], 'warnings': []}
    try:
        task_dir = Path.cwd()
        location = resolve_output_location(read_router_config_from_environment())
        summary.update(archive_status='skipped' if location.used_fallback else 'copied',
                       used_fallback=location.used_fallback, fallback_reason=location.fallback_reason)
        sources = [validate_output_path(Path(path)) for path in outputs]
        if not sources:
            raise ValueError('at least one completed deliverable is required')
        if len({path.name.casefold() for path in sources}) != len(sources):
            raise ValueError('deliverables must have distinct archive filenames')
        for source in sources:
            if not source.is_file():
                raise ValueError(f'deliverable is not a regular file: {source}')
            if any(_paths_alias(source, Path(item)) for item in protected_inputs):
                raise ValueError(f'deliverable must not be an input file: {source}')
        if not location.used_fallback:
            for source in sources:
                target = location.output_dir / source.name
                _check_delivery_path(task_dir, target)
                protected = [Path(item) for item in protected_inputs] + [p for p in sources if p != source]
                if any(_paths_alias(target, item) for item in protected):
                    raise ValueError(f'archive target conflicts with an input or another deliverable: {target}')
    except Exception as exc:
        summary.update(archive_status='failed', warnings=[str(exc)])
        return summary
    for source in sources:
        target = location.output_dir / source.name
        item = {'output': source.as_posix(),
                'archive_output': None if location.used_fallback else target.as_posix(),
                'archive_status': 'skipped'}
        summary['files'].append(item)
        if location.used_fallback:
            continue
        try:
            if source.resolve() == (task_dir / target).resolve():
                item['archive_status'] = 'already_archived'
                continue
            protected = [Path(p) for p in protected_inputs] + [p for p in sources if p != source]
            _copy_main_to_archive(task_dir, source, location, source.name, source, protected)
            item['archive_status'] = 'copied'
        except Exception as exc:
            item['archive_status'] = 'failed'
            item['error'] = str(exc)
            summary['archive_status'] = 'failed'
            summary['warnings'].append(f'{source}: {exc}')
    return summary


def finalize_delivery(output, protected_inputs, *, status, result=None) -> dict:
    """Archive a published file without turning an archive error into primary failure."""
    output = Path(output)
    try:
        archive_result = archive_outputs([output], protected_inputs)
    except Exception as exc:
        archive_result = {'archive_status': 'failed', 'used_fallback': True,
                          'fallback_reason': 'archive_error', 'files': [],
                          'warnings': [str(exc)]}
    payload = {**archive_result, 'status': status, 'output': output.as_posix()}
    if result is not None:
        payload['result'] = result if isinstance(result, dict) else str(result)
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('outputs', type=Path, nargs='*', help='explicit completed delivery files only')
    parser.add_argument('--protect-input', type=Path, action='append', default=[])
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    if args.check:
        if args.outputs or args.protect_input:
            parser.error('--check takes no file arguments')
        return 0 if _check_cli_runtime() else 1
    if not args.outputs:
        parser.error('explicit completed delivery files are required')
    result = archive_outputs(args.outputs, args.protect_input)
    print(json.dumps(result, ensure_ascii=True))
    for warning in result['warnings']:
        print('archive-warning: ' + warning, file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
