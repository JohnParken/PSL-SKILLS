"""Archive an explicit list of this turn's completed deliverables, never scan folders."""
import argparse
import errno
import hashlib
import json
import os
import re
from pathlib import Path
import sys
import stat
import tempfile
import unicodedata

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_files import (ArchiveConflictError, _check_delivery_path,
                            _copy_main_to_archive, _paths_alias)
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


ARCHIVE_CONTRACT = 'archive-delivery-v2'
_SUCCESS_ITEMS = frozenset({'copied', 'already_archived'})


def archive_user_message(result: dict) -> str:
    """Render verified batch state without exposing internal paths or errors."""
    state = result.get('archive_status')
    files = result.get('files')
    if (state == 'success' and isinstance(files, list) and files
            and all(isinstance(item, dict) and item.get('archive_status') in _SUCCESS_ITEMS
                    and item.get('archive_verified') is True for item in files)):
        return '归档完成'
    if state == 'not_required':
        return '无需归档'
    if (state == 'partial' and isinstance(files, list)
            and any(isinstance(item, dict) and item.get('archive_status') in _SUCCESS_ITEMS
                    and item.get('archive_verified') is True for item in files)
            and any(isinstance(item, dict) and item.get('archive_status') == 'failed' for item in files)):
        return '部分归档完成'
    if state == 'failed':
        return '归档未完成'
    return '归档状态未确认'


def archive_user_notice(result: dict) -> str:
    """Preserve the agreed space notice; never assert verified cloud upload."""
    state = archive_user_message(result)
    if state == '归档完成':
        return '🎉 文件归档成功，请到云空间查看。 ☁️✨'
    if state == '部分归档完成':
        return '部分文件归档成功，请到云空间查看已归档文件；其余文件归档未完成。'
    if state == '无需归档':
        return '本次没有需要归档的最终交付文件。'
    if state == '归档未完成':
        return '🥺 本次归档未完成，请先核对文件结果。 📄'
    if result.get('archive_status') in ('not_attempted', 'handoff_required'):
        return '本次归档尚未完成，请先核对文件结果。'
    return '归档结果尚未确认，请先核对文件结果。'


def _archive_summary(summary: dict) -> dict:
    files = summary['files']
    summary['completed_count'] = sum(item['archive_status'] in _SUCCESS_ITEMS for item in files)
    summary['failed_count'] = sum(item['archive_status'] == 'failed' for item in files)
    summary['archive_message'] = archive_user_message(summary)
    summary['archive_user_notice'] = archive_user_notice(summary)
    return summary


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _archive_snapshot(path):
    """Hash a stable regular file, refusing final symlinks and changed identities."""
    path = validate_input_path(Path(path))
    if not hasattr(os, 'O_NOFOLLOW'):
        raise RuntimeError('Safe archive hashing requires O_NOFOLLOW')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Archive evidence must be a regular file')
        digest = hashlib.sha256()
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = -1
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        validate_input_path(path)
        if _identity(before) != _identity(after) or _identity(after) != _identity(path.stat(follow_symlinks=False)):
            raise ValueError('Archive evidence changed during verification')
        return before.st_size, digest.hexdigest(), _identity(after)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _archive_digest(path):
    """Return a digest only after stable-file verification."""
    return _archive_snapshot(path)[1]


def _check_current_location(location):
    """Re-read the current host context, without falling back to a historical ID."""
    current = resolve_output_location(read_router_config_from_environment())
    if current.used_fallback or current.output_dir != location.output_dir:
        raise ValueError('Current archive task context is missing or changed')


def _verify_pair(source, target, expected):
    if _paths_alias(source, target):
        raise ValueError('Archive target must not alias the main deliverable')
    actual = _archive_snapshot(target)
    if actual[:2] != expected[:2]:
        raise ArchiveConflictError('Archive target has different content; existing archive preserved')
    if _archive_snapshot(source) != expected:
        raise ValueError('Source changed during archive verification')
    if _identity(target.stat(follow_symlinks=False)) != actual[2]:
        raise ValueError('Archive target changed during verification')


