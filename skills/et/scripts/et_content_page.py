"""Bounded, lossless pages of the content projection, not parser-time windows."""
import base64
import hashlib
from itertools import islice
import json

from wps_read_view import encoded, fail, position, validate_bytes

PROJECTION_REVISION = 1
DEFAULT_PAGE_SIZE = 100
DEFAULT_OUTPUT_BUDGET = 32768


def sheet_overview(sheet):
    """Count saved cells, including compressed explicit blanks without expanding them."""
    blanks = sheet.get('stored_blank_cells', {}).get('count', 0)
    result = {'name': sheet['name'], 'stored_cells': len(sheet['cells']) + blanks,
              'returned_cell_records': len(sheet['cells']), 'stored_blank_cells': blanks,
              'charts': len(sheet.get('charts', []))}
    for key in ('hidden_state', 'read_status', 'coordinates_verified', 'coordinate_basis'):
        if key in sheet:
            result[key] = sheet[key]
    return result


def _contains_list(value):
    if isinstance(value, list):
        return True
    return isinstance(value, dict) and any(_contains_list(item) for item in value.values())


def _fragments(value, path=()):
    """Keep semantic objects together; split nested lists without clipping fields.

    Paths are within one chart, not XML paths. Empty lists/dicts remain explicit.
    Assigning each data value at its path reconstructs the saved content object.
    """
    if not value or not _contains_list(value):
        yield list(path), value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _fragments(item, (*path, index))
    else:
        for key, item in value.items():
            yield from _fragments(item, (*path, key))


def _records(sheets):
    for sheet in sheets:
        name = sheet['name']
        lists = ('cells', 'charts', 'merges', 'comments', 'hyperlinks', 'stored_blank_cells')
        metadata = {key: value for key, value in sheet.items() if key not in lists}
        metadata['content_counts'] = {key: len(sheet.get(key, [])) for key in ('charts', 'merges', 'comments', 'hyperlinks')}
        metadata['stored_blank_count'] = sheet.get('stored_blank_cells', {}).get('count', 0)
        yield {'kind': 'worksheet', 'sheet': name, 'data': metadata}
        for index, chart in enumerate(sheet.get('charts', [])):
            for path, value in _fragments(chart):
                yield {'kind': 'chart_fragment', 'sheet': name, 'chart_index': index, 'path': path, 'data': value}
        for field, kind in (('merges', 'merge'), ('comments', 'comment'), ('hyperlinks', 'hyperlink')):
            for index, value in enumerate(sheet.get(field, [])):
                yield {'kind': kind, 'sheet': name, 'index': index, 'data': value}
        for index, value in enumerate(sheet.get('stored_blank_cells', {}).get('ranges', [])):
            yield {'kind': 'blank_range', 'sheet': name, 'index': index, 'data': value}
        for ref in sorted(sheet['cells'], key=position):
            yield {'kind': 'cell', 'sheet': name, 'cell': ref, 'data': sheet['cells'][ref]}


def _read_cursor(cursor, digest, binding):
    if cursor is None:
        return 0
    try:
        if not isinstance(cursor, str) or len(cursor) > 1024:
            raise ValueError()
        token = json.loads(base64.b64decode(cursor.encode('ascii'), altchars=b'-_', validate=True))
        if (not isinstance(token, dict) or set(token) != {'source', 'scope', 'next', 'revision', 'schema'}
                or token['schema'] != 'et_content_page_v1' or type(token['revision']) is not int
                or token['revision'] != PROJECTION_REVISION or token['source'] != digest or token['scope'] != binding
                or type(token['next']) is not int or token['next'] < 0):
            raise ValueError()
        return token['next']
    except (ValueError, TypeError, UnicodeError, KeyError):
        fail('Invalid content-page cursor or source/options changed; restart with --content-page')
    return 0


