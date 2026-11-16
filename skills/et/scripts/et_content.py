"""Content-first model output; diagnostic/raw contracts remain in --profile full."""
import re

ROOT_KEYS = ('status', 'partial', 'format', 'date_system', 'cell_value_coverage',
             'omissions_affect_cell_values', 'sheet_coverage', 'cell_count',
             'coordinates_verified', 'coordinate_semantics', 'coordinate_basis', 'issues_truncated',
             'encoding', 'encoding_source', 'encoding_declaration_verified')
SHEET_KEYS = ('name', 'hidden_state', 'read_status', 'substream_type', 'merges',
              'coordinates_verified', 'coordinate_semantics', 'coordinate_basis', 'charts')
CELL_KEYS = ('type', 'value', 'cache_status', 'text_truncated', 'coordinates_verified',
             'coordinate_semantics', 'coordinate_basis', 'coordinate_verified', 'raw_value',
             'display_text', 'formula_text', 'error', 'error_code')
NUMBER_KEYS = ('status', 'kind', 'interpreted_value', 'display_text', 'date_system',
               'reason', 'format_code', 'date', 'time', 'iso_date', 'iso_datetime',
               'excel_leap_day', 'negative_date', 'currency', 'unit', 'rounding',
               'confidence', 'not_excel_rendering', 'valid_gregorian_date', 'time_seconds',
               'display_locale_not_applied', 'currency_symbol', 'currency_code',
               'multiplier', 'scale_divisor', 'decimal_places', 'max_denominator')
FORMULA_KEYS = ('text', 'cache_may_be_stale', 'expression_evaluated', 'status',
                'expression_status', 'text_status', 'reason', 'formula_text', 'needs_recalculation_flag')
LOCATION_KEYS = ('sheet', 'cell', 'range', 'stage')


def _pick(value, keys):
    return {key: value[key] for key in keys if key in value}


def _issues(result):
    """One compact entry per diagnostic code, never duplicate assessment events.

    Codes/counts are not severity filters: even unfamiliar safety/coverage gaps
    survive. At most two human-useful sheet/cell positions per code are returned;
    binary offsets, XML tags and relationship bookkeeping require full mode.
    """
    grouped = {}
    for issue in result.get('issues', []):
        if not isinstance(issue, dict):
            issue = {'code': 'unstructured_diagnostic', 'message': str(issue)}
        code = str(issue.get('code', 'reader_diagnostic'))
        entry = grouped.setdefault(code, {'code': code, 'count': 0})
        count = issue.get('count', 1)
        entry['count'] += count if isinstance(count, int) and count > 0 else 1
        if issue.get('message') and 'message' not in entry:
            entry['message'] = str(issue['message'])[:240]
        for key in ('cell_value_impact', 'severity'):
            if key in issue:
                entry[key] = issue[key]
        samples = [issue, *issue.get('samples', [])]
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            location = _pick(sample, LOCATION_KEYS)
            if location:
                locations = entry.setdefault('locations', [])
                if location not in locations and len(locations) < 2:
                    locations.append(location)
    return list(grouped.values())


def _cell(cell):
    result = _pick(cell, CELL_KEYS)
    if 'formula' in cell:
        source = cell['formula']
        result['formula'] = _pick(source, FORMULA_KEYS) if isinstance(source, dict) else source
        if isinstance(result['formula'], dict):
            result['formula']['cache_may_be_stale'] = True
    meaning = cell.get('numeric_interpretation')
    if isinstance(meaning, dict):
        result['numeric_interpretation'] = _pick(meaning, NUMBER_KEYS)
    return result


def _annotations(entries):
    return [_pick(item, ('cell', 'range', 'text', 'author', 'display', 'tooltip',
                        'target', 'target_raw', 'location', 'kind', 'text_truncated',
                        'target_resolved', 'followed', 'display_raw', 'tooltip_raw', 'location_raw'))
            for item in entries if isinstance(item, dict)]



