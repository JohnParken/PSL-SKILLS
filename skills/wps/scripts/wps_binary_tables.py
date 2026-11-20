"""Bounded PAPX evidence for rows, supported cell layouts and paragraph marks."""
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Optional

from wps_binary_table_fkp import FkpPropertyReader, init_bte
from wps_binary_table_groups import (
    ROW_GROUP_PROPERTIES, TABLE_POSITION_CODE, TABLE_STYLE_PROPERTY,
    group_tables, has_table_boundary_bookmarks, row_identity,
)

MAX_PAGES = 4096
MAX_MARKS = 100000
MAX_CELLS = 100000
MAX_PROPERTY_BYTES = 1024 * 1024
MAX_LAYOUT_MODIFIERS = 1000
MAX_UNRESOLVED_MARKERS = 1000
TABLE_PROPERTIES = {0x2416: 'in_table', 0x2417: 'row_end', 0x6649: 'depth',
                    0x244B: 'inner_cell', 0x244C: 'inner_row_end'}
# MS-DOC 2.6.2. These fixed-width operands do not change table depth or
# row/cell marks. Other operands retain explicit uninterpreted diagnostics.
TABLE_NEUTRAL_PARAGRAPH_WIDTHS = {
    0x2407: 1,  # PFPageBreakBefore
    0x260A: 1, 0x460B: 2,  # PIlvl, PIlfo
    0x2433: 1, 0x2434: 1, 0x2435: 1, 0x2436: 1,  # East Asian line breaking
    0x2437: 1, 0x2438: 1, 0x4439: 2,  # PFAutoSpaceDE/DN, PWAlignFont
    0x2441: 1, 0x2443: 1, 0x2447: 1, 0x2448: 1,  # direction, list revision, grid
    0x4458: 2, 0x4459: 2,  # PDylBefore, PDylAfter
}
# MS-DOC 2.6.2: fixed-width paragraph layout only, never table boundaries.
LAYOUT_PROPERTIES = {
    0x2403: ('sprmPJc80', 1),
    0x2405: ('sprmPFKeep', 1),
    0x2406: ('sprmPFKeepFollow', 1),
    0x240C: ('sprmPFNoLineNumb', 1),
    0x840E: ('sprmPDxaRight80', 2),
    0x840F: ('sprmPDxaLeft80', 2),
    0x8411: ('sprmPDxaLeft180', 2),
    0x2431: ('sprmPFWidowControl', 1),
    0x8418: ('sprmPDxaAbs', 2),
    0x8419: ('sprmPDyaAbs', 2),
    0x261B: ('sprmPPc', 1),
    0x6412: ('sprmPDyaLine', 4),
    0xA413: ('sprmPDyaBefore', 2),
    0xA414: ('sprmPDyaAfter', 2),
    0x2461: ('sprmPJc', 1),
    0x4457: ('sprmPDxcLeft1', 2),
    0x4455: ('sprmPDxcRight', 2),
    0x4456: ('sprmPDxcLeft', 2),
    0x245B: ('sprmPFDyaBeforeAuto', 1),
    0x245C: ('sprmPFDyaAfterAuto', 1),
    0x8460: ('sprmPDxaLeft1', 2),
    0x845D: ('sprmPDxaRight', 2),
    0x845E: ('sprmPDxaLeft', 2),
}
LOGICAL_ALIGNMENT = (
    'logical_left', 'center', 'logical_right', 'justify', 'distribute',
    'medium_kashida', 'indent', 'high_kashida', 'low_kashida', 'thai_distribute',
)
PHYSICAL_ALIGNMENT = (
    'physical_left', 'center', 'physical_right', 'justify_low_compression',
    'justify_medium_compression', 'justify_high_compression',
)
LAYOUT_BOOLEANS = {0x2405, 0x2406, 0x240C, 0x2431, 0x245B, 0x245C}
INDENTS_TWIPS = {0x840E, 0x840F, 0x8411, 0x8460, 0x845D, 0x845E}
INDENTS_CHARACTER_UNITS = {0x4457, 0x4455, 0x4456}

