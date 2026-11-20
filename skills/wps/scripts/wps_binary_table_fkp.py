"""BTE/FKP page validation and PAPX SPRM decoding for binary MS-DOC tables."""
from bisect import bisect_right
from itertools import accumulate

from wps_binary_table_groups import FRAME_PROPERTY_WIDTHS, PARAGRAPH_POSITION_CODE


MAX_PAGES = 4096


def init_bte(word, table, table_ranges, enhanced, piece_properties, overlap_location, require):
    """Validate and return BTE boundaries and FC limits for paragraph FKP lookup."""
    pair = 154 + 13 * 8
    start, length = word.number(pair), word.number(pair + 4)
    if not length or (piece_properties and not enhanced):
        return length, None, []
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
    return length, bte, fc_limits


class FkpPropertyReader:
    """Lazy, bounded paragraph FKP page loader and PAPX SPRM decoder."""

    def __init__(self, *, word, bte, fc_limits, length, pieces, positions, fib_end,
                 options, limits, helpers, result):
        self.word = word
        self.bte = bte
        self.fc_limits = fc_limits
        self.length = length
        self.pieces = pieces
        self.positions = positions
        self.fib_end = fib_end
        self.enhanced = options.piece_prms is not None
        self.piece_properties = options.piece_properties
        self.word_ranges = options.word_ranges
        self.data_region = options.data_region
        self.data_ownership = options.data_ownership
        self.include_layout = options.include_layout
        self.overlap_location = {'reason_code': 'overlapping_decoded_metadata'} if self.enhanced else {}
        self.physical = sorted(pieces)
        self.physical_starts = [piece[0] for piece in self.physical]
        self.prm_counts = (
            tuple(accumulate(map(bool, options.piece_prms), initial=0))
            if self.enhanced and length else ()
        )
        self.limits = limits
        self.helpers = helpers
        self.result = result
        self.pages = {}
        self.occupied_pages = []
        self.external_ranges = []
        self.property_bytes = 0
        self.external_reference_count = 0

    def properties(self, cp, segment_start):
        """Return validated direct PAPX properties covering cp, or None."""
        if not self.length or (self.piece_properties and not self.enhanced):
            return None
        index = bisect_right(self.positions, cp) - 1
        if self.enhanced:
            first_piece = bisect_right(self.positions, segment_start) - 1
            if self.prm_counts[index + 1] != self.prm_counts[first_piece]:
                return None
        offset, _, stride, _ = self.pieces[index]
        fc = offset + (cp - self.positions[index]) * stride
        page_index = bisect_right(self.fc_limits, fc) - 1
        if not 0 <= page_index < len(self.fc_limits) - 1:
            return None
        page_offset = (self.bte.number(4 * len(self.fc_limits) + page_index * 4) & 0x3FFFFF) * 512
        if page_offset not in self.pages:
            self.pages[page_offset] = self._load_page(page_offset, page_index)
            self.occupied_pages.append((page_offset, page_offset + 512))
        offsets, groups = self.pages[page_offset]
        run = bisect_right(offsets, fc) - 1
        if not 0 <= run < len(groups) or offsets[run + 1] != fc + stride:
            return None
        return groups[run]

    def _load_page(self, page_offset, page_index):
        require = self.helpers['require']
        require(len(self.pages) < MAX_PAGES, self.word.location(page_offset),
                'Paragraph FKP page budget exceeded', 'resource_limit')
        require(page_offset >= self.fib_end, self.word.location(page_offset), 'Paragraph FKP overlaps FIB')
        for left, right in self.word_ranges:
            require(page_offset + 512 <= left or right <= page_offset,
                    self.word.location(page_offset, **self.overlap_location),
                    'Paragraph FKP overlaps decoded metadata')
        first = max(0, bisect_right(self.physical_starts, page_offset) - 1)
        last = bisect_right(self.physical_starts, page_offset + 511)
        for left, right, _, _ in self.physical[first:last]:
            require(page_offset + 512 <= left or right <= page_offset,
                    self.word.location(page_offset), 'Paragraph FKP overlaps text')
        page = self.word.region(page_offset, 512)
        count = page.number(511, 1)
        require(1 <= count <= 29, page.location(511), 'Invalid paragraph FKP count')
        offsets = [page.number(i * 4) for i in range(count + 1)]
        require(all(a < b for a, b in zip(offsets, offsets[1:])),
                page.location(), 'Paragraph FKP offsets not increasing')
        require(offsets[0] == self.fc_limits[page_index] and offsets[-1] == self.fc_limits[page_index + 1],
                page.location(), 'Paragraph BTE/FKP range mismatch')
        groups = []
        spans = []
        header_end = 4 * (count + 1) + 13 * count
        for run in range(count):
            groups.append(self._decode_run(page, count, run, header_end, spans))
        return offsets, groups

    def _decode_run(self, page, count, run, header_end, spans):
        require = self.helpers['require']
        pointer = page.number(4 * (count + 1) + 13 * run, 1) * 2
        if not pointer:
            return None
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
        self.property_bytes += size
        require(self.property_bytes <= self.limits['max_property_bytes'], page.location(pointer),
                'Paragraph property budget exceeded', 'resource_limit')
        style = page.number(cursor, 2)
        cursor += 2
        props = {'source': page.location(pointer), 'istd': style}
        valid = self._decode_sprms(page, style, cursor, stop, props)
        if self.enhanced:
            props['structure_unknown'] = not valid
        return props if valid or self.enhanced else None

    def _follow_external(self, properties_region, sprm, style, cursor, stop, array_start, props, visited_external):
        require = self.helpers['require']
        require(cursor + 4 <= stop, properties_region.location(cursor),
                'Truncated external paragraph property reference')
        external_offset = properties_region.number(cursor, 4)
        if sprm == 0x6646 and cursor - 2 != array_start:
            # The specification explicitly ignores non-first HugePapx.
            return None, cursor + 4, stop, array_start, False
        if properties_region is not self.data_region and sprm == 0x6646:
            require(style == 0 and cursor + 4 == stop, properties_region.location(cursor),
                    'Inline HugePapx requires zero style and a sole PRL')
        require(self.data_region is not None, properties_region.location(cursor),
                'External paragraph Data stream missing')
        require(external_offset not in visited_external, self.data_region.location(external_offset),
                'External paragraph property reference cycle')
        require(len(visited_external) < 32 and self.external_reference_count < 4096,
                self.data_region.location(external_offset),
                'External paragraph reference budget exceeded', 'resource_limit')
        visited_external.add(external_offset)
        self.external_reference_count += 1
        size = self.data_region.number(external_offset, 2)
        require(10 <= size <= 0x3FA2, self.data_region.location(external_offset),
                'Invalid external paragraph property length')
        external_end = external_offset + 2 + size
        self.data_region.view(external_offset + 2, size)
        require(all((external_offset, external_end) == span
                    or external_end <= span[0] or span[1] <= external_offset
                    for span in self.external_ranges),
                self.data_region.location(external_offset),
                'Overlapping external paragraph property arrays')
        if (external_offset, external_end) not in self.external_ranges:
            self.external_ranges.append((external_offset, external_end))
        if self.data_ownership is not None:
            self.data_ownership.add((external_offset, external_end))
        self.property_bytes += size
        require(self.property_bytes <= self.limits['max_property_bytes'],
                self.data_region.location(external_offset),
                'Paragraph property budget exceeded', 'resource_limit')
        if len(self.result['external_property_references']) < 100:
            self.result['external_property_references'].append({
                'sprm': hex(sprm), 'source': properties_region.location(cursor - 2),
                'target': self.data_region.location(external_offset), 'property_bytes': size,
            })
        props['external_properties_source'] = self.data_region.location(external_offset + 2)
        return self.data_region, external_offset + 2, external_end, external_offset + 2, True

    def _decode_sprms(self, page, style, cursor, stop, props):
        require = self.helpers['require']
        constants = self.helpers['constants']
        layout_properties = constants['LAYOUT_PROPERTIES']
        neutral_widths = constants['TABLE_NEUTRAL_PARAGRAPH_WIDTHS']
        table_properties = constants['TABLE_PROPERTIES']
        properties_region = page
        array_start = cursor
        visited_external = set()
        valid = True
        while cursor < stop:
            require(stop - cursor >= 2, properties_region.location(cursor), 'Truncated paragraph SPRM')
            sprm = properties_region.number(cursor, 2)
            cursor += 2
            if sprm in (0x6646, 0x646B):
                new_region, new_cursor, new_stop, new_start, switched = self._follow_external(
                    properties_region, sprm, style, cursor, stop, array_start, props, visited_external)
                if switched:
                    properties_region, cursor, stop, array_start = new_region, new_cursor, new_stop, new_start
                else:
                    cursor = new_cursor
                continue
            if sprm in layout_properties:
                cursor = self._consume_layout_sprm(properties_region, sprm, cursor, stop, props, layout_properties)
                continue
            if self.enhanced and sprm in FRAME_PROPERTY_WIDTHS:
                width = FRAME_PROPERTY_WIDTHS[sprm]
                require(cursor + width <= stop, properties_region.location(cursor),
                        'Truncated paragraph frame property')
                props.setdefault('frame', {})[sprm] = properties_region.number(cursor, width)
                cursor += width
                continue
            if self.enhanced and sprm in neutral_widths:
                width = neutral_widths[sprm]
                require(cursor + width <= stop, properties_region.location(cursor),
                        'Truncated fixed-width paragraph property')
                cursor += width
                continue
            if self.enhanced and sprm == 0x4600:
                require(cursor + 2 <= stop, properties_region.location(cursor),
                        'Truncated paragraph style operand')
                if 'initial_definition' in props:
                    props['unsupported_cell_layout'] = True
                props['istd'] = properties_region.number(cursor, 2)
                props.pop('frame', None)
                cursor += 2
                continue
            if self.enhanced and sprm == 0x664A:
                require(cursor + 4 <= stop, properties_region.location(cursor),
                        'Truncated relative table depth')
                delta = int.from_bytes(properties_region.view(cursor, 4), 'little', signed=True)
                depth = props.get('depth', 0) + delta
                require(0 <= depth <= 0x7FFFFFFF, properties_region.location(cursor),
                        'Invalid resulting table depth')
                props['depth'] = depth
                cursor += 4
                continue
            if sprm == 0xD608:  # TDefTable's exceptional two-byte length
                cursor = self._consume_tdef_table(properties_region, cursor, stop, props)
                continue
            if sprm not in table_properties:
                cursor, keep_scanning = self._consume_other_sprm(properties_region, sprm, cursor, stop, props)
                if not keep_scanning:
                    valid = False
                    break
                continue
            width = 4 if sprm == 0x6649 else 1
            require(cursor + width <= stop, properties_region.location(cursor), 'Truncated table SPRM')
            value = properties_region.number(cursor, width)
            if width == 1:
                require(value in (0, 1), properties_region.location(cursor), 'Invalid table boolean')
            elif self.enhanced:
                require(value <= 0x7FFFFFFF, properties_region.location(cursor), 'Invalid absolute table depth')
            props[table_properties[sprm]] = value
            cursor += width
        return valid

    def _consume_layout_sprm(self, properties_region, sprm, cursor, stop, props, layout_properties):
        require = self.helpers['require']
        name, width = layout_properties[sprm]
        require(cursor + width <= stop, properties_region.location(cursor),
                'Truncated known paragraph layout property')
        if self.enhanced and sprm in FRAME_PROPERTY_WIDTHS:
            value = properties_region.number(cursor, width)
            props.setdefault('frame', {})[sprm] = value & 0xF0 if sprm == PARAGRAPH_POSITION_CODE else value
        self.result['layout_property_count'] += 1
        if self.enhanced and self.include_layout:
            props['layout_modifier_count'] = props.get('layout_modifier_count', 0) + 1
        keep_raw = self.include_layout and len(self.result['layout_properties_not_rendered']) < 100
        keep_typed = (
            self.include_layout and self.enhanced
            and self.result['layout_property_count'] <= self.limits['max_layout_modifiers']
        )
        if keep_raw or keep_typed:
            raw = properties_region.view(cursor, width)
            modifier = {'sprm': hex(sprm), 'name': name, 'operand_hex': bytes(raw).hex(),
                        'source': properties_region.location(cursor)}
            if keep_raw:
                self.result['layout_properties_not_rendered'].append({**modifier, 'effect': 'layout_not_rendered'})
            if keep_typed:
                typed = self.helpers['layout_operand'](sprm, raw)
                props.setdefault('layout_modifiers', []).append({**modifier, **typed})
        return cursor + width

    def _consume_tdef_table(self, properties_region, cursor, stop, props):
        require = self.helpers['require']
        require(cursor + 2 <= stop, properties_region.location(cursor), 'Truncated table definition length')
        definition_end = cursor + properties_region.number(cursor, 2) + 1
        cursor += 2
        require(cursor < definition_end <= stop, properties_region.location(cursor),
                'Invalid table definition extent')
        columns = properties_region.number(cursor, 1)
        require(columns <= 63 and cursor + 1 + 2 * (columns + 1) <= definition_end,
                properties_region.location(cursor), 'Invalid table definition columns')
        edges = [int.from_bytes(properties_region.view(cursor + 1 + 2 * i, 2), 'little', signed=True)
                 for i in range(columns + 1)]
        require(all(a <= b for a, b in zip(edges, edges[1:])),
                properties_region.location(cursor), 'Table definition edges not ordered')
        tc_start = cursor + 1 + 2 * (columns + 1)
        tc_bytes = properties_region.view(tc_start, definition_end - tc_start)
        if self.enhanced and 'initial_definition' in props:
            props['unsupported_cell_layout'] = True
        props['initial_definition'] = {
            'column_count': columns,
            'edges_twips': edges,
            'remaining_property_bytes': len(tc_bytes),
            'initial_cell_properties': self.helpers['initial_cell_properties'](tc_bytes, columns),
        }
        return definition_end

    def _consume_other_sprm(self, properties_region, sprm, cursor, stop, props):
        require = self.helpers['require']
        if (sprm >> 10) & 7 == 5:
            width = (1, 1, 2, 4, 2, 2, None, 3)[sprm >> 13]
            if width is None:
                require(cursor < stop, properties_region.location(cursor), 'Missing table property length')
                width = properties_region.number(cursor, 1)
                cursor += 1
            require(cursor + width <= stop, properties_region.location(cursor), 'Truncated table property')
            if self.enhanced:
                self.helpers['observe_table_operand'](props, sprm, properties_region.view(cursor, width))
            if sprm in (0xD634, 0xD635):
                operands = props.setdefault('local_width_operands', [])
                if len(operands) < 128:
                    operands.append(self.helpers['table_width_operand'](
                        sprm, properties_region.view(cursor, width), properties_region.location(cursor)))
                else:
                    props['local_width_operands_omitted'] = props.get('local_width_operands_omitted', 0) + 1
            props['uninterpreted_table_modifiers'] = props.get('uninterpreted_table_modifiers', 0) + 1
            kinds = props.setdefault('uninterpreted_table_modifier_ids', {})
            key = hex(sprm)
            if key in kinds or len(kinds) < 64:
                kinds[key] = kinds.get(key, 0) + 1
            else:
                props['uninterpreted_table_modifier_ids_omitted'] = props.get(
                    'uninterpreted_table_modifier_ids_omitted', 0) + 1
            return cursor + width, True
        operand_end = (self.helpers['skipped_operand_end'](sprm, properties_region, cursor, stop)
                       if self.enhanced else None)
        self.result['unresolved_property_count'] += 1
        if len(self.result['unresolved_properties']) < 100:
            self.result['unresolved_properties'].append({
                'sprm': hex(sprm), 'source': properties_region.location(cursor - 2),
                'reason': ('unknown_property_skipped_semantics_unverified'
                           if self.enhanced else 'unknown_property_not_skipped'),
            })
        if self.enhanced:
            props['unsupported_cell_layout'] = True
            props.pop('frame', None)
            return operand_end, True
        return cursor, False
