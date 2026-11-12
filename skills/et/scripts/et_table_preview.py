"""Explicit rectangular stored-value preview for choosing table/header boundaries."""
from copy import deepcopy

from et_table_data import extract_table, need, source_context, validate_query

PREVIEW_KEYS = {'sheet', 'range', 'offset', 'limit'}
MAX_PREVIEW_ROWS = 50
MAX_PREVIEW_POSITIONS = 10000


def _preview_cell(cell, source_cells):
    projected = {key: value for key, value in cell.items() if key != 'analysis_state'}
    source = source_cells.get(cell['coordinate'], {})
    formula = source.get('formula')
    if isinstance(formula, dict):
        text = formula.get('text') if formula.get('encoding') == 'stored_ooxml_expression' else formula.get('formula_text')
        if isinstance(text, str) and text:
            projected['formula_text'] = text
    numeric = source.get('numeric_interpretation')
    if cell['type'] == 'number' and isinstance(numeric, dict) and numeric.get('status') == 'interpreted':
        display = numeric.get('display_text')
        if isinstance(display, str):
            projected['display_text'] = display
    return projected


def preview(result, query):
    validate_query(query, PREVIEW_KEYS)
    offset, limit = query.get('offset', 0), query.get('limit', 10)
    need(type(offset) is int and 0 <= offset <= 200000, 'Preview offset must be an integer in 0..200000')
    need(type(limit) is int and 1 <= limit <= MAX_PREVIEW_ROWS, 'Preview limit must be an integer in 1..50')
    table = extract_table(result, query)
    source_cells = next(sheet['cells'] for sheet in result['sheets'] if sheet.get('name') == query['sheet'])
    page = table['rows'][offset:offset + limit]
    need(len(page) * len(table['columns']) <= MAX_PREVIEW_POSITIONS, 'Preview page exceeds 10000 cell positions', 'resource_limit')
    rows = [{'source_row': row['source_row'], 'cells': {
        column: _preview_cell(cell, source_cells) for column, cell in row['cells'].items()}}
        for row in page]
    total = len(table['rows'])
    more = offset + len(rows) < total
    page_coordinates = {cell['coordinate'] for row in rows for cell in row['cells'].values()}
    partial = result['status'] == 'partial' or bool(table['merge_issues'])
    return {'schema_version': 'et_table_preview_v1', 'status': 'partial' if partial else 'ok', 'partial': partial,
            'preview_issues': table['merge_issues'],
            'source': source_context(result), 'query': deepcopy(query), 'columns': table['columns'], 'rows': rows,
            'merges': [{**merge, 'anchor_in_page': merge['anchor'] in page_coordinates} for merge in table['merges']],
            'scope': {'sheet': query['sheet'], 'range': query['range'], 'merges_scope': 'entire selected rectangle',
                                               'selected_positions': table['selected_positions'],
                                               'returned_positions': len(rows) * len(table['columns'])},
            'row_page': {'offset': offset, 'returned': len(rows), 'total': total,
                         'truncated': offset > 0 or more, 'next_offset': offset + len(rows) if more else None},
            'header_rows_inferred': False, 'values_filled': False, 'statistics_computed': False,
            'metadata_omitted': ['formula_tokens_and_details', 'cell_styles', 'numeric_format_interpretation',
                                 'visibility_and_source_filters', 'links_comments_and_other_workbook_metadata'],
            'formulas_executed': False, 'external_links_followed': False, 'source_modified': False}
