"""Bounded direct-PAPX row/cell evidence; no grid, merge or table grouping claims."""
from bisect import bisect_right

MAX_PAGES = 4096
MAX_MARKS = 100000
MAX_CELLS = 100000
MAX_PROPERTY_BYTES = 1024 * 1024
TABLE_PROPERTIES = {0x2416: 'in_table', 0x2417: 'row_end', 0x6649: 'depth'}
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


def inspect_tables(text, cp_start, word, table, pieces, positions, fib_end,
                   table_ranges=(), word_ranges=(), piece_properties=False, data_region=None):
    """Return observed depth-one rows only when every paragraph has direct evidence."""
    from read_wps import ParseError, require
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
    if '\x07' not in text:
        result['status'] = 'no_cell_markers'
        return result
    marker_count = text.count('\x07')
    require(marker_count <= MAX_MARKS, word.location(76),
            'Binary table marker budget exceeded', 'resource_limit')
    result['marker_count'] = marker_count
    pair = 154 + 13 * 8
    start, length = word.number(pair), word.number(pair + 4)
    pages = {}
    fc_limits = []
    bte = None
    property_bytes = 0
    occupied_pages = []
    physical = sorted(pieces)
    physical_starts = [piece[0] for piece in physical]

    def properties(cp):
        nonlocal property_bytes, external_reference_count
        if piece_properties or not length:
            return None
        index = bisect_right(positions, cp) - 1
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
                        word.location(page_offset), 'Paragraph FKP overlaps decoded metadata')
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
                        result['layout_property_count'] += 1
                        if len(result['layout_properties_not_rendered']) < 100:
                            result['layout_properties_not_rendered'].append({
                                'sprm': hex(sprm), 'name': name, 'operand_hex': bytes(properties_region.view(cursor, width)).hex(),
                                'source': properties_region.location(cursor), 'effect': 'layout_not_rendered'})
                        cursor += width
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
                        # Retain initial boundaries only. TC80 and later table changes
                        # can alter display/merges; never promote these to a grid.
                        tc_start = cursor + 1 + 2 * (columns + 1)
                        tc_bytes = properties_region.view(tc_start, definition_end - tc_start)
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
                    props[TABLE_PROPERTIES[sprm]] = value
                    cursor += width
                groups.append(props if valid else None)
            pages[page_offset] = (offsets, groups)
            occupied_pages.append((page_offset, page_offset + 512))
        offsets, groups = pages[page_offset]
        run = bisect_right(offsets, fc) - 1
        if not 0 <= run < len(groups) or offsets[run + 1] != fc + stride:
            return None
        props = groups[run]
        # MS-DOC 2.6.2 PFTtp defaults to false; a row still ends only at
        # an explicit PFTtp=1 mark. Do not require redundant false operands.
        if props is None or props.get('in_table') != 1 or props.get('depth') != 1:
            return None
        return props

    try:
        if length and not piece_properties:
            require(start > 0 and length >= 12 and (length - 4) % 8 == 0,
                    word.location(pair), 'Invalid paragraph BTE')
            require((length - 4) // 8 <= MAX_PAGES, word.location(pair),
                    'Paragraph BTE page budget exceeded', 'resource_limit')
            bte = table.region(start, length)
            require(all(start + length <= left or right <= start for left, right in table_ranges),
                    bte.location(), 'Paragraph BTE overlaps decoded table')
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
        cell_start = cp_start
        tainted = False
        total_cells = 0
        for char in text:
            width = 2 if ord(char) > 0xFFFF else 1
            if char in ('\r', '\x07'):
                props = properties(cp)
                if props is None:
                    tainted = tainted or bool(cells or chunks) or char == '\x07'
                    if char == '\x07':
                        result['unresolved_markers'].append({'cp': cp, 'stream': 'WordDocument'})
                    chunks, cells, row_start = [], [], None
                elif props.get('row_end', 0):
                    if char == '\x07' and not paragraph and not chunks and cells and not tainted:
                        result['rows'].append({'cells': cells, 'source': {'stream': 'WordDocument',
                            'cp_start': row_start, 'cp_end': cp + 1, 'row_mark_cp': cp},
                            'row_properties_source': props['source'], 'merges': 'unknown',
                            'external_properties_source': props.get('external_properties_source'),
                            'initial_definition': props.get('initial_definition'),
                            'uninterpreted_table_modifiers': props.get('uninterpreted_table_modifiers', 0),
                            'uninterpreted_table_modifier_ids': props.get('uninterpreted_table_modifier_ids', {}),
                            'uninterpreted_table_modifier_ids_omitted': props.get('uninterpreted_table_modifier_ids_omitted', 0),
                            'local_width_operands': props.get('local_width_operands', []),
                            'local_width_operands_omitted': props.get('local_width_operands_omitted', 0)})
                    else:
                        result['unresolved_markers'].append({'cp': cp, 'stream': 'WordDocument'})
                    chunks, cells, row_start = [], [], None
                    tainted = False
                else:
                    if row_start is None:
                        row_start = segment_start
                        cell_start = segment_start
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
            else:
                paragraph.append(char)
            cp += width
        if cells or chunks:
            result['issues'].append({'code': 'binary_table_unterminated_row', 'stream': 'WordDocument',
                                     'cp_start': row_start, 'cp_end': cp})
        result['status'] = 'rows_partial' if result['rows'] else 'unresolved'
    except ParseError as exc:
        if exc.status == 'resource_limit':
            raise
        result['rows'] = []
        result['status'] = 'unresolved'
        result['issues'].append({'code': 'binary_table_metadata_invalid', **exc.location,
                                 'metadata_status': exc.status, 'message': str(exc)})
    result['fkp_ranges'] = occupied_pages
    result['external_reference_count'] = external_reference_count
    result['external_references_truncated'] = max(0, external_reference_count - len(result['external_property_references']))
    result['layout_properties_truncated'] = max(0, result['layout_property_count'] - len(result['layout_properties_not_rendered']))
    result['unresolved_properties_truncated'] = max(0, result['unresolved_property_count'] - len(result['unresolved_properties']))
    result['issues'].append({'code': 'binary_table_structure_incomplete', 'stream': 'WordDocument',
                            'cp_start': cp_start, 'cp_end': cp_start + len(text.encode('utf-16le')) // 2,
                            'message': 'Only explicit depth-one PAPX row/cell boundaries; grouping, merges, styles and unsupported properties remain unknown'})
    return result
