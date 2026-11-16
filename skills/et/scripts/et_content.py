"""Stored-table content projection; full evidence remains an explicit opt-in."""

# Never drop diagnostics or value-coverage fields. Only presentation/evidence
# families are excluded; cell values, cache states and numeric meaning survive.
ROOT_DROP = frozenset((
    'embedded_charts', 'note_drawings', 'opaque_objects', 'styles', 'style_xfs',
    'shared_string_formatting', 'shared_string_phonetics', 'rich_string_fonts',
    'shared_string_encoding', 'table_style_sources', 'uninterpreted_metadata',
    'format_string_header_flags', 'other_container_streams_not_decoded',
    'macro_storages_not_loaded', 'policies',
))
SHEET_DROP = frozenset((
    'rows', 'columns', 'row_records', 'column_records', 'visibility', 'views',
    'sheet_views', 'pane', 'selection', 'print_options', 'page_setup',
    'page_margins', 'header_footer', 'conditional_formats', 'conditional_formatting',
    'data_validations', 'formula_groups', 'table_styles', 'drawing', 'drawings',
))
CELL_DROP = frozenset((
    'record_id', 'record_offset', 'source_record_id', 'raw_numeric_text',
    'rich_text', 'rich_runs', 'rich_text_runs', 'text_encoding',
))


def _without_binary(value):
    """Copy nested evidence without encoded bytes, preserving ordinary strings."""
    if isinstance(value, dict):
        return {key: _without_binary(item) for key, item in value.items()
                if not (key.endswith('_hex') or key in ('raw_bytes', 'payload_base64'))}
    if isinstance(value, list):
        return [_without_binary(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_without_binary(item) for item in value)
    return value


def project_content(result):
    """Return a distinct schema, not a silently incomplete original extraction.

    Status, all diagnostic entries and coverage assertions remain authoritative.
    Intentional presentation omissions do not change the source parse outcome.
    """
    if result.get('status') not in ('ok', 'partial'):
        return result
    output = {key: value for key, value in result.items()
              if key not in ROOT_DROP and key != 'sheets'}
    omitted_root = sorted(key for key in result if key in ROOT_DROP)
    output['sheets'] = []
    sheet_omissions = []
    for sheet in result.get('sheets', []):
        selected = _without_binary({key: value for key, value in sheet.items()
                                    if key not in SHEET_DROP and key != 'cells'})
        selected['cells'] = {
            ref: _without_binary({key: value for key, value in cell.items()
                                  if key not in CELL_DROP})
            for ref, cell in sheet.get('cells', {}).items()}
        output['sheets'].append(selected)
        sheet_omissions.append({'name': sheet.get('name'),
                               'fields': sorted(key for key in sheet if key in SHEET_DROP)})
    output.update(schema_version='et_content_v1', source_schema_version=result.get('schema_version'))
    output['output_projection'] = {
        'kind': 'stored_table_content', 'raw_evidence_complete': False,
        'saved_values_unchanged': True, 'source_status_unchanged': True,
        'omitted_root_fields': omitted_root, 'omitted_sheet_fields': sheet_omissions,
        'omitted_cell_fields': sorted(CELL_DROP), 'binary_evidence_omitted': True,
        'diagnostics_preserved': True, 'retrieve_full': '--profile full',
        'not_original_workbook_schema': True,
        'parse_scope': 'full_safety_scan; BIFF opaque metadata hex retention disabled except internal Font decoding',
    }
    return output
