"""Bounded, content-identified WPS-family document and spreadsheet extraction."""
import argparse
import json
import zipfile
import zlib
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wps_cfb import CompoundFile, MAX_FILE_BYTES, MAX_STREAM_BYTES, SIGNATURE
from wps_access import ParseError, read_input, load_compound, resolve_chart_policy
from wps_binary_doc import (
    CLX_PAIR_INDEX,
    COMPRESSED_MAPPING,
    FC_LCB_START,
    FIB_LAYOUTS,
    MAX_CLX_BYTES,
    MAX_FIELD_DEPTH,
    MAX_FIELDS,
    MAX_OBJECT_MARKERS,
    MAX_PARAGRAPHS,
    MAX_PIECES,
    MAX_PRC_RECORDS,
    MAX_TEXTBOX_COPY_UNITS,
    MAX_UNITS,
    TEXTBOX_STORIES,
    Region,
    parse_streams,
    require,
)


def failure(status, location, message):
    from wps_access import diagnostic_context
    return {'status': status, 'location': location, 'message': message, 'diagnostic_context': diagnostic_context(status, location),
            'partial': False, 'text': None, 'producer_verified': False}


def _read_bytes(data, *, source=None, compatibility='data', chart_policy=None,
               field_policy=None, object_policy=None, revisions=None, include_related=False,
               hidden_text="include", binary_paragraph_sources=False, text_policy=None,
               reading_mode='content', include_image_bytes=False):
    """Identify content first; the suffix is provenance, never a format selector."""
    require(isinstance(data, bytes) and len(data) <= MAX_FILE_BYTES,
            {'stage': 'input'}, 'Input exceeds byte budget or is not bytes', 'resource_limit')
    require(compatibility in ('strict', 'legacy_excel_clsid', 'legacy_word_clsid', 'data'), {'stage': 'options'},
            'Unknown compatibility mode', 'unsupported')
    require(type(binary_paragraph_sources) is bool, {'stage': 'options'},
            'binary_paragraph_sources must be boolean', 'unsupported')
    require(text_policy in (None, 'strict', 'recover'), {'stage': 'options'}, 'Unknown binary text policy', 'unsupported')
    require(reading_mode in ('standard', 'content', 'maximum'), {'stage': 'options'},
            'Unknown reading mode', 'unsupported')
    require(revisions in (None, 'accepted', 'original', 'all'), {'stage': 'options'},
            'Unknown revisions policy', 'unsupported')
    maximum = reading_mode != 'standard'
    require(type(include_image_bytes) is bool and (not include_image_bytes or reading_mode == 'maximum'),
            {'stage': 'options'}, 'Image bytes require maximum reading', 'unsupported')
    if maximum:
        require(compatibility == 'data' and chart_policy in (None, 'reject'), {'stage': 'options'},
                'Maximum reading requires data compatibility without chart recovery', 'unsupported')
        require(field_policy in (None, 'cached_result') and object_policy in (None, 'placeholder'),
                {'stage': 'options'}, 'Maximum reading retains cached fields and object placeholders', 'unsupported')
        require(revisions in (None, 'accepted', 'all') and hidden_text == 'include' and text_policy in (None, 'recover'),
                {'stage': 'options'}, 'Maximum reading retains hidden text with accepted/all revision views and bounded recovery',
                'unsupported')
        include_related = True
        text_policy = 'recover'
        field_policy = 'cached_result'
        object_policy = 'placeholder'
    requested_field_policy, requested_object_policy = field_policy, object_policy
    field_policy = 'reject' if field_policy is None else field_policy
    object_policy = 'reject' if object_policy is None else object_policy
    requested_chart_policy = chart_policy
    chart_policy = resolve_chart_policy(chart_policy, 'strict')

    def finish(result):
        if maximum:
            from wps_maximum_view import complete_maximum
            result = complete_maximum(data, result, revisions=revisions, reading_mode=reading_mode)
        return result
    require(not include_image_bytes or data.startswith(SIGNATURE), {'stage': 'options'},
            'Image bytes are available only for binary MS-DOC', 'unsupported')
    if data.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        require(text_policy is None or maximum, {'stage': 'options'},
                'Binary text policy does not apply to OOXML outside maximum reading', 'unsupported')
        require(not binary_paragraph_sources, {'stage': 'options'},
                'Binary paragraph sources do not apply to OOXML', 'unsupported')
        require(compatibility in ('strict', 'data') and chart_policy == 'reject'
                and (maximum or field_policy == 'reject' and object_policy == 'reject'), {'stage': 'options'},
                'Binary compatibility/field/chart options do not apply to OOXML', 'unsupported')
        from wps_ooxml import package_kind, read_bytes as read_ooxml_bytes
        try:
            family = package_kind(data)
        except (ValueError, KeyError, zipfile.BadZipFile, zlib.error) as exc:
            raise ParseError('invalid_or_unsupported', {'stage': 'OOXML package'}, str(exc)) from exc
        if family == 'spreadsheet':
            raise ParseError('unsupported', {'stage': 'OOXML package'},
                             'Spreadsheets (.et/.ett) use the et skill; locate its actual SKILL.md and resolve scripts from that skill root.')
        return finish(read_ooxml_bytes(data, revisions=revisions or 'accepted', include_related=include_related,
                                       hidden_text=hidden_text, recover_textboxes=maximum))
    if data.lstrip().startswith(b'{\\rtf'):
        require(hidden_text in ('include', 'exclude', 'annotate'), {'stage': 'options'},
                'Unknown RTF hidden-text policy', 'unsupported')
        require(not binary_paragraph_sources and text_policy in (None, 'recover'),
                {'stage': 'options'}, 'Requested option does not apply to RTF', 'unsupported')
        require(compatibility == 'data' and requested_chart_policy is None,
                {'stage': 'options'}, 'Requested compatibility or chart policy does not apply to RTF', 'unsupported')
        require(requested_object_policy in (None, 'reject', 'placeholder'), {'stage': 'options'},
                'Unknown RTF object policy', 'unsupported')
        require(requested_field_policy in (None, 'reject', 'cached_result'), {'stage': 'options'},
                'Unknown RTF field policy', 'unsupported')
        from wps_rtf_read import parse_rtf_bytes
        try:
            if maximum:
                from wps_maximum_view import read_rtf_maximum
                result = read_rtf_maximum(data, revisions)
            else:
                result = parse_rtf_bytes(data, hidden_text=hidden_text, revisions=revisions,
                                         object_policy=requested_object_policy or 'placeholder',
                                         include_related=include_related)
        except ParseError:
            raise
        except ValueError as exc:
            raise ParseError('corrupt', {'stage': 'RTF stream'}, str(exc)) from exc
        require(requested_field_policy != 'reject' or not result.get('field_count'), {'stage': 'RTF stream'},
                'RTF fields found; field-policy reject does not permit cached results', 'unsupported')
        return finish(result)
    require(data.startswith(SIGNATURE), {'stage': 'container'},
            'Unknown container; native private WPS/Works/ET formats unsupported', 'unsupported')
    require(hidden_text in ('include', 'annotate'), {'stage': 'options'}, 'Binary visibility is unknown; exclude unsupported', 'unsupported')
    compound = load_compound(data, compatibility, skip_large_optional=maximum)
    word = ('WordDocument',) in compound.streams
    workbooks = [path for path in compound.streams if len(path) == 1 and path[0].casefold() in ('workbook', 'book')]
    require(not (word and workbooks), {'stage': 'routing'},
            'Ambiguous document and workbook payloads; no branch chosen', 'unsupported')
    if word:
        require(compatibility in ('strict', 'legacy_word_clsid', 'data') and chart_policy == 'reject', {'stage': 'options'},
                'BIFF compatibility/chart options do not apply to MS-DOC', 'unsupported')
        require(revisions in (None, 'all'), {'stage': 'options'},
                'MS-DOC reads stored text; revision filtering unsupported', 'unsupported')
        effective_text_policy = text_policy or ('recover' if compatibility == 'data' else 'strict')
        if effective_text_policy == 'recover':
            field_policy = 'cached_result' if requested_field_policy is None else field_policy
            object_policy = 'placeholder' if requested_object_policy is None else object_policy
        result = parse_streams(compound.streams, field_policy=field_policy, object_policy=object_policy,
                               include_related=include_related, hidden_text=hidden_text,
                               binary_paragraph_sources=binary_paragraph_sources, text_policy=effective_text_policy,
                               maximum=maximum, include_image_bytes=include_image_bytes, content_only=reading_mode == 'content')
        result['compatibility_mode'] = compatibility
        result['container_compatibility'] = compound.compatibility
        result['container_issues'] = compound.issues
        if compound.issues:
            result['issues'] = result.get('issues', []) + compound.issues
            result['status'] = 'partial'
            result['partial'] = True
        return finish(result)
    if workbooks:
        raise ParseError('unsupported', {'stage': 'routing'},
                         'Spreadsheets (.et/.ett) use the et skill; locate its actual SKILL.md and resolve scripts from that skill root.')
    raise ParseError('unsupported', {'stage': 'routing'},
                     'Require WordDocument payload; no supported document found')


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


