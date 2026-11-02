"""Explicit bounded output projections; parser outcomes and diagnostics stay intact."""
import base64
import hashlib
import json
import re

from wps_access import ParseError


def encoded(result):
    return json.dumps(result, ensure_ascii=False, default=str, allow_nan=False, separators=(',', ':'))


def fail(message):
    raise ParseError('output_view_error', {'stage': 'output_view'}, message)


def position(ref):
    match = re.fullmatch(r'([A-Z]+)([1-9][0-9]*)', ref)
    if not match:
        fail('Unexpected parsed cell coordinate')
    column = 0
    for char in match[1]:
        column = column * 26 + ord(char) - 64
    return int(match[2]), column


def project(result, data, options, *, summary=False, sheet=None, page_size=200, cursor=None, max_chars=12000):
    """Project already parsed immutable bytes, without changing source completeness."""
    if not 1 <= page_size <= 10000 or not 512 <= max_chars <= 2 * 1024 * 1024:
        fail('page-size must be 1..10000; max-output-chars must be 512..2097152')
    if summary and cursor:
        fail('summary does not accept cursor')
    if result.get('status') not in ('ok', 'partial'):
        return result
    sheets = result.get('sheets')
    is_sheet = isinstance(sheets, list)
    if sheet is not None and (not is_sheet or not any(item['name'] == sheet for item in sheets)):
        fail('Unknown worksheet or text document has no worksheets')
    digest = hashlib.sha256(data).hexdigest()
    scope = {'sheet': sheet, 'page_size': page_size, 'parse_options': options}
    binding = hashlib.sha256(encoded(scope).encode()).hexdigest()
    start = 0
    if cursor is not None:
        try:
            if len(cursor) > 1024:
                raise ValueError()
            token = json.loads(base64.b64decode(cursor.encode('ascii'), altchars=b'-_', validate=True))
            if set(token) != {'source', 'scope', 'next'} or token['source'] != digest or token['scope'] != binding:
                raise ValueError()
            start = token['next']
            if type(start) is not int or start < 0:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, KeyError):
            fail('Invalid cursor or source/options changed; restart the read')
    retained = ('status', 'partial', 'issues', 'diagnostics', 'reading_assessment', 'date_system',
                'cell_value_coverage', 'omissions_affect_cell_values', 'producer_verified', 'format')
    view = {key: result[key] for key in retained if key in result}
    view.update(schema_version='wps_read_view_v1', source_schema_version=result.get('schema_version'),
                source_sha256=digest, scope=scope, mode='summary' if summary else 'cells' if is_sheet else 'main_text',
                metadata_omitted=[key for key in result if key not in retained and key not in ('schema_version', 'sheets', 'text')])
    # Omitted metadata is named, never substituted with guessed defaults. These views
    # deliberately cannot be consumed by the existing full-schema XLSX adapter.
    if is_sheet:
        selected = [item for item in sheets if sheet is None or item['name'] == sheet]
        view['sheets'] = [{'name': item['name'], 'stored_cells': len(item['cells']),
                           'metadata_omitted': [key for key in item if key not in ('name', 'cells')]} for item in selected]
        entries = [(item['name'], ref, item['cells'][ref]) for item in selected for ref in sorted(item['cells'], key=position)]
        total = len(entries)
    else:
        text = result.get('text')
        if not isinstance(text, str):
            fail('Parsed document has no supported main-text string')
        total = len(text)
    if summary:
        view.update(coverage='overview_only_no_values_or_text', total_items=total, truncated=False, next_cursor=None)
        if len(encoded(view)) + 1 > max_chars:
            fail('Summary/diagnostics exceed output budget; increase max-output-chars')
        return view
    if start > total:
        fail('Cursor exceeds selected content')
    view.update(coverage='selected_stored_cells_only' if is_sheet else 'main_text_only_no_layout_or_related_content',
                total_items=total, start=start, items=[] if is_sheet else None)
    if not is_sheet:
        del view['items']
        view['text'] = ''
    def finish(end):
        more = end < total
        view.update(returned_items=end - start, truncated=more, next_cursor=None)
        if more:
            token = {'source': digest, 'scope': binding, 'next': end}
            view['next_cursor'] = base64.urlsafe_b64encode(encoded(token).encode()).decode()
    finish(start)
    if len(encoded(view)) + 1 > max_chars:
        fail('View diagnostics/metadata exceed output budget; increase max-output-chars')
    if is_sheet:
        for index in range(start, min(total, start + page_size)):
            name, ref, value = entries[index]
            view['items'].append({'sheet': name, 'cell': ref, 'data': value})
            finish(index + 1)
            if len(encoded(view)) + 1 > max_chars:
                view['items'].pop()
                finish(index)
                if index == start:
                    fail('Single cell exceeds output budget; increase max-output-chars')
                break
    else:
        # Find the largest prefix fitting the JSON budget, including escapes and cursor.
        low, high = 0, min(page_size, total - start)
        while low < high:
            middle = (low + high + 1) // 2
            view['text'] = text[start:start + middle]
            finish(start + middle)
            if len(encoded(view)) + 1 <= max_chars:
                low = middle
            else:
                high = middle - 1
        view['text'] = text[start:start + low]
        finish(start + low)
        if low == 0 and start < total:
            fail('No text fits output budget; increase max-output-chars')
    return view
