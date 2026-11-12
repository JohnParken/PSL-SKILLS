"""Bounded, content-identified WPS Spreadsheets (ET/ETT) extraction."""
import argparse
import json
from pathlib import Path
import sys
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wps_cfb import MAX_FILE_BYTES, SIGNATURE
from wps_access import DEFAULT_FILE_COMPATIBILITY, ParseError, read_input, load_compound, resolve_chart_policy
from wps_result import (MAX_JSON_OUTPUT_BYTES, MAX_JSON_OUTPUT_BYTES_LIMIT, compact_result,
                        encode_result as _encode_result, write_result_file)

KNOWN_COMPATIBILITY = ('data', 'strict', 'legacy_excel_clsid')


def require(condition, location, message, status='corrupt'):
    if not condition:
        raise ParseError(status, location, message)


def failure(status, location, message):
    from wps_access import diagnostic_context
    return {
        'status': status,
        'location': location,
        'message': message,
        'diagnostic_context': diagnostic_context(status, location),
        'partial': False,
        'text': None,
        'producer_verified': False,
    }


def _annotate_legacy_options(result, compatibility, salvage_unknown_metadata):
    """Record ignored legacy options instead of silently dropping them."""
    if not isinstance(result, dict):
        return result
    issues = result.setdefault('issues', [])
    codes = {item.get('code') for item in issues if isinstance(item, dict)}
    if compatibility != 'data' and 'compatibility_option_ignored' not in codes:
        issues.append({'code': 'compatibility_option_ignored',
                       'requested': str(compatibility),
                       'message': 'Compatibility policies are deprecated; the tolerant content-first reader was used.'})
    if salvage_unknown_metadata and 'salvage_option_ignored' not in codes:
        issues.append({'code': 'salvage_option_ignored',
                       'message': 'Best-effort salvage is the default reading policy; the option no longer changes behavior.'})
    if issues and result.get('status') == 'ok':
        result['status'] = 'partial'
        result['partial'] = True
    return result


def _select_workbook(compound, require, location):
    workbooks = [p for p in compound.streams if len(p) == 1 and p[0].casefold() in ('workbook', 'book')]
    if len(workbooks) > 1:
        preferred = []
        for path in workbooks:
            content = compound.streams[path]
            if len(content) >= 20 and content[0:2] == b'\x09\x08' and content[2:4] == b'\x10\x00' and content[4:8] == b'\x00\x06\x05\x00':
                preferred.append(path)
        chosen = preferred[0] if preferred else workbooks[0]
        compound.issues.append({'code': 'multiple_workbook_streams',
                                'streams': [list(path) for path in workbooks],
                                'chosen': list(chosen),
                                'message': 'More than one root Workbook/Book stream is present; the first BIFF8-looking candidate was read.'})
        return chosen
    require(len(workbooks) == 1, location, 'Require exactly one supported workbook payload', 'unsupported')
    return workbooks[0]


def _read_bytes(data, *, source=None, compatibility=DEFAULT_FILE_COMPATIBILITY, chart_policy=None,
                requested_chart_policy=None, salvage_unknown_metadata=False, **ignored_kwargs):
    """Identify content first; suffix is provenance, never a format selector."""
    if ignored_kwargs:
        raise TypeError('Unsupported read options: ' + ', '.join(sorted(ignored_kwargs)))
    require(isinstance(data, bytes) and len(data) <= MAX_FILE_BYTES,
            {'stage': 'input'}, 'Input exceeds byte budget or is not bytes', 'resource_limit')
    if not isinstance(compatibility, str) or compatibility not in KNOWN_COMPATIBILITY:
        # Unknown legacy values are tolerated as a request for the default
        # content-first policy rather than being a brittle option validation.
        compatibility = 'data'
    requested_chart = requested_chart_policy if requested_chart_policy is not None else chart_policy
    if data.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        from wps_ooxml_sheet import package_kind, read_bytes as read_sheet_bytes
        try:
            family = package_kind(data)
        except (ValueError, KeyError, zipfile.BadZipFile, zlib.error) as exc:
            raise ParseError('invalid_or_unsupported', {'stage': 'OOXML package'}, str(exc)) from exc
        require(family == 'spreadsheet', {'stage': 'OOXML package'},
                'Package is not a spreadsheet', 'unsupported')
        return _annotate_legacy_options(read_sheet_bytes(data), compatibility, salvage_unknown_metadata)

    require(data.startswith(SIGNATURE), {'stage': 'container'},
            'Unknown container; native private ET formats unsupported', 'unsupported')
    compound = load_compound(data, compatibility)
    workbook = _select_workbook(compound, require,
                                {'stage': 'routing', 'stream': 'Workbook'})
    from wps_biff8 import read_container, normalize_result
    try:
        effective_chart = resolve_chart_policy(requested_chart, compatibility)
        result = read_container(compound, path=source, compatibility='data',
                                chart_policy=effective_chart,
                                salvage_unknown_metadata=salvage_unknown_metadata)
    except ValueError as exc:
        location = {'stage': 'BIFF', 'stream': workbook[0], 'compatibility_mode': 'data'}
        location.update(getattr(exc, 'location', {}))
        raise ParseError(getattr(exc, 'status', 'invalid_or_unsupported'), location, str(exc)) from exc
    return _annotate_legacy_options(normalize_result(result), compatibility, salvage_unknown_metadata)