MAX_JSON_OUTPUT_BYTES = 16 * 1024 * 1024


def encode_result(result):
    # Retain the public limit and function used by existing callers/tests.
    from wps_result import encode_result as encode
    return encode(result, limit=MAX_JSON_OUTPUT_BYTES)


def text_output(result):
    """Render RTF's stored block order; keep the legacy JSON text field intact."""
    if result.get('format') != 'rtf' or not isinstance(result.get('blocks'), list):
        return result['text']
    chunks = []
    for block in result['blocks']:
        if block['type'] == 'paragraph':
            chunks.append(result['paragraphs'][block['index']]['text'])
        elif block['type'] == 'table':
            table = result['tables'][block['index']]
            chunks.extend('\t'.join(cell['text'] for cell in row) for row in table['rows'])
    return '\n'.join(chunks)


def check_environment():
    """Report import/runtime readiness; never read or accept a task document."""
    try:
        if sys.version_info < (3, 10):
            raise RuntimeError('Python >=3.10 required')
        import wps_cfb
        import wps_ooxml
        from xml.parsers import expat
        from wps_task_paths import TaskPaths
        access = TaskPaths()
        os.close(access.descriptor)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print('check_kind=environment artifact_validation=not_run', file=sys.stderr)
        print('environment-failed: ' + str(exc), file=sys.stderr, flush=True)
        return 2 if sys.version_info < (3, 10) else 1
    print('check_kind=environment artifact_validation=not_run')
    print('ooxml-word=' + ('available' if expat.version_info >= (2, 6, 0)
                          else 'unavailable: Expat >=2.6 required'))
    print('ms-doc-chart-cache=' + ('available' if expat.version_info >= (2, 6, 0)
                                  else 'unavailable: Expat >=2.6 required'))
    print('environment-ok', flush=True)
    return 0


