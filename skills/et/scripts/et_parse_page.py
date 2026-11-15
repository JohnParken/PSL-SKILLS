"""Parse-time OOXML cell paging; full safety scans and legacy parsing are retained."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wps_access import ParseError, read_input
from wps_cfb import MAX_FILE_BYTES
from wps_ooxml_sheet import read_bytes as read_sheet_bytes
from wps_read_view import encoded
from read_et import check_environment, failure

SCHEMA = 'et_parse_page_v1'
REVISION = 1
INHERITED_STYLE_FALLBACK = 'Unsupported parse-page optimization: row/column inherited styles'


class CellWindow(dict):
    """Keep duplicate identities for all readable cells, but only page payloads."""

    def __init__(self, window, name):
        super().__init__()
        self.seen = set()
        self.window = window
        self.name = name
        self.total = 0

    def __contains__(self, ref):
        return ref in self.seen

    def __setitem__(self, ref, item):
        self.seen.add(ref)
        self.total += 1
        self.window.source_total += 1
        if self.window.sheet is not None and self.name != self.window.sheet:
            return
        index = self.window.total
        self.window.total += 1
        if self.window.start <= index < self.window.start + self.window.size:
            super().__setitem__(ref, item)


class ParseWindow:
    """Per-call state, never shared across requests or persisted to disk."""

    def __init__(self, start, size, sheet):
        self.start = start
        self.size = size
        self.sheet = sheet
        self.total = 0
        self.source_total = 0

    def create(self, name):
        return CellWindow(self, name)


def page_error(message):
    """Reject invalid page state without returning a successful empty page."""
    raise ParseError('output_view_error', {'stage': 'parse_page'}, message)



def bounded_result(result, max_bytes):
    """Apply the same complete UTF-8 record budget to success and failure."""
    if len((encoded(result) + '\n').encode('utf-8')) > max_bytes:
        page_error('Complete result and diagnostics exceed max-output-bytes; increase budget or restart with a smaller page-size')
    return result


def read_page(data, *, sheet=None, page_size=100, cursor=None, max_bytes=1048576):
    """Read a cell page in stored order; every call scans the same complete input."""
    if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES:
        raise ParseError('resource_limit', {'stage': 'input'}, 'Input exceeds byte budget or is not bytes')
    if not data.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        raise ParseError('unsupported', {'stage': 'container'}, 'Parse pages support OOXML spreadsheets only; use read_et for other formats')
    if type(page_size) is not int or not 1 <= page_size <= 10000:
        page_error('page-size must be 1..10000')
    if type(max_bytes) is not int or not 512 <= max_bytes <= 67108864:
        page_error('max-output-bytes must be 512..67108864')
    if sheet is not None and (not isinstance(sheet, str) or not 1 <= len(sheet) <= 31):
        page_error('sheet must be a nonempty exact worksheet name of at most 31 characters')
    digest = hashlib.sha256(data).hexdigest()
    binding = hashlib.sha256(encoded([SCHEMA, REVISION, sheet, page_size]).encode('utf-8')).hexdigest()
    start = 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 1024:
                raise ValueError()
            token = json.loads(base64.b64decode(cursor.encode('ascii'), altchars=b'-_', validate=True))
            if (not isinstance(token, list) or len(token) != 3 or token[:2] != [digest, binding]
                    or type(token[2]) is not int or token[2] < 0):
                raise ValueError()
            start = token[2]
        except (ValueError, TypeError, UnicodeError):
            page_error('Invalid cursor or changed source/options/revision; restart the read')
    window = ParseWindow(start, page_size, sheet)
    result = read_sheet_bytes(data, _cell_store_factory=window.create)
    mode = 'parse_time_cell_window'
    fallback_reason = None
    if result.get('message') == INHERITED_STYLE_FALLBACK:
        # Release partial stores before allocating the legacy full result.
        result = read_sheet_bytes(data)
        mode = 'full_parse_fallback'
        fallback_reason = 'row_column_style_inheritance_requires_full_cells'
    if result.get('status') not in ('ok', 'partial'):
        return bounded_result(result, max_bytes)
    sheets = result['sheets']
    if sheet is not None and not any(item['name'] == sheet for item in sheets):
        page_error('Unknown worksheet')
    if mode == 'full_parse_fallback':
        total = sum(len(entry['cells']) for entry in sheets if sheet is None or entry['name'] == sheet)
        items = []
        index = 0
        for entry in sheets:
            if sheet is not None and entry['name'] != sheet:
                continue
            for ref, item in entry['cells'].items():
                if start <= index < start + page_size:
                    items.append({'sheet': entry['name'], 'cell': ref, 'data': item})
                index += 1
        source_count = result['cell_count']
    else:
        total = window.total
        source_count = window.source_total
        items = [{'sheet': entry['name'], 'cell': ref, 'data': item}
                 for entry in sheets for ref, item in entry['cells'].items()]
    if start > total or (cursor is not None and start == total):
        page_error('Cursor is outside the selected stored-cell sequence')
    end = start + len(items)
    next_cursor = None
    if end < total:
        next_cursor = base64.urlsafe_b64encode(encoded([digest, binding, end]).encode('utf-8')).decode('ascii')
    retained = ('status', 'partial', 'issues', 'issues_truncated', 'cell_value_coverage',
                'omissions_affect_cell_values', 'text_budget', 'date_system', 'conformance',
                'formulas_executed', 'external_links_followed', 'producer_verified')
    output = {key: result[key] for key in retained if key in result}
    output.update({
        'schema_version': SCHEMA, 'revision': REVISION,
        'source_schema_version': result['schema_version'], 'source_sha256': digest,
        'parse_mode': mode, 'fallback_reason': fallback_reason,
        'order': 'declared_sheet_then_first_readable_cell_storage_order',
        'sheet': sheet, 'page_size': page_size, 'start': start,
        'total_items': total, 'source_cell_count': source_count,
        'returned_items': len(items), 'items': items,
        'next_cursor': next_cursor, 'has_more': next_cursor is not None,
        'coverage': 'selected_cell_page_not_full_workbook',
        'source_coverage_scope': 'all_declared_worksheets_with_original_parser_limits',
        'metadata_omitted': [key for key in result if key not in retained],
        'memory_scope': ('full_cell_payloads_retained; no_parse_window_memory_optimization'
                         if mode == 'full_parse_fallback' else
                         'page_cell_payloads_only; input_bytes_SST_row_metadata_and_duplicate_index_remain_bounded'),
    })
    # No partial page, silently shortened cell or diagnostic summary on overflow.
    return bounded_result(output, max_bytes)


def main():
    """Read-only standalone CLI; JSON stdout, no cache, no output files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--capabilities', action='store_true')
    parser.add_argument('--sheet')
    parser.add_argument('--page-size', type=int, default=100)
    parser.add_argument('--cursor')
    parser.add_argument('--max-output-bytes', type=int, default=1048576)
    args = parser.parse_args()
    if args.capabilities:
        print(encoded({'schema_version': SCHEMA, 'revision': REVISION, 'format': 'ooxml-spreadsheet',
                       'order': 'storage', 'inherited_styles': 'full_parse_fallback'}))
        return 0
    if args.check:
        return check_environment()
    if args.input is None:
        parser.error('input is required')
    try:
        output = read_page(read_input(args.input), sheet=args.sheet, page_size=args.page_size,
                           cursor=args.cursor, max_bytes=args.max_output_bytes)
    except (ParseError, OSError) as exc:
        output = failure(getattr(exc, 'status', 'io_error'),
                         getattr(exc, 'location', {'stage': 'input'}), str(exc))
    text = encoded(output)
    if len((text + '\n').encode('utf-8')) > max(512, args.max_output_bytes):
        text = encoded({'status': 'output_view_error', 'partial': False,
                        'message': 'Failure diagnostics exceed output budget'})
    sys.stdout.buffer.write(text.encode('utf-8') + b'\n')
    return 0 if output.get('status') == 'ok' else 3 if output.get('status') == 'partial' else 1


if __name__ == '__main__':
    raise SystemExit(main())