def read_bytes(data, **options):
    """Read immutable bytes and add scope-limited confidence diagnostics."""
    from wps_diagnostics import summarize
    return summarize(_read_bytes(data, **options))


def read(path, **options):
    try:
        data = read_input(path)
        return read_bytes(data, source=str(path), **options)
    except (ParseError, OSError) as exc:
        from wps_diagnostics import summarize
        return summarize(failure(getattr(exc, 'status', 'io_error'),
                                 getattr(exc, 'location', {'stage': 'input'}), str(exc)))


def encode_result(result):
    from wps_result import encode_result as encode
    return encode(result, limit=MAX_JSON_OUTPUT_BYTES)


def check_environment():
    """Report import/runtime readiness; never read or accept a task document."""
    try:
        if sys.version_info < (3, 9):
            raise RuntimeError('Python >=3.9 required')
        import wps_biff8
        import wps_ooxml_sheet
        from xml.parsers import expat
    except (ImportError, OSError, RuntimeError) as exc:
        print('check_kind=environment artifact_validation=not_run', file=sys.stderr)
        print('environment-failed: ' + str(exc), file=sys.stderr, flush=True)
        return 2 if sys.version_info < (3, 9) else 1
    print('check_kind=environment artifact_validation=not_run')
    expat_ok = expat.version_info >= (2, 6, 0)
    print('ooxml-sheet=' + ('available' if expat_ok else 'unavailable: Expat >=2.6 required'))
    print('environment-ok', flush=True)
    return 0


def _validate_json_budget(value):
    if value is None:
        return MAX_JSON_OUTPUT_BYTES
    if not isinstance(value, int) or not 1024 * 1024 <= value <= MAX_JSON_OUTPUT_BYTES_LIMIT:
        raise ParseError('unsupported', {'stage': 'options'},
                         'max-json-bytes must be 1048576..268435456')
    return value


def _manifest_payload(result, manifest, compacted):
    payload = {
        'schema_version': 'et_output_manifest_v1',
        'status': result.get('status'),
        'partial': bool(result.get('partial')),
        'source_schema_version': result.get('schema_version'),
        'cell_value_coverage': result.get('cell_value_coverage'),
        'omissions_affect_cell_values': result.get('omissions_affect_cell_values'),
        'output': manifest,
        'compacted': bool(compacted),
        'note': 'Complete JSON was written to a new task-root file; stdout only carries this manifest.',
    }
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


