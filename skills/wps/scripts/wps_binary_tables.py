"""Bounded PAPX evidence for rows, supported cell layouts and paragraph marks."""
from bisect import bisect_right
from itertools import accumulate

from wps_binary_table_groups import (
    FRAME_PROPERTY_WIDTHS, PARAGRAPH_POSITION_CODE, ROW_GROUP_PROPERTIES, TABLE_POSITION_CODE, TABLE_STYLE_PROPERTY,
    group_tables, has_table_boundary_bookmarks, row_identity,
)

MAX_PAGES = 4096
MAX_MARKS = 100000
MAX_CELLS = 100000
MAX_PROPERTY_BYTES = 1024 * 1024
MAX_LAYOUT_MODIFIERS = 1000
MAX_UNRESOLVED_MARKERS = 1000
TABLE_PROPERTIES = {0x2416: 'in_table', 0x2417: 'row_end', 0x6649: 'depth'}
# MS-DOC 2.6.2. These fixed-width operands do not change table depth or
# row/cell marks. Preserve the unknown-operand stop rule for other SPRMs.
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


def inspect_tables(text, cp_start, word, table, pieces, positions, fib_end,
                   table_ranges=(), word_ranges=(), piece_properties=False, data_region=None,
                   piece_prms=None, field_spans=(), marker_replacements=None, data_ownership=None):
    """Read evidenced rows and, in maximum mode, direct PAPX layout operands."""
    from read_wps import ParseError, require
    enhanced = piece_prms is not None
    overlap_location = {'reason_code': 'overlapping_decoded_metadata'} if enhanced else {}
    result = {'schema_version': 'ms_doc_table_rows_v1', 'status': 'unresolved',
              'rows': [], 'unresolved_markers': [], 'table_grouping': 'unknown',
              'grid_and_merges': 'unknown', 'source': {'stream': 'WordDocument'},
              'issues': []}
    result['layout_properties_not_rendered'] = []
    result['unresolved_properties'] = []
    result['layout_property_count'] = 0
    result['unresolved_property_count'] = 0
    result['external_property_references'] = []
    external_reference_count = 0
    external_ranges = []
    marker_count = text.count('\x07')
    has_cell_marks = bool(marker_count)
    if not has_cell_marks and not enhanced:
        result['status'] = 'no_cell_markers'
        return result
    pair = 154 + 13 * 8
    start, length = word.number(pair), word.number(pair + 4)
    require(marker_count <= MAX_MARKS, word.location(76),
            'Binary table marker budget exceeded', 'resource_limit')
    result['marker_count'] = marker_count
    pages = {}
    fc_limits = []
    bte = None
    property_bytes = 0
    layout_paragraphs = []
    layout_seen = 0
    layout_retained = 0
    occupied_pages = []
    row_identities = []
    outside_paragraphs = []
    cell_marks = []
    physical = sorted(pieces)
    physical_starts = [piece[0] for piece in physical]
    prm_counts = tuple(accumulate(map(bool, piece_prms), initial=0)) if enhanced and length else ()

    def unresolved(cp, reason, cp_start=None):
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

    def crosses_field(left, right):
        # (right,) sorts before any span that starts at the exclusive end.
        index = bisect_right(field_spans, (right,))
        return index > 0 and field_spans[index - 1][1] > left

    def properties(cp, segment_start):
        nonlocal property_bytes, external_reference_count
        if not length or (piece_properties and not enhanced):
            return None
        index = bisect_right(positions, cp) - 1
        if enhanced:
            first_piece = bisect_right(positions, segment_start) - 1
            if prm_counts[index + 1] != prm_counts[first_piece]:
                return None
        offset, _, stride, _ = pieces[index]
        fc = offset + (cp - positions[index]) * stride
        page_index = bisect_right(fc_limits, fc) - 1
        if not 0 <= page_index < len(fc_limits) - 1:
            return None
        page_offset = (bte.number(4 * len(fc_limits) + page_index * 4) & 0x3FFFFF) * 512
        if page_offset not in pages:
            require(len(pages) < MAX_PAGES, word.location(page_offset),
                    'Paragraph FKP page budget exceeded', 'resource_limit')
            require(page_offset >= fib_end, word.location(page_offset), 'Paragraph FKP overlaps FIB')
            for left, right in word_ranges:
                require(page_offset + 512 <= left or right <= page_offset,
                        word.location(page_offset, **overlap_location),
                        'Paragraph FKP overlaps decoded metadata')
            first = max(0, bisect_right(physical_starts, page_offset) - 1)
            last = bisect_right(physical_starts, page_offset + 511)
            for left, right, _, _ in physical[first:last]:
                require(page_offset + 512 <= left or right <= page_offset,
                        word.location(page_offset), 'Paragraph FKP overlaps text')
            page = word.region(page_offset, 512)
            count = page.number(511, 1)
            require(1 <= count <= 29, page.location(511), 'Invalid paragraph FKP count')
            offsets = [page.number(i * 4) for i in range(count + 1)]
            require(all(a < b for a, b in zip(offsets, offsets[1:])),
                    page.location(), 'Paragraph FKP offsets not increasing')
            require(offsets[0] == fc_limits[page_index] and offsets[-1] == fc_limits[page_index + 1],
                    page.location(), 'Paragraph BTE/FKP range mismatch')
            groups = []
            spans = []
            header_end = 4 * (count + 1) + 13 * count
            for run in range(count):
                pointer = page.number(4 * (count + 1) + 13 * run, 1) * 2
                if not pointer:
                    groups.append(None)
                    continue
                require(header_end <= pointer < 511, page.location(pointer), 'Paragraph property pointer overlaps arrays')
                size = page.number(pointer, 1)
                cursor = pointer + 1
                if size:
                    size = 2 * size - 1
                else:
                    size = 2 * page.number(cursor, 1)
                    cursor += 1
                stop = cursor + size
                require(size >= 2 and stop <= 511, page.location(pointer), 'Invalid paragraph property length')
                require(all((pointer, stop) == span or stop <= span[0] or span[1] <= pointer for span in spans),
                        page.location(pointer), 'Overlapping paragraph property groups')
                spans.append((pointer, stop))
                property_bytes += size
                require(property_bytes <= MAX_PROPERTY_BYTES, page.location(pointer),
                        'Paragraph property budget exceeded', 'resource_limit')
                style = page.number(cursor, 2)
                cursor += 2
                props = {'source': page.location(pointer), 'istd': style}
                valid = True
                properties_region = page
                array_start = cursor
                visited_external = set()
                while cursor < stop:
                    require(stop - cursor >= 2, properties_region.location(cursor), 'Truncated paragraph SPRM')
                    sprm = properties_region.number(cursor, 2)
                    cursor += 2
                    if sprm in (0x6646, 0x646B):
                        require(cursor + 4 <= stop, properties_region.location(cursor), 'Truncated external paragraph property reference')
                        external_offset = properties_region.number(cursor, 4)
                        if sprm == 0x6646 and cursor - 2 != array_start:
                            # The specification explicitly ignores non-first HugePapx.
                            cursor += 4
                            continue
                        if properties_region is page and sprm == 0x6646:
                            require(style == 0 and cursor + 4 == stop, properties_region.location(cursor),
                                    'Inline HugePapx requires zero style and a sole PRL')
                        require(data_region is not None, properties_region.location(cursor), 'External paragraph Data stream missing')
                        require(external_offset not in visited_external, data_region.location(external_offset),
                                'External paragraph property reference cycle')
                        require(len(visited_external) < 32 and external_reference_count < 4096,
                                data_region.location(external_offset), 'External paragraph reference budget exceeded', 'resource_limit')
                        visited_external.add(external_offset)
                        external_reference_count += 1
                        size = data_region.number(external_offset, 2)
                        require(10 <= size <= 0x3FA2, data_region.location(external_offset), 'Invalid external paragraph property length')
                        external_end = external_offset + 2 + size
                        data_region.view(external_offset + 2, size)
                        require(all((external_offset, external_end) == span or external_end <= span[0] or span[1] <= external_offset
                                    for span in external_ranges), data_region.location(external_offset),
                                'Overlapping external paragraph property arrays')
                        if (external_offset, external_end) not in external_ranges:
                            external_ranges.append((external_offset, external_end))
                        if data_ownership is not None:
                            data_ownership.add((external_offset, external_end))
                        property_bytes += size
                        require(property_bytes <= MAX_PROPERTY_BYTES, data_region.location(external_offset),
                                'Paragraph property budget exceeded', 'resource_limit')
                        if len(result['external_property_references']) < 100:
                            result['external_property_references'].append({
                                'sprm': hex(sprm), 'source': properties_region.location(cursor - 2),
                                'target': data_region.location(external_offset), 'property_bytes': size})
                        props['external_properties_source'] = data_region.location(external_offset + 2)
                        # Process the referenced array, never the original array suffix.
                        properties_region = data_region
                        cursor = external_offset + 2
                        stop = external_end
                        array_start = cursor
                        continue
                    if sprm in LAYOUT_PROPERTIES:
                        name, width = LAYOUT_PROPERTIES[sprm]
                        require(cursor + width <= stop, properties_region.location(cursor), 'Truncated known paragraph layout property')
                        if enhanced and sprm in FRAME_PROPERTY_WIDTHS:
                            value = properties_region.number(cursor, width)
                            props.setdefault('frame', {})[sprm] = value & 0xF0 if sprm == PARAGRAPH_POSITION_CODE else value
                        result['layout_property_count'] += 1
                        if enhanced:
                            props['layout_modifier_count'] = props.get('layout_modifier_count', 0) + 1
                        keep_raw = len(result['layout_properties_not_rendered']) < 100
                        keep_typed = enhanced and result['layout_property_count'] <= MAX_LAYOUT_MODIFIERS
                        if keep_raw or keep_typed:
                            raw = properties_region.view(cursor, width)
                            modifier = {'sprm': hex(sprm), 'name': name, 'operand_hex': bytes(raw).hex(),
                                        'source': properties_region.location(cursor)}
                            if keep_raw:
                                result['layout_properties_not_rendered'].append({**modifier, 'effect': 'layout_not_rendered'})
                            if keep_typed:
                                props.setdefault('layout_modifiers', []).append({**modifier, **layout_operand(sprm, raw)})
                        cursor += width
                        continue
                    if enhanced and sprm in FRAME_PROPERTY_WIDTHS:
                        width = FRAME_PROPERTY_WIDTHS[sprm]
                        require(cursor + width <= stop, properties_region.location(cursor), 'Truncated paragraph frame property')
                        props.setdefault('frame', {})[sprm] = properties_region.number(cursor, width)
                        cursor += width
                        continue
                    if enhanced and sprm in TABLE_NEUTRAL_PARAGRAPH_WIDTHS:
                        width = TABLE_NEUTRAL_PARAGRAPH_WIDTHS[sprm]
                        require(cursor + width <= stop, properties_region.location(cursor),
                                'Truncated fixed-width paragraph property')
                        cursor += width
                        continue
                    if enhanced and sprm == 0x4600:
                        # PIstd can reset formatting, but preserves the explicit
                        # in-table, depth and table-termination properties.
                        require(cursor + 2 <= stop, properties_region.location(cursor), 'Truncated paragraph style operand')
                        if 'initial_definition' in props:
                            props['unsupported_cell_layout'] = True
                        props['istd'] = properties_region.number(cursor, 2)
                        props.pop('frame', None)
                        cursor += 2
                        continue
                    if enhanced and sprm == 0x664A:
                        require(cursor + 4 <= stop, properties_region.location(cursor), 'Truncated relative table depth')
                        delta = int.from_bytes(properties_region.view(cursor, 4), 'little', signed=True)
                        depth = props.get('depth', 0) + delta
                        require(0 <= depth <= 0x7FFFFFFF, properties_region.location(cursor), 'Invalid resulting table depth')
                        props['depth'] = depth
                        cursor += 4
                        continue
                    # Unrecognized operands may contain exceptional variable lengths.
                    # Stop rather than scan their bytes for apparent table flags.
                    if sprm == 0xD608:  # TDefTable's exceptional two-byte length
                        require(cursor + 2 <= stop, properties_region.location(cursor), 'Truncated table definition length')
                        definition_end = cursor + properties_region.number(cursor, 2) + 1
                        cursor += 2
                        require(cursor < definition_end <= stop, properties_region.location(cursor), 'Invalid table definition extent')
                        columns = properties_region.number(cursor, 1)
                        require(columns <= 63 and cursor + 1 + 2 * (columns + 1) <= definition_end,
                                properties_region.location(cursor), 'Invalid table definition columns')
                        edges = [int.from_bytes(properties_region.view(cursor + 1 + 2 * i, 2), 'little', signed=True)
                                 for i in range(columns + 1)]
                        require(all(a <= b for a, b in zip(edges, edges[1:])),
                                properties_region.location(cursor), 'Table definition edges not ordered')
                        # The initial records remain available even when later
                        # operands make the supported stored grid indeterminate.
                        tc_start = cursor + 1 + 2 * (columns + 1)
                        tc_bytes = properties_region.view(tc_start, definition_end - tc_start)
                        if enhanced and 'initial_definition' in props:
                            props['unsupported_cell_layout'] = True
                        props['initial_definition'] = {'column_count': columns, 'edges_twips': edges,
                            'remaining_property_bytes': len(tc_bytes),
                            'initial_cell_properties': initial_cell_properties(tc_bytes, columns)}
                        cursor = definition_end
                        continue
                    if sprm not in TABLE_PROPERTIES:
                        if (sprm >> 10) & 7 == 5:
                            width = (1, 1, 2, 4, 2, 2, None, 3)[sprm >> 13]
                            if width is None:
                                require(cursor < stop, properties_region.location(cursor), 'Missing table property length')
                                width = properties_region.number(cursor, 1)
                                cursor += 1
                            require(cursor + width <= stop, properties_region.location(cursor), 'Truncated table property')
                            if enhanced:
                                observe_table_operand(props, sprm, properties_region.view(cursor, width))
                            if sprm in (0xD634, 0xD635):
                                operands = props.setdefault('local_width_operands', [])
                                if len(operands) < 128:
                                    operands.append(table_width_operand(sprm, properties_region.view(cursor, width),
                                                                        properties_region.location(cursor)))
                                else:
                                    props['local_width_operands_omitted'] = props.get('local_width_operands_omitted', 0) + 1
                            cursor += width
                            props['uninterpreted_table_modifiers'] = props.get('uninterpreted_table_modifiers', 0) + 1
                            kinds = props.setdefault('uninterpreted_table_modifier_ids', {})
                            key = hex(sprm)
                            if key in kinds or len(kinds) < 64:
                                kinds[key] = kinds.get(key, 0) + 1
                            else:
                                props['uninterpreted_table_modifier_ids_omitted'] = props.get(
                                    'uninterpreted_table_modifier_ids_omitted', 0) + 1
                            continue
                        result['unresolved_property_count'] += 1
                        if len(result['unresolved_properties']) < 100:
                            result['unresolved_properties'].append({
                                'sprm': hex(sprm), 'source': properties_region.location(cursor - 2),
                                'reason': 'external_paragraph_properties_not_followed' if sprm == 0x6646 else 'unknown_property_not_skipped'})
                        valid = False
                        break
                    width = 4 if sprm == 0x6649 else 1
                    require(cursor + width <= stop, properties_region.location(cursor), 'Truncated table SPRM')
                    value = properties_region.number(cursor, width)
                    if width == 1:
                        require(value in (0, 1), properties_region.location(cursor), 'Invalid table boolean')
                    elif enhanced:
                        require(value <= 0x7FFFFFFF, properties_region.location(cursor), 'Invalid absolute table depth')
                    props[TABLE_PROPERTIES[sprm]] = value
                    cursor += width
                if enhanced:
                    props['structure_unknown'] = not valid
                groups.append(props if valid or enhanced else None)
            pages[page_offset] = (offsets, groups)
            occupied_pages.append((page_offset, page_offset + 512))
        offsets, groups = pages[page_offset]
        run = bisect_right(offsets, fc) - 1
        if not 0 <= run < len(groups) or offsets[run + 1] != fc + stride:
            return None
        return groups[run]

    try:
        if length and (enhanced or not piece_properties):
            require(start > 0 and length >= 12 and (length - 4) % 8 == 0,
                    word.location(pair), 'Invalid paragraph BTE')
            require((length - 4) // 8 <= MAX_PAGES, word.location(pair),
                    'Paragraph BTE page budget exceeded', 'resource_limit')
            bte = table.region(start, length)
            require(all(start + length <= left or right <= start for left, right in table_ranges),
                    bte.location(**overlap_location), 'Paragraph BTE overlaps decoded table')
            fc_limits = [bte.number(i * 4) for i in range((length - 4) // 8 + 1)]
            require(all(a < b for a, b in zip(fc_limits, fc_limits[1:])),
                    bte.location(), 'Paragraph BTE offsets not increasing')
            require(fc_limits[-1] <= len(word.data), bte.location(), 'Paragraph BTE outside WordDocument')
            page_numbers = [bte.number(4 * len(fc_limits) + i * 4) & 0x3FFFFF
                            for i in range(len(fc_limits) - 1)]
            require(len(set(page_numbers)) == len(page_numbers), bte.location(), 'Aliased paragraph FKP')
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
                segment_has_field = crosses_field(segment_start, cp + 1)
                props = properties(cp, segment_start)
                if enhanced and props and props.get('layout_modifier_count') and not segment_has_field:
                    layout_seen += props['layout_modifier_count']
                    modifiers = props.get('layout_modifiers', ())[:MAX_LAYOUT_MODIFIERS - layout_retained]
                    if modifiers:
                        layout_paragraphs.append({'stream': 'WordDocument', 'mark_cp': cp,
                                                  'modifiers': modifiers, 'papx_group_complete': not props['structure_unknown'],
                                                  'modifier_count': props['layout_modifier_count']})
                        layout_retained += len(modifiers)
                outside_table = (enhanced and char == '\r' and props and not props['structure_unknown']
                                 and not props.get('in_table') and not props.get('depth') and not props.get('row_end'))
                if enhanced and has_cell_marks:
                    if char == '\x07':
                        cell_marks.append(cp)
                    elif outside_table and not segment_has_field:
                        outside_paragraphs.append(cp)
                # PFTtp defaults to false. Only the explicit value 1 ends a row.
                if not has_cell_marks or (props and (props.get('structure_unknown')
                                                    or props.get('in_table') != 1 or props.get('depth') != 1)):
                    props = None
                if segment_has_field:
                    tainted = True
                    # A row boundary can be trusted only outside the field.
                    if not props or not props.get('row_end') or crosses_field(cp, cp + 1):
                        props = None
                if props is None:
                    tainted = (tainted or bool(cells or chunks) or char == '\x07') and not outside_table
                    if char == '\x07':
                        unresolved(cp, 'field_overlap' if segment_has_field else 'unverified_paragraph_properties')
                    chunks, cells, row_start = [], [], None
                    first_paragraph = None
                elif props.get('row_end', 0):
                    if (char == '\x07' and not paragraph and not chunks and cells and not tainted
                            and not crosses_field(row_start, cp + 1)):
                        row = {'cells': cells, 'source': {'stream': 'WordDocument',
                            'cp_start': row_start, 'cp_end': cp + 1, 'row_mark_cp': cp},
                            'row_properties_source': props['source'], 'merges': 'unknown',
                            'external_properties_source': props.get('external_properties_source'),
                            'initial_definition': props.get('initial_definition'),
                            'uninterpreted_table_modifiers': props.get('uninterpreted_table_modifiers', 0),
                            'uninterpreted_table_modifier_ids': props.get('uninterpreted_table_modifier_ids', {}),
                            'uninterpreted_table_modifier_ids_omitted': props.get('uninterpreted_table_modifier_ids_omitted', 0),
                            'local_width_operands': props.get('local_width_operands', []),
                            'local_width_operands_omitted': props.get('local_width_operands_omitted', 0)}
                        if enhanced:
                            row['stored_cell_layout'] = row_definition(props, len(cells))
                            row_identities.append(row_identity(props, first_paragraph))
                        result['rows'].append(row)
                    else:
                        unresolved(cp, 'row_boundary_incomplete', row_start)
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
                        cells.append({'index': len(cells), 'text': '\n'.join(chunks),
                            'source': {'stream': 'WordDocument', 'cp_start': cell_start,
                                       'cp_end': cp + 1}, 'properties_source': props['source'],
                                      'external_properties_source': props.get('external_properties_source')})
                        total_cells += 1
                        chunks = []
                        cell_start = cp + 1
                paragraph = []
                segment_start = cp + width
            elif has_cell_marks:
                paragraph.append(marker_replacements.get(cp, char) if marker_replacements else char)
            cp += width
        if cells or chunks:
            result['issues'].append({'code': 'binary_table_unterminated_row', 'stream': 'WordDocument',
                                     'cp_start': row_start, 'cp_end': cp})
        result['status'] = ('rows_partial' if result['rows'] else 'unresolved') if has_cell_marks else 'no_cell_markers'
    except ParseError as exc:
        if exc.status == 'resource_limit':
            raise
        if enhanced and exc.location.get('reason_code') == 'overlapping_decoded_metadata':
            raise
        result['rows'] = []
        result['status'] = 'unresolved'
        layout_paragraphs, layout_seen, layout_retained = [], 0, 0
        code = 'binary_table_metadata_invalid' if has_cell_marks else 'binary_paragraph_metadata_invalid'
        result['issues'].append({'code': code, **exc.location,
                                 'metadata_status': exc.status, 'message': str(exc)})
    result['fkp_ranges'] = occupied_pages
    result['external_reference_count'] = external_reference_count
    result['external_references_truncated'] = max(0, external_reference_count - len(result['external_property_references']))
    result['layout_properties_truncated'] = max(0, result['layout_property_count'] - len(result['layout_properties_not_rendered']))
    result['unresolved_properties_truncated'] = max(0, result['unresolved_property_count'] - len(result['unresolved_properties']))
    if enhanced:
        if has_cell_marks:
            result['contiguous_row_runs'] = contiguous_row_runs(result['rows'])
        result['paragraph_layout'] = {'scope': 'direct_papx_operands', 'effective_layout': 'unknown',
                                      'paragraphs': layout_paragraphs, 'modifier_count': layout_seen,
                                      'modifiers_omitted': layout_seen - layout_retained}
        result['unresolved_marker_count'] = result.get('unresolved_marker_count', 0)
        result['unresolved_markers_omitted'] = result['unresolved_marker_count'] - len(result['unresolved_markers'])
        if has_cell_marks:
            group_tables(result, row_identities, (cp_start, cp), outside_paragraphs, cell_marks,
                         bookmarks=has_table_boundary_bookmarks(word))
    if has_cell_marks:
        result['issues'].append({'code': 'binary_table_structure_incomplete', 'stream': 'WordDocument',
                                'cp_start': cp_start, 'cp_end': cp_start + len(text.encode('utf-16le')) // 2,
                                'message': ('Stored depth-one table evidence only; check grouping/merge coverage. '
                                            'Nested tables, styles and visual layout remain unverified' if enhanced else
                                            'Only explicit depth-one PAPX row/cell boundaries; grouping, merges, '
                                            'styles and unsupported properties remain unknown')})
    return result
