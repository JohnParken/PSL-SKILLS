"""Typed, bounded table selection from complete ET reader results; no file writes."""
from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import re
from types import SimpleNamespace

from wps_access import ParseError
from wps_biff8_headers import address, column_name, reconstruct

MAX_TABLE_CELLS = 200000
MAX_TABLE_COLUMNS = 256
MAX_QUERY_BYTES = 65536
MAX_NUMBER_DIGITS = 1100
MAX_NUMBER_EXPONENT = 2200
MAX_EXCLUDED_ROWS = 10000
SCHEMAS = {'biff8_readonly_v1', 'ooxml_spreadsheet_readonly_v1'}
CELL_TYPES = {'number', 'text', 'boolean', 'blank', 'error'}


def need(condition, message, status='analysis_error'):
    if not condition:
        raise ParseError(status, {'stage': 'table_analysis'}, message)


def validate_query(query, allowed):
    need(isinstance(query, dict) and set(query) <= allowed, 'Unknown table query fields')
    try:
        encoded = json.dumps(query, ensure_ascii=False, allow_nan=False, default=str).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        need(False, 'Query must contain finite, UTF-8 serializable values')
    need(len(encoded) <= MAX_QUERY_BYTES, 'Query exceeds 64 KiB', 'resource_limit')


def source_context(result):
    return {name: deepcopy(result[name]) for name in ('status', 'partial', 'schema_version', 'issues', 'diagnostics', 'reading_assessment',
            'cell_value_coverage', 'omissions_affect_cell_values', 'date_system', 'producer_verified') if name in result}


def number(value):
    """Accept reader Decimal/JSON numeric strings, never bool or binary float."""
    need(isinstance(value, (Decimal, str)) or type(value) is int, 'Numeric value must be Decimal, integer or decimal string')
    if isinstance(value, str):
        need(0 < len(value) <= 4096 and re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]{1,4})?', value),
             'Invalid numeric decimal string')
    try:
        result = Decimal(value)
    except (ValueError, InvalidOperation):
        need(False, 'Invalid decimal value')
    need(result.is_finite(), 'Nonfinite numbers cannot be analyzed')
    parts = result.as_tuple()
    need(len(parts.digits) <= MAX_NUMBER_DIGITS and abs(parts.exponent) <= MAX_NUMBER_EXPONENT and abs(result.adjusted()) <= 1100,
         'Numeric value exceeds analysis precision budget', 'resource_limit')
    return result


def bounds(region, schema):
    need(isinstance(region, str) and len(region) <= 40, 'Explicit A1 region required')
    ends = region.split(':')
    need(len(ends) in (1, 2), 'Region must be one cell or an A1 rectangle')
    try:
        first_row, first_col = address(ends[0])
        last_row, last_col = address(ends[-1])
    except ValueError as error:
        need(False, str(error))
    max_row, max_col = (65536, 256) if schema == 'biff8_readonly_v1' else (1048576, 16384)
    need(1 <= first_row <= last_row <= max_row and 1 <= first_col <= last_col <= max_col, 'Region exceeds source grid or is reversed')
    need((last_row - first_row + 1) * (last_col - first_col + 1) <= MAX_TABLE_CELLS and last_col - first_col + 1 <= MAX_TABLE_COLUMNS,
         'Selected rectangle exceeds 200000 positions or 256 columns', 'resource_limit')
    return first_row, first_col, last_row, last_col


def typed_cell(cell, coordinate, formula_policy):
    if cell is None:
        return {'coordinate': coordinate, 'type': 'missing', 'value': None, 'analysis_state': 'usable', 'formula': False}
    need(isinstance(cell, dict) and isinstance(cell.get('type'), str) and cell['type'] in CELL_TYPES,
         'Unsupported selected cell type at ' + coordinate)
    kind, value = cell['type'], cell.get('value')
    if kind == 'number':
        value = number(value)
    elif kind == 'boolean':
        need(type(value) is bool, 'Boolean cell requires a boolean at ' + coordinate)
    elif kind == 'blank':
        need(value is None, 'Blank cell must retain null at ' + coordinate)
    else:
        need(isinstance(value, str), 'Text/error cell requires text at ' + coordinate)
    formula = cell.get('formula') is not None
    state = 'error' if kind == 'error' else 'usable'
    cache = cell.get('cache_status')
    if formula:
        if cache == 'missing':
            state = 'missing_cache'
        elif kind == 'error' or cache == 'error_unverified':
            state = 'error_cache'
        elif formula_policy == 'exclude':
            state = 'formula_excluded'
        else:
            need(cache == 'unverified', 'Unknown formula cache status at ' + coordinate)
    return {'coordinate': coordinate, 'type': kind, 'value': value, 'analysis_state': state,
            'formula': formula, **({'cache_status': cache} if formula else {})}


def count_states(cells):
    """Count unavailable values and selected unverified caches, including blanks."""
    counts = Counter()
    for cell in cells:
        if cell['analysis_state'] != 'usable':
            counts[cell['analysis_state']] += 1
        elif cell['formula']:
            counts['formula_cache_used'] += 1
    return dict(counts)


