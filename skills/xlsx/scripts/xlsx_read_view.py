"""Opt-in snapshot-bound cell views; never replace the legacy full read schema."""
import base64
import hashlib
import json
import os
import re
import stat

MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_CHARS = 2 * 1024 * 1024
# Bump when projection ordering or interpretation changes; legacy cursors fail closed.
PROJECTION_REVISION = 2


def snapshot(path):
    """Read one bounded regular-file snapshot for both parsing and fingerprinting."""
    with open(path, 'rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SOURCE_BYTES:
            raise ValueError('Read view requires a regular file within 128 MiB')
        data = handle.read(before.st_size + 1)
        after = os.fstat(handle.fileno())
    if len(data) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('Input changed during snapshot read')
    return data


def coordinates(ref):
    match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', ref)
    if not match:
        raise ValueError('Expected uppercase A1 cell reference')
    col = 0
    for char in match[1]:
        col = col * 26 + ord(char) - 64
    row = int(match[2])
    if col > 16384 or row > 1048576:
        raise ValueError('Cell reference outside XLSX limits')
    return row, col


def bounds(value):
    if value is None:
        return None
    parts = value.split(':')
    if len(parts) not in (1, 2):
        raise ValueError('Range requires A1 or A1:B2')
    top, left = coordinates(parts[0])
    bottom, right = coordinates(parts[-1])
    if top > bottom or left > right:
        raise ValueError('Range endpoints are reversed')
    return top, left, bottom, right


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False, separators=(',', ':'))


def read_view(book, data, *, summary=False, sheet=None, cell_range=None, page_size=200, cursor=None, max_chars=12000):
    """Return a valid bounded JSON projection in deterministic sheet/row/column order."""
    if not 1 <= page_size <= 10000 or not 512 <= max_chars <= MAX_OUTPUT_CHARS:
        raise ValueError('page-size must be 1..10000; max-output-chars must be 512..2097152')
    if summary and (cell_range or cursor):
        raise ValueError('summary cannot be combined with range or cursor')
    if cell_range and sheet is None:
        raise ValueError('range requires an explicit sheet')
    area = bounds(cell_range)
    sheets = book.inspect()['sheets']
    if sheet is not None and not any(item['name'] == sheet for item in sheets):
        raise ValueError('Unknown worksheet')
    selected = [item for item in sheets if sheet is None or item['name'] == sheet]
    digest = hashlib.sha256(data).hexdigest()
    scope = {'sheet': sheet, 'range': cell_range, 'page_size': page_size}
    binding = hashlib.sha256(encode(scope).encode()).hexdigest()
    start = 0
    if cursor is not None:
        try:
            if len(cursor) > 1024:
                raise ValueError()
            token = json.loads(base64.b64decode(cursor.encode('ascii'), altchars=b'-_', validate=True))
            if (set(token) != {'sha256', 'scope', 'next', 'revision'} or type(token['revision']) is not int
                    or token['revision'] != PROJECTION_REVISION
                    or token['sha256'] != digest or token['scope'] != binding):
                raise ValueError()
            start = token['next']
            if type(start) is not int or start < 0:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, KeyError) as exc:
            raise ValueError('Invalid or legacy cursor, or source/options/projection changed; restart the read') from exc
    result = {'schema_version': 'xlsx_read_view_v1', 'status': 'ok', 'mode': 'summary' if summary else 'cells',
              'source_sha256': digest, 'projection_revision': PROJECTION_REVISION, 'scope': scope, 'date_system': book.inspect()['date_system'],
              'formula_caches': 'stored and unverified; no calculation', 'implicit_cells': 'not materialized',
              'sheets': [{'name': item['name'], 'stored_cells': len(item['cells'])} for item in selected]}
    if summary:
        result.update(coverage='overview_only_no_cell_values', truncated=False, next_cursor=None)
        if len(encode(result)) + 1 > max_chars:
            raise ValueError('Summary exceeds output budget; select one sheet or increase max-output-chars')
        return result
    entries = []
    for item in selected:
        for ref in sorted(item['cells'], key=coordinates):
            row, col = coordinates(ref)
            if area and not (area[0] <= row <= area[2] and area[1] <= col <= area[3]):
                continue
            entries.append({'sheet': item['name'], 'cell': ref, 'data': item['cells'][ref]})
    if start > len(entries):
        raise ValueError('Cursor exceeds selected stored cells')
    result.update(coverage='selected_stored_cells_only', total_items=len(entries), start=start, items=[])
    def finish(end):
        more = end < len(entries)
        result.update(returned_items=end - start, truncated=more, next_cursor=None)
        if more:
            raw = encode({'sha256': digest, 'scope': binding, 'next': end, 'revision': PROJECTION_REVISION}).encode()
            result['next_cursor'] = base64.urlsafe_b64encode(raw).decode()
    def variable_size():
        # Cursor is generated URL-safe ASCII, so JSON adds exactly two quotes.
        token_chars = 4 if result['next_cursor'] is None else len(result['next_cursor']) + 2
        return len(str(result['returned_items'])) + (4 if result['truncated'] else 5) + token_chars
    finish(start)
    initial_size = len(encode(result)) + 1
    fixed_size = initial_size - variable_size()
    if initial_size > max_chars:
        raise ValueError('View metadata exceeds output budget')
    item_size = 0
    for index in range(start, min(len(entries), start + page_size)):
        entry = entries[index]
        encoded_size = len(encode(entry))
        finish(index + 1)
        count = index + 1 - start
        projected_size = fixed_size + variable_size() + item_size + encoded_size + max(0, count - 1)
        if projected_size > max_chars:
            finish(index)
            if index == start:
                raise ValueError('Single cell exceeds output budget; increase max-output-chars or narrow range')
            break
        result['items'].append(entry)
        item_size += encoded_size
    # The actual canonical encoder remains the authoritative final budget check.
    if len(encode(result)) + 1 > max_chars:
        raise ValueError('Final view exceeds output budget')
    return result
