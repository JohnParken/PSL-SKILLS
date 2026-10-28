"""Bounded direct-PAPX row/cell evidence; no grid, merge or table grouping claims."""
from bisect import bisect_right

MAX_PAGES = 4096
MAX_MARKS = 100000
MAX_CELLS = 100000
MAX_PROPERTY_BYTES = 1024 * 1024
TABLE_PROPERTIES = {0x2416: 'in_table', 0x2417: 'row_end', 0x6649: 'depth'}


def inspect_tables(text, cp_start, word, table, pieces, positions, fib_end,
                   table_ranges=(), word_ranges=(), piece_properties=False):
    """Return observed depth-one rows only when every paragraph has direct evidence."""
    from read_wps import ParseError, require
    result = {'schema_version': 'ms_doc_table_rows_v1', 'status': 'unresolved',
              'rows': [], 'unresolved_markers': [], 'table_grouping': 'unknown',
              'grid_and_merges': 'unknown', 'source': {'stream': 'WordDocument'},
              'issues': []}
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
        nonlocal property_bytes
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
                while cursor < stop:
                    require(stop - cursor >= 2, page.location(cursor), 'Truncated paragraph SPRM')
                    sprm = page.number(cursor, 2)
                    cursor += 2
                    # Unrecognized operands may contain exceptional variable lengths.
                    # Stop rather than scan their bytes for apparent table flags.
                    if sprm == 0xD608:  # TDefTable's exceptional two-byte length
                        require(cursor + 2 <= stop, page.location(cursor), 'Truncated table definition length')
                        definition_end = cursor + page.number(cursor, 2) + 1
                        cursor += 2
                        require(cursor < definition_end <= stop, page.location(cursor), 'Invalid table definition extent')
                        columns = page.number(cursor, 1)
                        require(columns <= 63 and cursor + 1 + 2 * (columns + 1) <= definition_end,
                                page.location(cursor), 'Invalid table definition columns')
                        edges = [int.from_bytes(page.view(cursor + 1 + 2 * i, 2), 'little', signed=True)
                                 for i in range(columns + 1)]
                        require(all(a <= b for a, b in zip(edges, edges[1:])),
                                page.location(cursor), 'Table definition edges not ordered')
                        # Retain initial boundaries only. TC80 and later table changes
                        # can alter display/merges; never promote these to a grid.
                        props['initial_definition'] = {'column_count': columns, 'edges_twips': edges,
                            'remaining_property_bytes': definition_end - cursor - 1 - 2 * (columns + 1)}
                        cursor = definition_end
                        continue
                    if sprm not in TABLE_PROPERTIES:
                        if (sprm >> 10) & 7 == 5:
                            width = (1, 1, 2, 4, 2, 2, None, 3)[sprm >> 13]
                            if width is None:
                                require(cursor < stop, page.location(cursor), 'Missing table property length')
                                width = page.number(cursor, 1)
                                cursor += 1
                            require(cursor + width <= stop, page.location(cursor), 'Truncated table property')
                            cursor += width
                            props['uninterpreted_table_modifiers'] = props.get('uninterpreted_table_modifiers', 0) + 1
                            continue
                        valid = False
                        break
                    width = 4 if sprm == 0x6649 else 1
                    require(cursor + width <= stop, page.location(cursor), 'Truncated table SPRM')
                    value = page.number(cursor, width)
                    if width == 1:
                        require(value in (0, 1), page.location(cursor), 'Invalid table boolean')
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
        if props is None or props.get('in_table') != 1 or props.get('depth') != 1 or 'row_end' not in props:
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
                            'initial_definition': props.get('initial_definition'),
                            'uninterpreted_table_modifiers': props.get('uninterpreted_table_modifiers', 0)})
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
                                       'cp_end': cp + 1}, 'properties_source': props['source']})
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
                                 'metadata_status': exc.status})
    result['fkp_ranges'] = occupied_pages
    result['issues'].append({'code': 'binary_table_structure_incomplete', 'stream': 'WordDocument',
                            'cp_start': cp_start, 'cp_end': cp_start + len(text.encode('utf-16le')) // 2,
                            'message': 'Only explicit depth-one PAPX row/cell boundaries; grouping, merges, styles and unsupported properties remain unknown'})
    return result