# These operands do not rewrite the stored cell definitions or merge flags.
TABLE_TOPOLOGY_NEUTRAL = {
    0x5400, 0x548A, 0x9407,  # justification and height
    0x3403, 0x3404, 0x3466, 0x3619,  # row split, headers and pagination
    0xD605, 0xD613, 0xD61A, 0xD61B, 0xD61C, 0xD61D, 0xD620, 0xD62F,  # borders
    0xD609, 0xD60C, 0xD612, 0xD616, 0xD660, 0xD670, 0xD671, 0xD672,  # shading
    0xD632, 0xD634, 0xD635, 0xF614, 0x3615,  # margins, preferred widths, autofit
    0x7629, 0xD62C, 0xD639,  # cell text flow, alignment and wrapping
    *ROW_GROUP_PROPERTIES,
}
OPAQUE_TABLE_PROPERTY = 0xD5FF
TABLE_LEFT_ORIGIN = 0x9601
TABLE_HALF_GAP = 0x9602
CELL_MERGE_OPERATIONS = (0x5624, 0x5625, 0xD62B)  # TMerge, TSplit, TVertMerge


def layout_operand(sprm, raw):
    """Interpret a single stored operand without applying formatting."""
    value = int.from_bytes(raw, 'little')
    if sprm in (0x2403, 0x2461):
        alignment = PHYSICAL_ALIGNMENT if sprm == 0x2403 else LOGICAL_ALIGNMENT
        return {'value': alignment[value]} if value < len(alignment) else {'interpretation': 'invalid_operand'}
    if sprm in LAYOUT_BOOLEANS:
        return {'value': bool(value)} if value in (0, 1) else {'interpretation': 'invalid_operand'}
    if sprm in (0xA413, 0xA414):
        return {'value': value, 'unit': 'twips'} if value <= 0x7BC0 else {'interpretation': 'invalid_operand'}
    if sprm in INDENTS_TWIPS or sprm in INDENTS_CHARACTER_UNITS:
        return {'value': int.from_bytes(raw, 'little', signed=True),
                'unit': 'twips' if sprm in INDENTS_TWIPS else 'hundredths_of_character'}
    return {'interpretation': 'raw_only'}


def contiguous_row_runs(rows):
    """Index source-adjacent recovered rows without inferring logical tables."""
    runs = []
    for index, row in enumerate(rows):
        start = row['source']['cp_start']
        end = row['source']['cp_end']
        if not runs or runs[-1]['cp_end'] != start:
            runs.append({'first_row_index': index, 'row_count': 1, 'cp_start': start, 'cp_end': end})
        else:
            runs[-1]['row_count'] += 1
            runs[-1]['cp_end'] = end
    return runs


def skipped_operand_end(sprm, region, cursor, stop):
    """Bound a PRL operand without interpreting its formatting semantics."""
    from read_wps import require
    width = (1, 1, 2, 4, 2, 2, None, 3)[sprm >> 13]
    if sprm == 0xD608:
        require(cursor + 2 <= stop, region.location(cursor), 'Truncated TDefTable size')
        width = region.number(cursor, 2) + 1
        require(width >= 3, region.location(cursor), 'Invalid TDefTable size')
    elif width is None:
        require(cursor < stop, region.location(cursor), 'Missing variable SPRM size')
        size = region.number(cursor, 1)
        width = size + 1
        if sprm == 0xC615:  # PChgTabs, not PChgTabsPapx
            require(size >= 2, region.location(cursor), 'Invalid PChgTabs size')
            if size == 255:
                require(cursor + 2 <= stop, region.location(cursor), 'Missing deleted tab count')
                deleted = region.number(cursor + 1, 1)
                require(deleted <= 64, region.location(cursor + 1), 'Invalid deleted tab count')
                added_at = cursor + 2 + 4 * deleted
                require(added_at < stop, region.location(added_at), 'Missing added tab count')
                added = region.number(added_at, 1)
                require(added <= 64, region.location(added_at), 'Invalid added tab count')
                width = 3 + 4 * deleted + 3 * added
    require(cursor + width <= stop, region.location(cursor), 'Truncated uninterpreted SPRM operand')
    return cursor + width