from wps_task_paths import cli_boundary, validate_argument


@cli_boundary
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?')
    parser.add_argument('--profile', choices=('content', 'full'), help='Content-focused output (default); full retains machine structure')
    parser.add_argument('--format', choices=('json', 'text'), default='json')
    parser.add_argument('--reading-mode', choices=('standard', 'content', 'maximum'), default='content')
    parser.add_argument('--include-image-bytes', action='store_true', help='Include bounded base64 PNG bytes from binary MS-DOC in maximum mode')
    parser.add_argument('--compatibility', choices=('data', 'strict', 'legacy_excel_clsid', 'legacy_word_clsid'), default='data')
    parser.add_argument('--chart-policy', choices=('reject', 'opaque'), default=None)
    parser.add_argument('--object-policy', choices=('reject', 'placeholder'), default=None)
    parser.add_argument('--field-policy', choices=('reject', 'cached_result'), default=None)
    parser.add_argument('--text-policy', choices=('strict', 'recover'), default=None)
    parser.add_argument('--revisions', choices=('accepted', 'original', 'all'))
    parser.add_argument('--include-related', action='store_true')
    parser.add_argument('--binary-paragraph-sources', action='store_true')
    parser.add_argument('--hidden-text', choices=('include','exclude','annotate'), default='include')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--summary', action='store_true')
    parser.add_argument('--heading-only', action='store_true')
    parser.add_argument('--paragraph-range', type=str)
    parser.add_argument('--section', type=str)
    parser.add_argument('--section-index', type=int, help='1-based paragraph index disambiguating a matching section heading')
    parser.add_argument('--sheet')
    parser.add_argument('--page-size', type=int)
    parser.add_argument('--cursor')
    parser.add_argument('--max-output-chars', type=int)
    parser.add_argument('--max-output-bytes', type=int)
    args = parser.parse_args()
    from wps_content_view import configure, project_content
    configure(args, parser)
    if args.include_image_bytes and (args.reading_mode != 'maximum' or args.check):
        parser.error('--include-image-bytes requires --reading-mode maximum and a binary document')
    if args.reading_mode != 'standard' and args.format != 'json':
        parser.error('Full content reading requires JSON to preserve separate recovered regions')
    if args.include_related and args.format == 'text':
        parser.error('--include-related requires --format json to preserve separate story regions')
    if args.binary_paragraph_sources and args.format == 'text':
        parser.error('--binary-paragraph-sources requires --format json to retain provenance ranges')
    if args.section_index is not None and (args.section is None or args.section_index < 1):
        parser.error('--section-index requires --section and a positive paragraph index')
    maximum_projection = any((args.summary, args.heading_only, args.paragraph_range is not None,
                              args.section is not None, args.sheet is not None))
    if args.reading_mode != 'standard' and maximum_projection:
        parser.error('Full content pages preserve all regions; summary/heading/paragraph/section/sheet projections require explicit standard')
    if args.check:
        if any((args.summary, args.heading_only, args.paragraph_range is not None, args.section is not None,
                args.sheet is not None, args.page_size is not None, args.cursor is not None,
                args.max_output_chars is not None, args.max_output_bytes is not None)):
            parser.error('--check does not accept read-view options')
        return check_environment()
    if sys.version_info < (3, 10):
        parser.error('Python >=3.10 required')
    if not args.input:
        parser.error('input required')
    validate_argument(args.input)
    view_requested = any((args.summary, args.sheet is not None, args.page_size is not None, args.cursor is not None, args.max_output_chars is not None, args.max_output_bytes is not None))
    selective_count = sum((args.heading_only, args.paragraph_range is not None, args.section is not None))
    if selective_count > 1 or (selective_count and view_requested):
        parser.error('Choose one selective mode; do not combine selective reading with summary or paged read views')
    if view_requested and (args.format != 'json' or args.reading_mode == 'standard' and (args.include_related or args.binary_paragraph_sources)):
        parser.error('Read views require JSON and main content only; use full reading for related stories or paragraph sources')
    options = dict(compatibility=args.compatibility, chart_policy=args.chart_policy, text_policy=args.text_policy,
                   field_policy=args.field_policy, object_policy=args.object_policy, revisions=args.revisions,
                   include_related=args.include_related, hidden_text=args.hidden_text,
                   binary_paragraph_sources=args.binary_paragraph_sources)
    options['reading_mode'] = args.reading_mode
    if args.include_image_bytes:
        options['include_image_bytes'] = True
    if view_requested:
        from wps_read_view import project
        try:
            data = read_input(args.input)
            result = read_bytes(data, source=str(args.input), **options)
            if args.reading_mode != 'standard':
                from wps_maximum_pages import project as project_maximum
                result = project_maximum(project_content(result) if args.profile == 'content' else result, data, {**options, 'profile': args.profile},
                         page_size=8000 if args.page_size is None else args.page_size, cursor=args.cursor,
                         max_chars=12000 if args.max_output_chars is None else args.max_output_chars,
                         max_bytes=12000 if args.max_output_bytes is None else args.max_output_bytes)
            else:
                result = project(result, data, options, summary=args.summary, sheet=args.sheet,
                                 page_size=200 if args.page_size is None else args.page_size, cursor=args.cursor,
                                 max_chars=12000 if args.max_output_chars is None else args.max_output_chars, max_bytes=args.max_output_bytes)
        except (ParseError, OSError) as exc:
            result = failure(getattr(exc, 'status', 'io_error'), getattr(exc, 'location', {'stage': 'input'}), str(exc))
    else:
        result = read(args.input, **options)
    if not view_requested and args.profile == 'content':
        result = project_content(result)
    selective_requested = any((args.heading_only, args.paragraph_range is not None, args.section is not None))
    if selective_requested and result.get('status') in ('ok', 'partial'):
        from wps_selective import project_headings, project_paragraph_range, project_section
        try:
            if args.heading_only:
                result = project_headings(result)
            elif args.paragraph_range is not None:
                result = project_paragraph_range(result, args.paragraph_range)
            elif args.section is not None:
                result = project_section(result, args.section, args.section_index)
        except ValueError as exc:
            result = failure('invalid_argument', {'stage': 'selective_reading'}, str(exc))
    if args.format == 'text' and result['status'] in ('ok', 'partial') and 'text' not in result:
        result = failure('unsupported', {'stage': 'output'}, 'Spreadsheet extraction requires JSON output')
    if args.format == 'text' and result['status'] in ('ok', 'partial'):
        print(text_output(result), end='')
        if result['status'] == 'partial':
            print(json.dumps({'status': 'partial', 'issues': result.get('issues', result.get('diagnostics', []))}, ensure_ascii=False), file=sys.stderr)
    else:
        try:
            if result.get('schema_version') in ('wps_read_view_v1', 'wps_maximum_page_v1'):
                from wps_read_view import encoded as encode_view
                encoded = encode_view(result)
            else:
                encoded = encode_result(result)
        except ParseError as exc:
            result = failure(exc.status, exc.location, str(exc))
            encoded = json.dumps(result, ensure_ascii=False)
        if args.max_output_bytes is not None and args.max_output_bytes >= 512:
            from wps_read_view import check_serialized_bytes
            try:
                check_serialized_bytes(encoded, args.max_output_bytes)
            except ParseError as exc:
                result = failure(exc.status, exc.location, str(exc))
                encoded = json.dumps(result, ensure_ascii=False)
        from wps_read_view import print_json_record
        print_json_record(encoded, max_bytes=args.max_output_bytes or (12000 if result.get('schema_version') == 'wps_maximum_page_v1' else None),
                          stream=sys.stderr if args.format == 'text' else sys.stdout)
    return 0 if result['status'] == 'ok' else 3 if result['status'] == 'partial' else 1


if __name__ == '__main__':
    sys.exit(main())
