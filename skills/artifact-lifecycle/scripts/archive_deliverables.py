"""Archive an explicit list of this turn's completed deliverables, never scan folders."""
import argparse
import errno
import hashlib
import json
import os
import re
from pathlib import Path
import sys
import unicodedata

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_files import _check_delivery_path, _copy_main_to_archive, _paths_alias
from output_location import resolve_output_location, read_router_config_from_environment


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


def validate_delivery_output(path: str | Path, protected_inputs=()) -> Path:
    """Accept a filename only, before Path can normalize user-supplied syntax."""
    raw = os.fspath(path)
    if (not isinstance(raw, str) or not raw or raw in {'.', '..'}
            or '/' in raw or '\\' in raw):
        raise ValueError('output path must be a bare filename in the task directory')
    return validate_output_path(Path(raw), protected_inputs)


def choose_delivery_output(path: Path, protected_inputs=()) -> Path:
    """Choose an unused versioned name using metadata only; do not reserve it."""
    path = validate_delivery_output(path, protected_inputs)
    location = resolve_output_location(read_router_config_from_environment())
    stem, suffix = path.stem, path.suffix
    match = re.fullmatch(r'(.+)_第([1-9][0-9]*)版', stem)
    base = match[1] if match else stem
    first_version = int(match[2]) + 1 if match else 2
    for attempt in range(1000):
        candidate = path
        if attempt:
            ending = f'_第{first_version + attempt - 1}版{suffix}'
            budget = 255 - len(ending.encode('utf-8'))
            shortened = base
            while shortened and len(shortened.encode('utf-8')) > budget:
                shortened = shortened[:-1]
            if not shortened:
                raise ValueError('Filename has no space for a version suffix')
            candidate = path.with_name(shortened + ending)
        validate_output_path(candidate, protected_inputs)
        if os.path.lexists(candidate):
            continue
        if not location.used_fallback:
            try:
                _check_delivery_path(Path.cwd(), location.output_dir, directory=True)
                target = location.output_dir / candidate.name
                if os.path.lexists(target):
                    continue
            except (OSError, ValueError):
                # Optional archive access must not block a usable main output.
                # Actual archival will report its own failure without overwriting.
                pass
        return candidate
    raise ValueError('No free filename found within 1000 version candidates')


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


def archive_user_notice(result: dict) -> str:
    """Guide users to cloud space after archival; do not assert upload completion."""
    state = archive_user_message(result)
    if state == '归档完成':
        return '🎉 文件归档成功，请到云空间查看。 ☁️✨'
    if state == '归档未执行':
        return '本次未执行归档，请先核对文件结果。'
    if result.get('archive_status') == 'handoff_required':
        return '文件待交接归档，归档完成后再提示到云空间查看。'
    if result.get('archive_status') == 'not_attempted':
        return '本次未执行归档，请先核对文件结果。'
    if state == '归档未完成':
        files = result.get('files')
        if isinstance(files, list) and files and all(isinstance(item, dict) for item in files):
            succeeded = sum(item.get('archive_status') in ('copied', 'already_archived') for item in files)
            # A failed summary can invalidate otherwise copied entries (for example,
            # a primary file changed after verification). Do not call that a partial success.
            if 0 < succeeded < len(files):
                return '部分文件归档成功，请到云空间查看已归档文件；其余文件归档未完成。'
        return '🥺 本次归档未完成，请先核对文件结果。 📄'
    return '归档结果尚未确认，请先核对文件结果。'


def _archive_summary(summary: dict) -> dict:
    summary['archive_message'] = archive_user_message(summary)
    summary['archive_user_notice'] = archive_user_notice(summary)
    return summary


def _archive_digest(path):
    """Read a bounded-memory digest without accepting links as archive evidence."""
    validate_input_path(Path(path))
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def archive_outputs(outputs: list[Path], protected_inputs: list[Path],
                    *, replace_existing: bool = False) -> dict:
    """Copy only supplied completed files; preserve inputs and report partial failures."""
    summary = {'status': 'completed', 'archive_status': 'skipped',
               'used_fallback': True, 'fallback_reason': 'archive_config_error',
               'files': [], 'warnings': []}
    try:
        if type(replace_existing) is not bool:
            raise ValueError('replace_existing must be a boolean')
        task_dir = Path.cwd()
        location = resolve_output_location(read_router_config_from_environment())
        summary.update(archive_status='skipped' if location.used_fallback else 'copied',
                       used_fallback=location.used_fallback, fallback_reason=location.fallback_reason)
        sources = [validate_delivery_output(path) for path in outputs]
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
            expected_digest = _archive_digest(source)
            protected = [Path(p) for p in protected_inputs] + [p for p in sources if p != source]
            if replace_existing:
                _copy_main_to_archive(task_dir, source, location, source.name, source, protected,
                                      replace_existing=True)
            else:
                _copy_main_to_archive(task_dir, source, location, source.name, source, protected)
            if _archive_digest(target) != expected_digest or _archive_digest(source) != expected_digest:
                raise ValueError('Archive verification failed: source changed or archive bytes differ')
            item['archive_verified'] = True
            item['archive_status'] = 'copied'
        except Exception as exc:
            item['archive_status'] = 'failed'
            item['error'] = str(exc)
            if getattr(exc, 'error_code', None):
                item['error_code'] = exc.error_code
            else:
                item['error_code'] = {
                    errno.EXDEV: 'archive_cross_device', errno.EACCES: 'archive_permission_denied',
                    errno.EPERM: 'archive_permission_denied', errno.ENOSPC: 'archive_storage_full',
                }.get(getattr(exc, 'errno', None), 'archive_copy_or_verification_failed')
            if getattr(exc, 'temporary_residuals', None):
                summary.setdefault('temporary_residuals', []).extend(exc.temporary_residuals)
            summary['archive_status'] = 'failed'
            summary['warnings'].append(f'{source}: {exc}')
    return _archive_summary(summary)


def finalize_delivery(output, protected_inputs, *, status, result=None) -> dict:
    """Archive a published file without turning an archive error into primary failure."""
    raw_output = output
    output = Path(output)
    try:
        archive_result = archive_outputs([raw_output], protected_inputs)
    except Exception as exc:
        archive_result = {'archive_status': 'failed', 'used_fallback': True,
                          'fallback_reason': 'archive_error', 'files': [],
                          'warnings': [str(exc)]}
    payload = {**archive_result, 'status': status, 'output': output.as_posix()}
    payload['archive_message'] = archive_user_message(payload)
    payload['archive_user_notice'] = archive_user_notice(payload)
    if result is not None:
        payload['result'] = result if isinstance(result, dict) else str(result)
    return payload