def _error_code(exc):
    return getattr(exc, 'error_code', None) or {
        errno.EXDEV: 'archive_cross_device', errno.EACCES: 'archive_permission_denied',
        errno.EPERM: 'archive_permission_denied', errno.ENOSPC: 'archive_storage_full',
    }.get(getattr(exc, 'errno', None), 'archive_copy_or_verification_failed')


def archive_delivery(outputs, protected_inputs=()) -> dict:
    """Archive an explicit final list under this call's current host questionId.

    The host must supply the current request's ROUTER_CONFIG_VARIABLES. This
    function cannot establish freshness of a stale value supplied by the host.
    No historical receipt, directory discovery or overwrite option is accepted.
    """
    summary = {'status': 'completed', 'archive_contract': ARCHIVE_CONTRACT,
               'archive_status': 'failed', 'used_fallback': True,
               'fallback_reason': None, 'files': [], 'warnings': []}
    declared = list(outputs)
    try:
        sources = [validate_delivery_output(path) for path in declared]
        summary['requested_count'] = len(sources)
        if not sources:
            summary.update(archive_status='not_required', reason_code='no_final_deliverables')
            return _archive_summary(summary)
        if len({path.name.casefold() for path in sources}) != len(sources):
            raise ValueError('Deliverables must have distinct archive filenames')
        task_dir = Path.cwd()
        protected_inputs = [Path(item) for item in protected_inputs]
        for source in sources:
            validate_input_path(source)
            if any(_paths_alias(source, item) for item in protected_inputs):
                raise ValueError('Deliverable must not alias a protected input')
        location = resolve_output_location(read_router_config_from_environment())
        summary.update(used_fallback=location.used_fallback, fallback_reason=location.fallback_reason)
        if location.used_fallback:
            summary.update(reason_code='current_question_id_unavailable')
            summary['warnings'].append('Current host questionId is unavailable or invalid; archive not performed')
            summary['files'] = [dict(output=source.as_posix(), archive_output=None,
                                     archive_status='failed', reason_code='current_question_id_unavailable',
                                     error_code='current_question_id_unavailable') for source in sources]
            return _archive_summary(summary)
        # Validate the complete declared set before any copy or directory creation.
        for source in sources:
            target = location.output_dir / source.name
            _check_delivery_path(task_dir, target)
            if any(_paths_alias(target, item) for item in protected_inputs + sources):
                raise ValueError('Archive target aliases an input or deliverable')
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        summary.update(reason_code='archive_request_invalid', requested_count=len(declared), warnings=[str(exc)])
        summary['files'] = [dict(output=str(path), archive_output=None, archive_status='failed',
                                 reason_code='archive_request_invalid', error_code='archive_request_invalid',
                                 error=str(exc)) for path in declared]
        return _archive_summary(summary)

    for source in sources:
        target = location.output_dir / source.name
        item = dict(output=source.as_posix(), archive_output=target.as_posix(), archive_status='failed')
        summary['files'].append(item)
        try:
            _check_current_location(location)
            expected = _archive_snapshot(source)
            if os.path.lexists(target):
                _verify_pair(source, target, expected)
                action = 'already_archived'
            else:
                protected = protected_inputs + [path for path in sources if path != source]
                try:
                    _copy_main_to_archive(task_dir, source, location, source.name, source, protected)
                    action = 'copied'
                except ArchiveConflictError:
                    # A concurrent publisher won. Verify rather than overwrite.
                    action = 'already_archived'
                _verify_pair(source, target, expected)
            _check_current_location(location)
            item.update(archive_status=action, archive_verified=True,
                        reason_code='copy_verified' if action == 'copied' else 'existing_copy_verified',
                        bytes=expected[0], sha256=expected[1])
        except (OSError, ValueError, RuntimeError) as exc:
            item.update(error=str(exc), error_code=_error_code(exc), reason_code=_error_code(exc))
            if getattr(exc, 'temporary_residuals', None):
                summary.setdefault('temporary_residuals', []).extend(exc.temporary_residuals)
            summary['warnings'].append(f'{source}: {exc}')
    successes = sum(item['archive_status'] in _SUCCESS_ITEMS for item in summary['files'])
    summary['archive_status'] = 'success' if successes == len(sources) else 'partial' if successes else 'failed'
    return _archive_summary(summary)


