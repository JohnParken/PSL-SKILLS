"""Offline exact-content acceptance checks for separately obtained .wps samples.

Manifest provenance is declared metadata and always requires human review. A
passing report checks one exact byte sequence against declared expectations; it
does not verify a producer or establish full WPS format compatibility.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

MAX_SAMPLE_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
SCHEMA_VERSION = 'wps_sample_expectations_v1'
PROVENANCE_FIELDS = ('source', 'original_filename', 'producer_evidence',
                     'independent_expectation_source')
READ_OPTIONS = {
    'compatibility': {'strict', 'legacy_excel_clsid', 'legacy_word_clsid'},
    'field_policy': {'reject', 'cached_result'},
    'object_policy': {'reject', 'placeholder'},
    'hidden_text': {'include', 'exclude', 'annotate'},
    'revisions': {'accepted', 'original', 'all'},
    'chart_policy': {'reject', 'opaque'},
}
BASE_REPORT = {
    'status': 'failed',
    'passed': False,
    'provenance_status': 'not_assessed',
    'full_format_verified': False,
    'evidence_kind': 'declared_original_exact_content',
    'checks': {},
    'failure_code': None,
}


class VerificationError(ValueError):
    """A bounded verifier failure represented by a stable code."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise VerificationError(code)


def _within_task_directory(value, cwd):
    if not isinstance(value, (str, os.PathLike)):
        _fail('invalid_task_relative_path')
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or '\x00' in raw or '\\' in raw:
        _fail('invalid_task_relative_path')
    candidate = Path(raw)
    if candidate.is_absolute() or '..' in candidate.parts:
        _fail('invalid_task_relative_path')
    try:
        resolved = (cwd / candidate).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail('task_path_unavailable')
    if not resolved.is_relative_to(cwd):
        _fail('task_path_escape')
    return resolved


def _stat_key(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _read_bounded_regular_file(path, maximum, code_prefix):
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0)
    flags |= getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(path, flags)
    except (OSError, ValueError):
        _fail(code_prefix + '_unreadable')
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail(code_prefix + '_not_regular')
        if before.st_size > maximum:
            _fail(code_prefix + '_too_large')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            data = stream.read(maximum + 1)
        after = os.fstat(descriptor)
        if (len(data) != before.st_size or len(data) > maximum
                or _stat_key(before) != _stat_key(after)):
            _fail(code_prefix + '_changed_during_read')
        return data, _stat_key(before)
    except OSError:
        _fail(code_prefix + '_unreadable')
    finally:
        os.close(descriptor)


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('manifest_duplicate_key')
        result[key] = value
    return result


def _nonempty_string(value):
    return type(value) is str and bool(value.strip())


def _validate_manifest(manifest):
    if type(manifest) is not dict:
        _fail('manifest_invalid')
    if set(manifest) != {'schema_version', 'sha256', 'bytes', 'provenance',
                         'options', 'expected'}:
        _fail('manifest_invalid')
    if manifest['schema_version'] != SCHEMA_VERSION:
        _fail('manifest_invalid')
    if (type(manifest['sha256']) is not str
            or re.fullmatch(r'[0-9a-f]{64}', manifest['sha256']) is None):
        _fail('manifest_invalid')
    if type(manifest['bytes']) is not int or not 1 <= manifest['bytes'] <= MAX_SAMPLE_BYTES:
        _fail('manifest_invalid')

    provenance = manifest['provenance']
    if type(provenance) is not dict or set(provenance) != set(PROVENANCE_FIELDS):
        _fail('manifest_invalid')
    if any(not _nonempty_string(provenance[key]) for key in PROVENANCE_FIELDS):
        _fail('manifest_invalid')
    original_name = provenance['original_filename']
    if ('/' in original_name or '\\' in original_name
            or not original_name.endswith('.wps')
            or not original_name[:-4].strip()):
        _fail('manifest_invalid')

    options = manifest['options']
    if type(options) is not dict:
        _fail('manifest_invalid')
    allowed_options = set(READ_OPTIONS) | {'include_related'}
    if set(options) - allowed_options:
        _fail('manifest_invalid')
    for name, choices in READ_OPTIONS.items():
        if name in options and (type(options[name]) is not str or options[name] not in choices):
            _fail('manifest_invalid')
    if 'include_related' in options and type(options['include_related']) is not bool:
        _fail('manifest_invalid')

    expected = manifest['expected']
    required_expected = {'status', 'format', 'text', 'paragraphs'}
    if (type(expected) is not dict or not required_expected <= set(expected)
            or set(expected) - required_expected - {'binary_table_rows'}):
        _fail('manifest_invalid')
    if expected['status'] not in ('ok', 'partial'):
        _fail('manifest_invalid')
    if not _nonempty_string(expected['format']) or len(expected['format']) > 128:
        _fail('manifest_invalid')
    if not _nonempty_string(expected['text']):
        _fail('manifest_invalid')
    paragraphs = expected['paragraphs']
    if (type(paragraphs) is not list or not paragraphs
            or any(type(item) is not str for item in paragraphs)
            or not any(item.strip() for item in paragraphs)):
        _fail('manifest_invalid')
    if 'binary_table_rows' in expected:
        rows = expected['binary_table_rows']
        if type(rows) is not list:
            _fail('manifest_invalid')
        if any(type(row) is not list or not row
               or any(type(cell) is not str for cell in row) for row in rows):
            _fail('manifest_invalid')
    return manifest