def _blank_ranges(cells):
    """Losslessly compress explicit blank coordinates, never infer absent cells."""
    rows = {}
    blank_refs = set()
    for ref, cell in cells.items():
        match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,6})', ref)
        if (not match or cell.get('type') != 'blank' or cell.get('value') is not None
                or any(key in cell for key in ('formula', 'text_truncated', 'error', 'error_code'))
                or cell.get('cache_status') not in (None, 'not_applicable')
                or cell.get('coordinate_verified') is False or cell.get('coordinates_verified') is False):
            continue
        column = 0
        for char in match[1]:
            column = column * 26 + ord(char) - 64
        rows.setdefault(int(match[2]), []).append(column)
        blank_refs.add(ref)
    rectangles = []
    active = {}
    for row, cols in sorted(rows.items()):
        spans = []
        for col in sorted(cols):
            if spans and col == spans[-1][1] + 1:
                spans[-1][1] = col
            else:
                spans.append([col, col])
        next_active = {}
        for first, last in spans:
            old = active.get((first, last))
            if old is not None and old[1] == row - 1:
                old[1] = row
                rectangle = old
            else:
                rectangle = [row, row, first, last]
                rectangles.append(rectangle)
            next_active[first, last] = rectangle
        active = next_active
    def address(row, column):
        text = ''
        while column:
            column, digit = divmod(column - 1, 26)
            text = chr(65 + digit) + text
        return text + str(row)
    ranges = []
    for first_row, last_row, first_col, last_col in rectangles:
        start, end = address(first_row, first_col), address(last_row, last_col)
        ranges.append(start if start == end else start + ':' + end)
    return blank_refs, ranges


def project_content(result):
    """Preserve typed values and explicit gaps without exposing parser internals."""
    if result.get('status') not in ('ok', 'partial'):
        return result
    output = _pick(result, ROOT_KEYS)
    output.update(schema_version='et_content_v1', source_schema_version=result.get('schema_version'))
    output['issues'] = _issues(result)
    output['sheets'] = []
    for sheet in result.get('sheets', []):
        selected = _pick(sheet, SHEET_KEYS)
        cells = sheet.get('cells', {})
        blank_refs, blank_ranges = _blank_ranges(cells)
        selected['cells'] = {ref: _cell(cell) for ref, cell in cells.items() if ref not in blank_refs}
        if blank_refs:
            selected['stored_blank_cells'] = {'count': len(blank_refs), 'ranges': blank_ranges}
        for key in ('comments', 'hyperlinks'):
            if sheet.get(key):
                selected[key] = _annotations(sheet[key])
        output['sheets'].append(selected)
    output['content_scope'] = {
        'cells': 'saved_cells_including_hidden_sheets_and_rows; explicit_blanks_in_stored_blank_cells_ranges',
        'charts': 'referenced_worksheet_OOXML_chart_titles_series_and_saved_caches',
        'not_rendered': True, 'formula_cache_freshness': 'unverified',
        'not_extracted': ['image_text', 'drawing_text', 'BIFF_chart_semantics',
                          'standalone_chart_sheets', 'chart_layout_and_extensions'],
    }
    opaque_charts = result.get('embedded_charts', [])
    if opaque_charts:
        output['chart_gaps'] = [{'status': 'not_extracted', 'count': len(opaque_charts),
                                 'reason': 'BIFF chart semantics are not decoded; not absent'}]
    for key in ('text_budget', 'output_text_budget'):
        budget = result.get(key, {})
        limited = {name: value for name, value in budget.items()
                   if ('limited' in name or 'truncated' in name) and value}
        if limited:
            output[key] = limited
    output['output_projection'] = {
        'kind': 'stored_table_content', 'revision': 2,
        'diagnostics': 'codes_counts_and_bounded_locations; full_details_omitted',
        'omitted': 'parser_structure_style_and_binary_evidence',
        'retrieve_full': '--profile full',
    }
    return output
