"""Group verified MS-DOC rows using stored boundaries and direct properties."""

from bisect import bisect_left

TABLE_POSITION_CODE = 0x360D  # TPc
PARAGRAPH_POSITION_CODE = 0x261B  # PPc
INLINE_POSITION_CODE = 0xF0  # Both PositionCodeOperand axes are "None".
ROW_POSITION_DEFAULTS = {
    TABLE_POSITION_CODE: INLINE_POSITION_CODE,
    0x3465: 0,  # TFNoAllowOverlap
    0x940E: 0, 0x940F: 0,  # TDxaAbs, TDyaAbs
    0x9410: 0, 0x9411: 0, 0x941E: 0, 0x941F: 0,  # distances from text
}
TABLE_STYLE_PROPERTY = 0x563A
ROW_GROUP_PROPERTIES = {0x7469, TABLE_STYLE_PROPERTY, 0x560B, 0x5664, *ROW_POSITION_DEFAULTS}
FRAME_PROPERTY_WIDTHS = {
    PARAGRAPH_POSITION_CODE: 1, 0x8418: 2, 0x8419: 2, 0x841A: 2,  # origin, horizontal/vertical position, width
    0x442B: 2, 0x442C: 2, 0x2423: 1,  # height, drop cap, wrapping
    0x842F: 2, 0x842E: 2, 0x2430: 1, 0x2462: 1, 0x443A: 2,  # distances, lock, overlap, flow
}
FRAME_POSITION_INDEX = tuple(FRAME_PROPERTY_WIDTHS).index(PARAGRAPH_POSITION_CODE)
STRUCTURED_OR_PROTECTED_BOOKMARK_PAIRS = (137, 138, 139, 141, 142, 143)
PROVEN_BOUNDARIES = frozenset((
    'story_start', 'story_end', 'outside_table_paragraph', 'different_row_properties', 'different_paragraph_frame',
))
DEFAULT_ROW_POSITION = tuple(ROW_POSITION_DEFAULTS.values())


def has_table_boundary_bookmarks(word):
    """Read the optional FibRgFcLcb2003 size fields, not the bookmark content."""
    if word.number(152, 2) < 164:
        return False
    return any(word.number(154 + 8 * index + 4) for index in STRUCTURED_OR_PROTECTED_BOOKMARK_PAIRS)


def row_identity(row_props, first_paragraph):
    """Keep only the stored values involved in the MS-DOC table boundary rule."""
    if row_props.get('grouping_unknown'):
        return None
    values = row_props.get('table_group_properties', {})
    table_bidi = values.get(0x560B, 0), values.get(0x5664, 0)
    if any(value not in (0, 1) for value in (*table_bidi, values.get(0x3465, 0))):
        return None
    placement = tuple(values.get(code, default) for code, default in ROW_POSITION_DEFAULTS.items())
    inherited_frame = None
    if first_paragraph is not None and not first_paragraph.get('grouping_unknown'):
        stored_frame = first_paragraph.get('frame', {})
        inherited_frame = (first_paragraph['istd'], tuple(stored_frame.get(code) for code in FRAME_PROPERTY_WIDTHS))
    return {
        'row_properties': (values.get(0x7469), values.get(TABLE_STYLE_PROPERTY, 0x000B), any(table_bidi), placement),
        'uses_paragraph_frame': placement == DEFAULT_ROW_POSITION,
        'first_paragraph': inherited_frame,
        'opaque': row_props.get('opaque_table_value'),
    }


def _adjacent_boundary(left, right, bookmarks):
    """Return None for a join; otherwise describe the stored boundary evidence."""
    if left is None or right is None:
        return 'unresolved_row_properties'
    if left['row_properties'] != right['row_properties']:
        return 'different_row_properties'
    if left['opaque'] != right['opaque']:
        return 'unresolved_opaque_table_property'
    if left['uses_paragraph_frame']:
        first, second = left['first_paragraph'], right['first_paragraph']
        if first is None or second is None:
            return 'unresolved_first_paragraph'
        if first != second:
            has_frame = any(frame[FRAME_POSITION_INDEX] != INLINE_POSITION_CODE for _, frame in (first, second))
            if has_frame and any(a is not None and b is not None and a != b for a, b in zip(first[1], second[1])):
                return 'different_paragraph_frame'
            return 'unresolved_paragraph_frame'
    return 'unresolved_table_bookmarks' if bookmarks else None


def _gap_boundary(start, end, outside_paragraphs, cell_marks):
    # At least one proven outside-table paragraph separates the runs. An
    # unassigned cell/row mark in the gap could extend either recovered table.
    if bisect_left(cell_marks, start) == bisect_left(cell_marks, end):
        if bisect_left(outside_paragraphs, start) < bisect_left(outside_paragraphs, end):
            return 'outside_table_paragraph'
    return 'unresolved_row_gap'


def group_tables(result, identities, story_span, outside_paragraphs, cell_marks, *, bookmarks):
    """Index row fragments and attach merge coverage to each stored table."""
    from wps_binary_table_merges import analyze_table_merges

    rows = result['rows']
    tables = result['tables'] = []
    if not rows:
        return
    story_start, story_end = story_span

    def source(index, key):
        return rows[index]['source'][key]

    def emit(first, limit, start_boundary, end_boundary):
        stored = start_boundary in PROVEN_BOUNDARIES and end_boundary in PROVEN_BOUNDARIES
        merge_details = analyze_table_merges(rows[first:limit], uncertain_end=end_boundary not in PROVEN_BOUNDARIES)
        tables.append({
            'table_index': len(tables), 'first_row_index': first, 'row_count': limit - first,
            'source': {'stream': 'WordDocument', 'cp_start': source(first, 'cp_start'), 'cp_end': source(limit - 1, 'cp_end')},
            'grouping_status': 'stored' if stored else 'partial',
            'start_boundary': start_boundary, 'end_boundary': end_boundary,
            **merge_details,
        })

    first = 0
    first_cp = source(0, 'cp_start')
    start_boundary = ('story_start' if first_cp == story_start else
                      _gap_boundary(story_start, first_cp, outside_paragraphs, cell_marks))
    for index in range(1, len(rows)):
        left = source(index - 1, 'cp_end')
        right = source(index, 'cp_start')
        boundary = (_adjacent_boundary(identities[index - 1], identities[index], bookmarks) if left == right else
                    _gap_boundary(left, right, outside_paragraphs, cell_marks))
        if boundary is not None:
            emit(first, index, start_boundary, boundary)
            first, start_boundary = index, boundary
    last_cp = source(len(rows) - 1, 'cp_end')
    end_boundary = ('story_end' if last_cp == story_end else
                    _gap_boundary(last_cp, story_end, outside_paragraphs, cell_marks))
    emit(first, len(rows), start_boundary, end_boundary)

    stored = result['unresolved_marker_count'] == 0 and all(table['grouping_status'] == 'stored' for table in tables)
    result['table_grouping'] = 'stored' if stored else 'partial'
    if stored and all(table['merge_status'] == 'stored' and table['column_edges_twips'] is not None for table in tables):
        result['grid_and_merges'] = 'stored'
    elif any(table['merge_status'] != 'unknown' for table in tables):
        result['grid_and_merges'] = 'partial'
