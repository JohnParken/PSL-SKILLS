from __future__ import annotations
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

from .artifact_files import _check_delivery_path, _copy_main_to_archive, _paths_alias
from .output_location import resolve_output_location, read_router_config_from_environment


def validate_input_path(path: Path) -> Path:
    """Check CLI input boundaries before opening task data; preserve legal user names."""
    path = Path(path)
    _check_delivery_path(Path.cwd(), path)
    if not path.name or not path.is_file():
        raise ValueError("input must name a regular task file")
    return path


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


def archive_user_message(result: dict) -> str:
    """Render archive state for users without exposing internal paths or errors."""
    status = result.get('archive_status')
    files = result.get('files')
    if (status == 'copied' and isinstance(files, list) and files
            and all(isinstance(item, dict) and item.get('archive_status') in
                    ('copied', 'already_archived') for item in files)):
        return '归档完成'
    if status == 'skipped':
        return '归档未执行'
    if status == 'failed':
        return '归档未完成'
    return '归档状态未确认'


def _archive_summary(summary: dict) -> dict:
    summary['archive_message'] = archive_user_message(summary)
    return summary


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
        return _archive_summary(summary)
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
            if getattr(exc, 'temporary_residuals', None):
                summary.setdefault('temporary_residuals', []).extend(exc.temporary_residuals)
            summary['archive_status'] = 'failed'
            summary['warnings'].append(f'{source}: {exc}')
    return _archive_summary(summary)


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
    payload['archive_message'] = archive_user_message(payload)
    if result is not None:
        payload['result'] = result if isinstance(result, dict) else str(result)
    return payload