def _read_manifest(path):
    data, _ = _read_bounded_regular_file(path, MAX_MANIFEST_BYTES, 'manifest')
    try:
        manifest = json.loads(data.decode('utf-8'), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        _fail('manifest_invalid')
    return _validate_manifest(manifest)


def _table_rows(result):
    structure = result.get('binary_table_structure')
    if type(structure) is not dict or type(structure.get('rows')) is not list:
        return None
    output = []
    for row in structure['rows']:
        if type(row) is not dict or type(row.get('cells')) is not list:
            return None
        cells = []
        for cell in row['cells']:
            if type(cell) is not dict or type(cell.get('text')) is not str:
                return None
            cells.append(cell['text'])
        output.append(cells)
    return output


def _no_positive_compatibility_claims(result):
    for name in ('producer_verified', 'native_et_ett_parsed',
                 'wps_compatibility_verified'):
        if name in result and result[name] is not False:
            return False
    if 'reading_assessment' in result:
        assessment = result['reading_assessment']
        if type(assessment) is not dict:
            return False
        if ('full_format_verified' in assessment
                and assessment['full_format_verified'] is not False):
            return False
    return True


def _make_report():
    return dict(BASE_REPORT, checks={})


def _failure(report, code):
    report['status'] = 'failed'
    report['passed'] = False
    report['failure_code'] = code
    return report


def verify(sample_argument, manifest_argument):
    """Verify task-relative sample bytes against a bounded declared manifest."""
    report = _make_report()
    try:
        cwd = Path.cwd().resolve(strict=True)
        sample_path = _within_task_directory(sample_argument, cwd)
        manifest_path = _within_task_directory(manifest_argument, cwd)
        if Path(os.fspath(sample_argument)).suffix != '.wps':
            _fail('sample_suffix_invalid')
        manifest = _read_manifest(manifest_path)
        report['provenance_status'] = 'declared_requires_human_review'
        report['checks']['manifest_valid'] = True
        sample_data, initial_stat = _read_bounded_regular_file(
            sample_path, MAX_SAMPLE_BYTES, 'sample')
        digest = hashlib.sha256(sample_data).hexdigest()
        report['checks']['sha256'] = digest == manifest['sha256']
        report['checks']['bytes'] = len(sample_data) == manifest['bytes']
        if not report['checks']['sha256'] or not report['checks']['bytes']:
            parse_result = None
            parse_failed = False
        else:
            try:
                from read_wps import read_bytes
                parse_result = read_bytes(sample_data, **manifest['options'])
                parse_failed = type(parse_result) is not dict
            except Exception:
                parse_result = None
                parse_failed = True
        if parse_failed:
            report['checks']['reader'] = False
        elif parse_result is None:
            report['checks']['reader'] = None
        else:
            report['checks']['reader'] = True
        try:
            final_data, final_stat = _read_bounded_regular_file(
                sample_path, MAX_SAMPLE_BYTES, 'sample')
            unchanged = (initial_stat == final_stat
                         and len(final_data) == len(sample_data)
                         and hashlib.sha256(final_data).digest()
                         == hashlib.sha256(sample_data).digest())
        except VerificationError:
            unchanged = False
        report['checks']['input_unchanged'] = unchanged
        if report['checks'].get('reader') is None:
            if not unchanged:
                return _failure(report, 'input_changed')
            return _failure(report, 'sample_identity_mismatch')
        if parse_result is not None and not parse_failed:
            expected = manifest['expected']
            actual_status = parse_result.get('status')
            actual_format = parse_result.get('format')
            actual_text = parse_result.get('text')
            actual_paragraphs = parse_result.get('paragraphs')
            report['checks']['status'] = (
                type(actual_status) is str and actual_status == expected['status'])
            report['checks']['format'] = (
                type(actual_format) is str and actual_format == expected['format'])
            report['checks']['text'] = (
                type(actual_text) is str and actual_text == expected['text'])
            report['checks']['paragraphs'] = (
                type(actual_paragraphs) is list
                and all(type(item) is str for item in actual_paragraphs)
                and actual_paragraphs == expected['paragraphs'])
            report['checks']['full_format_claims_absent'] = (
                _no_positive_compatibility_claims(parse_result))
            if 'binary_table_rows' in expected:
                report['checks']['binary_table_rows'] = (
                    _table_rows(parse_result) == expected['binary_table_rows'])
        failed_checks = [value for value in report['checks'].values() if value is False]
        if failed_checks:
            if report['checks'].get('input_unchanged') is False:
                return _failure(report, 'input_changed')
            if report['checks'].get('reader') is False:
                return _failure(report, 'reader_failed')
            return _failure(report, 'expectation_mismatch')
        report['status'] = 'passed'
        report['passed'] = True
        return report
    except VerificationError as exc:
        return _failure(report, exc.code)
    except (OSError, TypeError, ValueError, RecursionError):
        return _failure(report, 'verification_failed')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sample', help='Task-relative .wps sample')
    parser.add_argument('expectations', help='Task-relative JSON expectation manifest')
    args = parser.parse_args(argv)
    report = verify(args.sample, args.expectations)
    print(json.dumps(report, ensure_ascii=False, separators=(',', ':')))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