def project(result, data, options, *, sheet=None, page_size=DEFAULT_PAGE_SIZE, cursor=None,
            max_chars=DEFAULT_OUTPUT_BUDGET, max_bytes=DEFAULT_OUTPUT_BUDGET, continuation_args=None):
    """Return a complete bounded JSON page; never claim source parsing is paged."""
    validate_bytes(max_bytes)
    if type(page_size) is not int or not 1 <= page_size <= 10000 or type(max_chars) is not int or not 512 <= max_chars <= 2097152:
        fail('page-size must be 1..10000; max-output-chars must be 512..2097152')
    if result.get('status') not in ('ok', 'partial'):
        return result
    if result.get('schema_version') != 'et_content_v1':
        fail('Content pages require the content profile')
    sheets = result.get('sheets', [])
    if sheet is not None and not any(item['name'] == sheet for item in sheets):
        fail('Unknown worksheet')
    selected = [item for item in sheets if sheet is None or item['name'] == sheet]
    digest = hashlib.sha256(data).hexdigest()
    scope = {'sheet': sheet, 'page_size': page_size, 'parse_options': options}
    binding = hashlib.sha256(encoded(scope).encode('utf-8')).hexdigest()
    start = _read_cursor(cursor, digest, binding)
    # Iterate rather than materializing another workbook-sized list of objects.
    total = sum(1 + len(item['cells']) + sum(len(item.get(key, [])) for key in ('merges', 'comments', 'hyperlinks'))
                + len(item.get('stored_blank_cells', {}).get('ranges', []))
                + sum(sum(1 for _ in _fragments(chart)) for chart in item.get('charts', [])) for item in selected)
    if start > total or (cursor is not None and start == total):
        fail('Cursor exceeds selected content or has no remaining records')
    view = {key: value for key, value in result.items() if key not in ('schema_version', 'sheets', 'source_schema_version')}
    view.update(schema_version='et_content_page_v1', source_schema_version='et_content_v1',
                original_schema_version=result.get('source_schema_version'), source_sha256=digest,
                projection_revision=PROJECTION_REVISION, scope=scope,
                parse_mode='full_parse_then_output_page', sheets=[sheet_overview(item) for item in sheets],
                coverage='selected_content_records_only_not_full_workbook_or_rendered_view',
                total_items=total, start=start, items=[],
                continuation='Repeat with --content-page --cursor NEXT_CURSOR and unchanged sheet/page-size/parse options; budgets may change.')

    def finish(end):
        more = end < total
        token = {'source': digest, 'scope': binding, 'next': end, 'revision': PROJECTION_REVISION, 'schema': 'et_content_page_v1'}
        view.update(returned_items=end - start, has_more=more, truncated=more,
                    next_cursor=base64.urlsafe_b64encode(encoded(token).encode('utf-8')).decode('ascii') if more else None)
        if continuation_args is not None:
            view['continuation_entrypoint'] = 'read_et.py'
            view['next_arguments'] = [*continuation_args, '--cursor', view['next_cursor']] if more else None

    def fits():
        text = encoded(view) + '\n'
        return len(text) <= max_chars and (max_bytes is None or len(text.encode('utf-8')) <= max_bytes)

    # Try complete tail first: removing a continuation token may make it fit.
    candidates = list(islice(_records(selected), start, min(total, start + page_size)))
    if total - start <= page_size:
        view['items'] = candidates
        finish(total)
        if fits():
            return view
    view['items'] = []
    finish(start)
    if not fits():
        fail('Content-page overview/diagnostics exceed budget; increase --max-output-chars/--max-output-bytes or use --output FILE')
    low, high = 0, len(candidates)
    while low < high:
        middle = (low + high + 1) // 2
        view['items'] = candidates[:middle]
        finish(start + middle)
        if fits():
            low = middle
        else:
            high = middle - 1
    if low == 0 and start < total:
        fail('Single content record exceeds budget; increase --max-output-chars/--max-output-bytes or use --output FILE; no value was clipped')
    view['items'] = candidates[:low]
    finish(start + low)
    return view
