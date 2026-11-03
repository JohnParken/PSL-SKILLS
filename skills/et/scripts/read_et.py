"""Bounded, content-identified WPS Spreadsheets (ET/ETT) extraction."""
import argparse
import json
import os
from pathlib import Path
import struct
import sys
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wps_cfb import CompoundFile, MAX_FILE_BYTES, MAX_STREAM_BYTES, SIGNATURE
from wps_access import ParseError, read_input, load_compound, resolve_chart_policy

MAX_JSON_OUTPUT_BYTES = 16 * 1024 * 1024


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


def _read_bytes(data, *, source=None, compatibility='data', chart_policy=None,
                requested_chart_policy=None, **ignored_kwargs):
    """Identify content first; suffix is provenance, never a format selector."""
    require(isinstance(data, bytes) and len(data) <= MAX_FILE_BYTES,
            {'stage': 'input'}, 'Input exceeds byte budget or is not bytes', 'resource_limit')
    require(compatibility in ('strict', 'legacy_excel_clsid', 'data'),
            {'stage': 'options'}, 'Unknown compatibility mode', 'unsupported')
    requested_chart = requested_chart_policy if requested_chart_policy is not None else chart_policy
    if data.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        from wps_ooxml_sheet import package_kind, read_bytes as read_sheet_bytes
        try:
            family = package_kind(data)
        except (ValueError, KeyError, zipfile.BadZipFile, zlib.error) as exc:
            raise ParseError('invalid_or_unsupported', {'stage': 'OOXML package'}, str(exc)) from exc
        require(family == 'spreadsheet', {'stage': 'OOXML package'},
                'Package is not a spreadsheet', 'unsupported')
        return read_sheet_bytes(data)

    require(data.startswith(SIGNATURE), {'stage': 'container'},
            'Unknown container; native private ET formats unsupported', 'unsupported')
    compound = load_compound(data, compatibility)
    workbooks = [p for p in compound.streams if len(p) == 1 and p[0].casefold() in ('workbook', 'book')]
    require(len(workbooks) == 1, {'stage': 'routing'},
            'Require exactly one supported workbook payload', 'unsupported')
    from wps_biff8 import read_container, normalize_result
    try:
        effective_chart = resolve_chart_policy(requested_chart, compatibility)
        result = read_container(compound, path=source, compatibility=compatibility,
                                chart_policy=effective_chart)
    except ValueError as exc:
        location = {'stage': 'BIFF', 'stream': workbooks[0][0], 'compatibility_mode': compatibility}
        location.update(getattr(exc, 'location', {}))
        raise ParseError(getattr(exc, 'status', 'invalid_or_unsupported'), location, str(exc)) from exc
    return normalize_result(result)


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?')
    parser.add_argument('--format', choices=('json',), default='json')
    parser.add_argument('--compatibility', choices=('data', 'strict', 'legacy_excel_clsid'), default='data')
    parser.add_argument('--chart-policy', choices=('reject', 'opaque'), default=None)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--include-related', action='store_true')
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--sheet')
    parser.add_argument('--page-size', type=int)
    parser.add_argument('--cursor')
    parser.add_argument('--max-output-chars', type=int)
    args = parser.parse_args()

    if args.check:
        if any((args.summary, args.sheet is not None, args.page_size is not None,
                args.cursor is not None, args.max_output_chars is not None)):
            parser.error('--check does not accept read-view options')
        return check_environment()

    if sys.version_info < (3, 9):
        parser.error('Python >=3.9 required')
    if not args.input:
        parser.error('input required')

    view_requested = any((args.summary, args.sheet is not None, args.page_size is not None,
                         args.cursor is not None, args.max_output_chars is not None))
    if view_requested and (args.format != 'json' or args.include_related):
        parser.error('Read views require JSON and main content only; use full reading for related stories or paragraph sources')
    if args.include_related:
        parser.error('--include-related does not apply to spreadsheets')
    options = dict(compatibility=args.compatibility, chart_policy=args.chart_policy)

    if view_requested:
        from wps_read_view import project
        try:
            data = read_input(args.input)
            result = read_bytes(data, source=str(args.input), **options)
            result = project(result, data, options, summary=args.summary, sheet=args.sheet,
                             page_size=200 if args.page_size is None else args.page_size, cursor=args.cursor,
                             max_chars=12000 if args.max_output_chars is None else args.max_output_chars)
        except (ParseError, OSError) as exc:
            result = failure(getattr(exc, 'status', 'io_error'),
                             getattr(exc, 'location', {'stage': 'input'}), str(exc))
    else:
        result = read(args.input, **options)

    try:
        if result.get('schema_version') == 'wps_read_view_v1':
            from wps_read_view import encoded as encode_view
            encoded = encode_view(result)
        else:
            encoded = encode_result(result)
    except ParseError as exc:
        result = failure(exc.status, exc.location, str(exc))
        encoded = json.dumps(result, ensure_ascii=False)
    print(encoded, file=sys.stdout)
    return 0 if result.get('status') == 'ok' else 3 if result.get('status') == 'partial' else 1


if __name__ == '__main__':
    sys.exit(main())