def archive_exit_code(result):
    """New archive CLI contract; independent from primary business status."""
    return {'success': 0, 'not_required': 0, 'partial': 3, 'failed': 1}.get(result.get('archive_status'), 1)


def finalize_delivery(output, protected_inputs, *, status, result=None) -> dict:
    """Archive a published file without changing the primary business outcome."""
    try:
        archive_result = archive_delivery([output], protected_inputs)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        archive_result = _archive_summary({'archive_contract': ARCHIVE_CONTRACT,
            'archive_status': 'failed', 'used_fallback': True, 'fallback_reason': 'archive_error',
            'files': [], 'warnings': [str(exc)]})
    payload = {**archive_result, 'status': status, 'output': Path(output).as_posix()}
    if result is not None:
        payload['result'] = result if isinstance(result, dict) else str(result)
    return payload


def environment_check():
    """Exercise task-root reads, writes and exclusive hardlink publication only."""
    if sys.version_info < (3, 10) or not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'link'):
        raise RuntimeError('Python >=3.10 with O_NOFOLLOW and hardlinks is required')
    descriptor, name = tempfile.mkstemp(prefix='.archive-check-', dir='.')
    source = Path(name).relative_to(Path.cwd()) if Path(name).is_absolute() else Path(name)
    target = source.with_name(source.name + '.link')
    info = os.fstat(descriptor)
    identity = (info.st_dev, info.st_ino)
    linked = False
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            descriptor = -1
            stream.write(b'archive-capability-probe')
        os.link(source, target, follow_symlinks=False)
        linked = True
        if _archive_digest(source) != _archive_digest(target):
            raise RuntimeError('Archive capability probe readback failed')
        try:
            os.link(source, target, follow_symlinks=False)
        except FileExistsError:
            pass
        else:
            raise RuntimeError('Exclusive archive publication is unavailable')
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        cleanup_errors = []
        for path in ([target] if linked else []) + [source]:
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) != identity:
                    raise OSError('archive probe identity changed; retained')
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                cleanup_errors.append(str(exc))
        if cleanup_errors:
            raise RuntimeError('archive probe cleanup incomplete: ' + '; '.join(cleanup_errors))
    return {'status': 'environment-ok', 'task_writable': True,
            'exclusive_publish': True, 'check_kind': 'environment',
            'artifact_validation': 'not_run', 'archive_contract': ARCHIVE_CONTRACT}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('outputs', nargs='*', help='explicit completed delivery files only')
    parser.add_argument('--protect-input', type=Path, action='append', default=[])
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    if args.check:
        if args.outputs or args.protect_input:
            parser.error('--check takes no file arguments')
        try:
            result = environment_check()
            code = 0
        except (OSError, ValueError, RuntimeError) as exc:
            result = {'status': 'environment-unavailable', 'message': str(exc),
                      'check_kind': 'environment', 'artifact_validation': 'not_run',
                      'archive_contract': ARCHIVE_CONTRACT}
            code = 1
        print(json.dumps(result, ensure_ascii=True), flush=True)
        return code
    result = archive_delivery(args.outputs, args.protect_input)
    print(json.dumps(result, ensure_ascii=True), flush=True)
    for warning in result['warnings']:
        print('archive-warning: ' + warning, file=sys.stderr)
    return archive_exit_code(result)


if __name__ == '__main__':
    raise SystemExit(main())