def excluded_rows(query, data_first_row, last_row):
    rows = query.get('exclude_rows', [])
    need(isinstance(rows, list), 'exclude_rows must list physical worksheet row integers')
    need(len(rows) <= MAX_EXCLUDED_ROWS, 'exclude_rows exceeds 10000 rows', 'resource_limit')
    need(all(type(row) is int and data_first_row <= row <= last_row for row in rows),
         'Excluded rows must lie inside the selected data rows, after any declared header')
    need(len(set(rows)) == len(rows), 'exclude_rows contains duplicate rows')
    return set(rows)


def extract_table(result, query):
    """Keep physical columns as IDs; header labels never decide column identity."""
    need(isinstance(result, dict) and isinstance(result.get('schema_version'), str) and result['schema_version'] in SCHEMAS,
         'Complete ET spreadsheet schema required; projections are not tables')
    need(result.get('status') in ('ok', 'partial') and isinstance(result.get('sheets'), list), 'Successful complete reader result required')
    need(all(isinstance(sheet, dict) for sheet in result['sheets']), 'Malformed worksheet collection')
    need(isinstance(query.get('sheet'), str), 'Explicit worksheet name required')
    sheets = [sheet for sheet in result['sheets'] if sheet.get('name') == query['sheet']]
    need(len(sheets) == 1, 'Worksheet name must identify exactly one sheet')
    sheet = sheets[0]
    need(isinstance(sheet.get('cells'), dict), 'Worksheet cell mapping required')
    first_row, first_col, last_row, last_col = bounds(query.get('range'), result['schema_version'])
    formula_policy = query.get('formula_policy', 'exclude')
    merge_policy = query.get('merge_policy', 'preserve')
    need(formula_policy in ('exclude', 'cached'), 'formula_policy must be exclude or cached')
    need(merge_policy in ('preserve', 'fill_text'), 'merge_policy must be preserve or fill_text')
    columns = [column_name(col) for col in range(first_col, last_col + 1)]
    cells = {}
    for row in range(first_row, last_row + 1):
        for col in columns:
            coordinate = col + str(row)
            cell = typed_cell(sheet['cells'].get(coordinate), coordinate, formula_policy)
            cells[coordinate] = cell

    merges = []
    occupied = set()
    source_merges = sheet.get('merges', [])
    need(isinstance(source_merges, list) and len(source_merges) <= 65536, 'Malformed/excessive merge list')
    for merge in source_merges:
        need(isinstance(merge, dict), 'Malformed merge')
        ref = merge.get('range', merge.get('ref'))
        need(isinstance(ref, str) and len(ref) <= 40 and ref.count(':') == 1, 'Malformed merge range')
        try:
            top, left = address(ref.split(':')[0])
            bottom, right = address(ref.split(':')[1])
        except ValueError as error:
            need(False, str(error))
        need(top <= bottom and left <= right and (top, left) != (bottom, right), 'Invalid merge extent')
        if bottom < first_row or top > last_row or right < first_col or left > last_col:
            continue
        anchor = column_name(left) + str(top)
        clipped = top < first_row or left < first_col or bottom > last_row or right > last_col
        merges.append({'range': ref, 'anchor': anchor, 'clipped_by_selection': clipped})
        for row in range(max(top, first_row), min(bottom, last_row) + 1):
            for col in range(max(left, first_col), min(right, last_col) + 1):
                coordinate = column_name(col) + str(row)
                need(coordinate not in occupied, 'Overlapping selected merges')
                occupied.add(coordinate)
                cells[coordinate]['merge_anchor'] = anchor

    header = None
    data_first_row = first_row
    if query.get('header_rows') is not None:
        def read_header(coordinate):
            item = cells[coordinate]
            return (item['type'], item['value']) if item['analysis_state'] == 'usable' else ('unavailable', None)
        try:
            header = reconstruct(SimpleNamespace(cells={query['sheet']: sheet['cells']}), query['sheet'],
                                 (first_row, first_col, last_row, last_col), query['header_rows'], read_header,
                                 merge_nodes=[{'ref': item['range']} for item in merges])
        except ValueError as error:
            need(False, str(error))
        data_first_row = header['data_first_row']

    excluded = excluded_rows(query, data_first_row, last_row)
    filled = 0
    if merge_policy == 'fill_text':
        for coordinate, cell in cells.items():
            row = address(coordinate)[0]
            if row < data_first_row or row in excluded or cell.get('merge_anchor', coordinate) == coordinate:
                continue
            anchor = cells.get(cell['merge_anchor'])
            if (anchor is None or address(cell['merge_anchor'])[0] in excluded or anchor['type'] != 'text'
                    or anchor['formula'] or anchor['analysis_state'] != 'usable'):
                continue
            need(cell['type'] in ('missing', 'blank') and not cell['formula'],
                 'Text-fill would overwrite stored non-anchor content at ' + coordinate)
            cell.update(type='text', value=anchor['value'], propagated_from=cell['merge_anchor'],
                        original_type=cell['type'], original_value=cell['value'])
            filled += 1
    rows = [{'source_row': row, 'cells': {col: cells[col + str(row)] for col in columns}}
            for row in range(data_first_row, last_row + 1) if row not in excluded]
    return {'columns': columns, 'rows': rows, 'header': header, 'merges': merges,
            'data_first_row': data_first_row, 'selected_positions': len(cells),
            'input_rows': last_row - data_first_row + 1, 'excluded_source_rows': sorted(excluded),
            'state_counts': count_states(cells.values()), 'filled_text_positions': filled,
            'formula_policy': formula_policy, 'merge_policy': merge_policy}