def _full_output(result, args):
    """Write or encode a full result; compact once before failing a budget."""
    limit = _validate_json_budget(args.max_json_bytes)
    if args.compact:
        result = compact_result(result)
    if args.output:
        output_limit = limit if args.max_json_bytes is not None else MAX_JSON_OUTPUT_BYTES_LIMIT
        try:
            manifest = write_result_file(args.output, result, limit=output_limit)
        except ParseError as exc:
            if exc.status != 'resource_limit' or args.compact:
                raise
            result = compact_result(result)
            result.setdefault('issues', []).append({
                'code': 'output_auto_compacted',
                'message': 'Written output exceeded the JSON budget; provenance-heavy fields were compacted once.'})
            if result.get('status') == 'ok':
                result['status'] = 'partial'
                result['partial'] = True
            from wps_diagnostics import summarize
            summarize(result)
            args.compact = True
            manifest = write_result_file(args.output, result, limit=output_limit)
        return _manifest_payload(result, manifest, args.compact)
    try:
        encoded = _encode_result(result, limit=limit)
    except ParseError as exc:
        if exc.status != 'resource_limit' or args.compact:
            raise
        result = compact_result(result)
        result.setdefault('issues', []).append({
            'code': 'output_auto_compacted',
            'message': 'Full output exceeded the JSON budget; provenance-heavy fields were compacted once '
                       'before delivery. Saved values, types, cache status and diagnostics are retained.'})
        if result.get('status') == 'ok':
            result['status'] = 'partial'
            result['partial'] = True
        from wps_diagnostics import summarize
        summarize(result)
        encoded = _encode_result(result, limit=limit)
        args.compact = True
    return encoded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?')
    parser.add_argument('--format', choices=('json',), default='json')
    parser.add_argument('--compatibility', choices=('data', 'strict', 'legacy_excel_clsid'),
                        default='data', help='Deprecated no-op; all values use the tolerant reader')
    parser.add_argument('--salvage-unknown-metadata', action='store_true',
                        help='Deprecated no-op; best-effort retention is the default')
    parser.add_argument('--chart-policy', choices=('reject', 'opaque'), default=None,
                        help='Deprecated no-op; charts are always retained as bounded opaque bytes')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--include-related', action='store_true')
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--sheet')
    parser.add_argument('--page-size', type=int)
    parser.add_argument('--cursor')
    parser.add_argument('--max-output-chars', type=int)
    parser.add_argument('--max-output-bytes', type=int)
    parser.add_argument('--max-json-bytes', type=int,
                        help='Full JSON budget in bytes, 1048576..268435456 (default 64 MiB)')
    parser.add_argument('--compact', action='store_true',
                        help='Drop provenance-heavy per-cell fields while keeping saved values and diagnostics')
    parser.add_argument('--output', help='Write the complete JSON to a new task-root file; stdout returns a manifest')
    args = parser.parse_args()

    if args.check:
        if any((args.summary, args.sheet is not None, args.page_size is not None,
                args.cursor is not None, args.max_output_chars is not None, args.max_output_bytes is not None,
                args.max_json_bytes is not None, args.compact, args.output is not None)):
            parser.error('--check does not accept read/output options')
        return check_environment()

    if sys.version_info < (3, 9):
        parser.error('Python >=3.9 required')
    if args.max_json_bytes is not None and (not 1024 * 1024 <= args.max_json_bytes <= MAX_JSON_OUTPUT_BYTES_LIMIT):
        parser.error('--max-json-bytes must be 1048576..268435456')
    if not args.input:
        parser.error('input required')

    view_requested = any((args.summary, args.sheet is not None, args.page_size is not None,
                         args.cursor is not None, args.max_output_chars is not None, args.max_output_bytes is not None))
    if view_requested and (args.format != 'json' or args.include_related):
        parser.error('Read views require JSON and main content only; use full reading for related stories')
    if view_requested and args.output:
        parser.error('--output applies to the full read; bounded views are printed to stdout')
    if args.include_related:
        parser.error('--include-related does not apply to spreadsheets')
    if args.output and (not isinstance(args.output, str) or not args.output or args.output in ('.', '..')
                        or any(char in args.output for char in ('/', '\\', '\x00'))):
        parser.error('--output must be a bare filename in the current task directory')
    options = dict(compatibility=args.compatibility, chart_policy=args.chart_policy,
                   salvage_unknown_metadata=args.salvage_unknown_metadata)

    if view_requested:
        from et_read_view import project
        try:
            data = read_input(args.input)
            result = read_bytes(data, source=str(args.input), **options)
            result = project(result, data, options, summary=args.summary, sheet=args.sheet,
                             page_size=200 if args.page_size is None else args.page_size, cursor=args.cursor,
                             max_chars=65536 if args.max_output_chars is None else args.max_output_chars,
                             max_bytes=args.max_output_bytes)
        except (ParseError, OSError) as exc:
            result = failure(getattr(exc, 'status', 'io_error'),
                             getattr(exc, 'location', {'stage': 'input'}), str(exc))
        from wps_read_view import encoded as encode_view
        encoded = encode_view(result)
        if args.max_output_bytes is not None and args.max_output_bytes >= 512:
            from wps_read_view import check_serialized_bytes
            try:
                check_serialized_bytes(encoded, args.max_output_bytes)
            except ParseError as exc:
                result = failure(exc.status, exc.location, str(exc))
                encoded = json.dumps(result, ensure_ascii=False)
        from wps_read_view import print_json_record
        print_json_record(encoded, max_bytes=args.max_output_bytes)
        return 0 if result.get('status') == 'ok' else 3 if result.get('status') == 'partial' else 1

    try:
        result = read(args.input, **options)
    except (ParseError, OSError) as exc:
        result = failure(getattr(exc, 'status', 'io_error'),
                         getattr(exc, 'location', {'stage': 'input'}), str(exc))
    if result.get('status') not in ('ok', 'partial'):
        encoded = _encode_result(result, limit=1024 * 1024)
        from wps_read_view import print_json_record
        print_json_record(encoded)
        return 1
    try:
        encoded = _full_output(result, args)
    except (ParseError, OSError) as exc:
        result = failure(getattr(exc, 'status', 'resource_limit'),
                         getattr(exc, 'location', {'stage': 'output'}), str(exc))
        encoded = json.dumps(result, ensure_ascii=False)
        from wps_read_view import print_json_record
        print_json_record(encoded)
        return 1
    from wps_read_view import print_json_record
    print_json_record(encoded)
    return 0 if result.get('status') == 'ok' else 3 if result.get('status') == 'partial' else 1


if __name__ == '__main__':
    sys.exit(main())