def initial_cell_properties(raw, columns):
    """Expose only complete initial TC80 records, never infer the final grid."""
    count = min(columns, len(raw) // 20)
    cells = []
    for index in range(count):
        record = raw[index * 20:(index + 1) * 20]
        flags = int.from_bytes(record[:2], 'little')
        cells.append({'index': index, 'tcgrf_raw_hex': bytes(record[:2]).hex(),
                      'horizontal_merge_initial': flags & 3,
                      'vertical_merge_initial': (flags >> 5) & 3,
                      'preferred_width_raw': int.from_bytes(record[2:4], 'little', signed=True)})
    return {'cells': cells, 'complete_tc80_records': count,
            'columns_without_complete_tc80': columns - count,
            'incomplete_tc80_bytes': len(raw) % 20 if count < columns else 0,
            'scope': 'initial_definition_only; later modifiers not applied'}


def table_width_operand(sprm, raw, source):
    """Decode bounded local operands; invalid semantics remain raw and partial."""
    raw = bytes(raw)
    result = {'sprm': hex(sprm), 'source': source, 'raw_hex': raw.hex(),
              'interpretation': 'invalid_or_unsupported', 'scope': 'local_modifier_only'}
    if sprm == 0xD635 and len(raw) == 5:
        first, limit, units = raw[:3]
        width = int.from_bytes(raw[3:5], 'little', signed=True)
        valid = first <= limit <= 63 and first < 63 and units in (0, 1, 2, 3)
        valid = valid and (units == 0 or (units == 1 and width == 0)
                           or (units == 2 and 0 <= width <= 5000)
                           or (units == 3 and 0 <= width <= 31680))
        if valid:
            result.update(interpretation='decoded', first_cell=first, limit_cell=limit,
                          width_type=units, width_raw=width)
    elif sprm == 0xD634 and len(raw) == 6:
        first, limit, sides, units = raw[:4]
        width = int.from_bytes(raw[4:6], 'little')
        if (first, limit) == (0, 1) and not sides & ~15 and units in (0, 3) and width <= 31680 and (units != 0 or width == 0):
            result.update(interpretation='decoded', first_cell=first, limit_cell=limit,
                          sides_mask=sides, width_type=units, width_raw=width)
    return result


def row_definition(props, cell_count):
    """Bind supported stored cell definitions to a validated physical row."""
    definition = props.get('initial_definition')
    if not definition or props.get('unsupported_cell_layout') or definition['column_count'] != cell_count:
        return None
    initial = definition['initial_cell_properties']
    if initial['incomplete_tc80_bytes']:
        return None
    horizontal = [cell['horizontal_merge_initial'] for cell in initial['cells']]
    vertical = [cell['vertical_merge_initial'] for cell in initial['cells']]
    horizontal.extend([0] * (cell_count - len(horizontal)))
    vertical.extend([0] * (cell_count - len(vertical)))
    edges = definition['edges_twips']
    if props.get('gap_half', 0) < 0 or (
            'left_origin' in props and props['left_origin'] - props.get('gap_half', 0) != edges[0]):
        # This subset compares the original grid; no guessed translation.
        return None
    for sprm, raw in props.get('merge_operands', ()):
        if len(raw) != 2 or raw[0] >= cell_count:
            return None
        first, limit_or_flag = raw
        if sprm == 0xD62B:
            vertical[first] = limit_or_flag
        elif not first <= limit_or_flag <= cell_count:
            return None
        elif sprm == 0x5625:
            horizontal[first:limit_or_flag] = [0] * (limit_or_flag - first)
        elif limit_or_flag > first + 1:
            previous = horizontal[first:limit_or_flag]
            if any(previous) and not (previous[0] in (2, 3) and all(flag == 1 for flag in previous[1:])
                                      and (limit_or_flag == cell_count or horizontal[limit_or_flag] != 1)):
                return None
            horizontal[first:limit_or_flag] = [2] + [1] * (limit_or_flag - first - 1)

    merges = []
    index = 0
    while index < cell_count:
        flag = horizontal[index]
        if flag == 1:
            return None
        end = index + 1
        if flag in (2, 3):
            while end < cell_count and horizontal[end] == 1:
                end += 1
            if end == index + 1:
                return None
            merges.append({'first_cell': index, 'cell_count': end - index})
        index = end
    vertical_roles = {0: 'none', 1: 'continuation', 3: 'start'}
    if any(flag not in vertical_roles for flag in vertical):
        return None
    return {
        'scope': 'supported_row_operands',
        'cell_edges_twips': edges,
        'horizontal_merges': merges,
        'vertical_merge_roles': [vertical_roles[flag] for flag in vertical],
    }


def observe_table_operand(props, sprm, raw):
    """Bind supported direct row properties without applying visual formatting."""
    if sprm in ROW_GROUP_PROPERTIES:
        value = int.from_bytes(raw, 'little')
        props.setdefault('table_group_properties', {})[sprm] = value & 0xF0 if sprm == TABLE_POSITION_CODE else value
        if sprm == TABLE_STYLE_PROPERTY and 'initial_definition' in props:
            props['unsupported_cell_layout'] = True
    elif sprm in CELL_MERGE_OPERATIONS:
        if 'initial_definition' in props and len(raw) == 2:
            props.setdefault('merge_operands', []).append((sprm, bytes(raw)))
        else:
            props['unsupported_cell_layout'] = True
    elif sprm == TABLE_LEFT_ORIGIN:
        props['left_origin'] = int.from_bytes(raw, 'little', signed=True)
    elif sprm == TABLE_HALF_GAP:
        props['gap_half'] = int.from_bytes(raw, 'little', signed=True)
    elif sprm == OPAQUE_TABLE_PROPERTY and len(raw) == 4 and 'opaque_table_value' not in props:
        # Retain equality as evidence for a contiguous fragment; its meaning
        # is unspecified. A difference/duplicate makes grouping ambiguous.
        props['opaque_table_value'] = bytes(raw)
    elif sprm not in TABLE_TOPOLOGY_NEUTRAL:
        props['unsupported_cell_layout'] = True
        props['grouping_unknown'] = True


@dataclass(frozen=True)
class TableScanOptions:
    """Optional ranges, flags, and budgets passed to inspect_tables."""

    table_ranges: tuple = ()
    word_ranges: tuple = ()
    piece_properties: bool = False
    data_region: Any = None
    piece_prms: Any = None
    field_spans: tuple = ()
    marker_replacements: Optional[dict] = None
    data_ownership: Optional[set] = None
    include_layout: bool = True


def _crosses_field(field_spans, left, right):
    index = bisect_right(field_spans, (right,))
    return index > 0 and field_spans[index - 1][1] > left


def _record_unresolved(result, enhanced, cp, reason, cp_start=None):
    record = {'cp': cp, 'stream': 'WordDocument'}
    if enhanced:
        record['reason'] = reason
        if cp_start is not None:
            record['cp_start'] = cp_start
            record['cp_end'] = cp + 1
        result['unresolved_marker_count'] = result.get('unresolved_marker_count', 0) + 1
        if len(result['unresolved_markers']) >= MAX_UNRESOLVED_MARKERS:
            return
    result['unresolved_markers'].append(record)


def _build_table_row_record(cells, row_start, cp, props, enhanced):
    row = {
        'cells': cells,
        'source': {'stream': 'WordDocument', 'cp_start': row_start, 'cp_end': cp + 1, 'row_mark_cp': cp},
        'row_properties_source': props['source'],
        'merges': 'unknown',
        'external_properties_source': props.get('external_properties_source'),
        'initial_definition': props.get('initial_definition'),
        'uninterpreted_table_modifiers': props.get('uninterpreted_table_modifiers', 0),
        'uninterpreted_table_modifier_ids': props.get('uninterpreted_table_modifier_ids', {}),
        'uninterpreted_table_modifier_ids_omitted': props.get('uninterpreted_table_modifier_ids_omitted', 0),
        'local_width_operands': props.get('local_width_operands', []),
        'local_width_operands_omitted': props.get('local_width_operands_omitted', 0),
    }
    if enhanced:
        row['stored_cell_layout'] = row_definition(props, len(cells))
    return row


def _scan_text_marks(text, cp_start, word, opts, fkp, nested, state, require):
    enhanced = opts.piece_prms is not None
    result = state['result']
    has_cell_marks = state['has_cell_marks']
    cp = cp_start
    segment_start = cp_start
    chunks = []
    cells = []
    paragraph = []
    row_start = None
    first_paragraph = None
    cell_start = cp_start
    tainted = False
    total_cells = 0

    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if char in ('\r', '\x07'):
            segment_has_field = _crosses_field(opts.field_spans, segment_start, cp + 1)
            props = fkp.properties(cp, segment_start)
            if nested is not None and has_cell_marks:
                nested.feed(''.join(paragraph), char, segment_start, cp + width, props, segment_has_field)
            if enhanced and props and props.get('layout_modifier_count') and not segment_has_field:
                state['layout_seen'] += props['layout_modifier_count']
                modifiers = props.get('layout_modifiers', ())[:MAX_LAYOUT_MODIFIERS - state['layout_retained']]
                if modifiers:
                    state['layout_paragraphs'].append({
                        'stream': 'WordDocument', 'mark_cp': cp,
                        'modifiers': modifiers, 'papx_group_complete': not props['structure_unknown'],
                        'modifier_count': props['layout_modifier_count'],
                    })
                    state['layout_retained'] += len(modifiers)
            outside_table = (enhanced and char == '\r' and props and not props['structure_unknown']
                             and not props.get('in_table') and not props.get('depth') and not props.get('row_end'))
            if enhanced and has_cell_marks:
                if char == '\x07':
                    state['cell_marks'].append(cp)
                elif outside_table and not segment_has_field:
                    state['outside_paragraphs'].append(cp)
            if not has_cell_marks or (props and (props.get('structure_unknown')
                                                or props.get('in_table') != 1 or props.get('depth') != 1)):
                props = None
            if segment_has_field:
                tainted = True
                if not props or not props.get('row_end') or _crosses_field(opts.field_spans, cp, cp + 1):
                    props = None
            if props is None:
                tainted = (tainted or bool(cells or chunks) or char == '\x07') and not outside_table
                if char == '\x07':
                    _record_unresolved(result, enhanced, cp,
                                       'field_overlap' if segment_has_field else 'unverified_paragraph_properties')
                chunks, cells, row_start = [], [], None
                first_paragraph = None
            elif props.get('row_end', 0):
                if (char == '\x07' and not paragraph and not chunks and cells and not tainted
                        and not _crosses_field(opts.field_spans, row_start, cp + 1)):
                    row = _build_table_row_record(cells, row_start, cp, props, enhanced)
                    if enhanced:
                        state['row_identities'].append(row_identity(props, first_paragraph))
                    result['rows'].append(row)
                else:
                    _record_unresolved(result, enhanced, cp, 'row_boundary_incomplete', row_start)
                chunks, cells, row_start = [], [], None
                first_paragraph = None
                tainted = enhanced and char != '\x07'
            else:
                if row_start is None:
                    row_start = segment_start
                    cell_start = segment_start
                    first_paragraph = props if enhanced else None
                chunks.append(''.join(paragraph).replace('\x0b', '\n'))
                if char == '\x07':
                    require(len(cells) < 63, word.location(76), 'Too many cells in observed row')
                    require(total_cells < MAX_CELLS,
                            word.location(76), 'Binary table cell budget exceeded', 'resource_limit')
                    cells.append({
                        'index': len(cells), 'text': '\n'.join(chunks),
                        'source': {'stream': 'WordDocument', 'cp_start': cell_start, 'cp_end': cp + 1},
                        'properties_source': props['source'],
                        'external_properties_source': props.get('external_properties_source'),
                    })
                    total_cells += 1
                    chunks = []
                    cell_start = cp + 1
            paragraph = []
            segment_start = cp + width
        elif has_cell_marks:
            paragraph.append(opts.marker_replacements.get(cp, char) if opts.marker_replacements else char)
        cp += width
    if cells or chunks:
        result['issues'].append({'code': 'binary_table_unterminated_row', 'stream': 'WordDocument',
                                 'cp_start': row_start, 'cp_end': cp})
    result['status'] = ('rows_partial' if result['rows'] else 'unresolved') if has_cell_marks else 'no_cell_markers'
    state['end_cp'] = cp


def inspect_tables(text, cp_start, word, table, pieces, positions, fib_end,
                   options: Optional[TableScanOptions] = None, **legacy_kwargs):
    """Read evidenced rows and, in maximum mode, direct PAPX layout operands."""
    from read_wps import ParseError, require
    from wps_nested_tables import NestedRows
    opts = options if options is not None else TableScanOptions(**legacy_kwargs)
    enhanced = opts.piece_prms is not None
    nested = NestedRows(bookmarks=has_table_boundary_bookmarks(word)) if enhanced else None
    overlap_location = {'reason_code': 'overlapping_decoded_metadata'} if enhanced else {}
    result = {
        'schema_version': 'ms_doc_table_rows_v1', 'status': 'unresolved',
        'rows': [], 'unresolved_markers': [], 'table_grouping': 'unknown',
        'grid_and_merges': 'unknown', 'source': {'stream': 'WordDocument'},
        'issues': [],
        'layout_properties_not_rendered': [], 'unresolved_properties': [],
        'layout_property_count': 0, 'unresolved_property_count': 0,
        'external_property_references': [],
    }
    if not opts.include_layout:
        result['layout_metadata_requested'] = False
    marker_count = text.count('\x07')
    has_cell_marks = bool(marker_count)
    if not has_cell_marks and not enhanced:
        result['status'] = 'no_cell_markers'
        return result
    require(marker_count <= MAX_MARKS, word.location(76),
            'Binary table marker budget exceeded', 'resource_limit')
    result['marker_count'] = marker_count
    state = {
        'result': result, 'has_cell_marks': has_cell_marks,
        'layout_paragraphs': [], 'layout_seen': 0, 'layout_retained': 0,
        'row_identities': [], 'outside_paragraphs': [], 'cell_marks': [], 'end_cp': cp_start,
    }
    fkp = None
    try:
        length, bte, fc_limits = init_bte(
            word, table, opts.table_ranges, enhanced, opts.piece_properties, overlap_location, require)
        fkp = FkpPropertyReader(
            word=word, bte=bte, fc_limits=fc_limits, length=length,
            pieces=pieces, positions=positions, fib_end=fib_end, options=opts,
            limits={'max_property_bytes': MAX_PROPERTY_BYTES, 'max_layout_modifiers': MAX_LAYOUT_MODIFIERS},
            helpers={
                'require': require, 'layout_operand': layout_operand,
                'initial_cell_properties': initial_cell_properties,
                'observe_table_operand': observe_table_operand,
                'table_width_operand': table_width_operand, 'skipped_operand_end': skipped_operand_end,
                'constants': {
                    'LAYOUT_PROPERTIES': LAYOUT_PROPERTIES,
                    'TABLE_NEUTRAL_PARAGRAPH_WIDTHS': TABLE_NEUTRAL_PARAGRAPH_WIDTHS,
                    'TABLE_PROPERTIES': TABLE_PROPERTIES,
                },
            },
            result=result,
        )
        _scan_text_marks(text, cp_start, word, opts, fkp, nested, state, require)
    except ParseError as exc:
        if exc.status == 'resource_limit':
            raise
        if enhanced and exc.location.get('reason_code') == 'overlapping_decoded_metadata':
            raise
        result['rows'] = []
        if nested is not None:
            nested.rows.clear()
            nested.reject(cp_start, 'invalid_paragraph_metadata')
        result['status'] = 'unresolved'
        state['layout_paragraphs'], state['layout_seen'], state['layout_retained'] = [], 0, 0
        code = 'binary_table_metadata_invalid' if has_cell_marks else 'binary_paragraph_metadata_invalid'
        result['issues'].append({'code': code, **exc.location,
                                 'metadata_status': exc.status, 'message': str(exc)})
    text_units = cp_start + len(text.encode('utf-16le')) // 2
    if nested is not None and has_cell_marks:
        evidence = nested.finish(text_units)
        if evidence['rows'] or evidence['issue_count']:
            result['nested_row_evidence'] = evidence
    result['fkp_ranges'] = fkp.occupied_pages if fkp is not None else []
    ext_count = fkp.external_reference_count if fkp is not None else 0
    result['external_reference_count'] = ext_count
    result['external_references_truncated'] = max(0, ext_count - len(result['external_property_references']))
    result['layout_properties_truncated'] = max(0, result['layout_property_count'] - len(result['layout_properties_not_rendered']))
    result['unresolved_properties_truncated'] = max(0, result['unresolved_property_count'] - len(result['unresolved_properties']))
    if enhanced:
        if has_cell_marks:
            result['contiguous_row_runs'] = contiguous_row_runs(result['rows'])
        if opts.include_layout:
            result['paragraph_layout'] = {
                'scope': 'direct_papx_operands', 'effective_layout': 'unknown',
                'paragraphs': state['layout_paragraphs'], 'modifier_count': state['layout_seen'],
                'modifiers_omitted': state['layout_seen'] - state['layout_retained'],
            }
        result['unresolved_marker_count'] = result.get('unresolved_marker_count', 0)
        result['unresolved_markers_omitted'] = result['unresolved_marker_count'] - len(result['unresolved_markers'])
        if has_cell_marks:
            group_tables(result, state['row_identities'], (cp_start, state['end_cp']),
                         state['outside_paragraphs'], state['cell_marks'],
                         bookmarks=has_table_boundary_bookmarks(word))
    if has_cell_marks:
        result['issues'].append({
            'code': 'binary_table_structure_incomplete', 'stream': 'WordDocument',
            'cp_start': cp_start, 'cp_end': text_units,
            'message': ('Stored depth-one table evidence only; check grouping/merge coverage. '
                        'Complete nested grouping, styles and visual layout remain unverified' if enhanced else
                        'Only explicit depth-one PAPX row/cell boundaries; grouping, merges, '
                        'styles and unsupported properties remain unknown'),
        })
    return result
