"""Opt-in MS-DOC CHPX evidence, not effective formatting or visibility."""
from bisect import bisect_right
import struct

MAX_FKP_PAGES = 4096
MAX_CHPX_RUNS = 65536
MAX_PROPERTY_BYTES = 1024 * 1024
MODIFIERS = {0x0800: 'deleted_revision', 0x0801: 'inserted_revision',
             0x0802: 'field_hidden', 0x0811: 'web_hidden', 0x083C: 'hidden'}


def inspect_character_properties(word, table, pieces, positions, fib_end, table_ranges=(), cp_limit=None):
    # Local import prevents module-load dependency cycles with the public reader.
    from read_wps import require
    pair = 154 + 12 * 8
    start, length = word.number(pair), word.number(pair + 4)
    if cp_limit is None:
        cp_limit = positions[-1]
    result = {'mode': 'annotate', 'effective_visibility': 'unknown',
              'effective_revisions': 'unknown', 'text_filtered': False,
              'style_and_piece_properties_applied': False, 'runs': []}
    if not length:
        result['availability'] = 'missing_bte'
        return result
    # FibRgFcLcb97 explicitly requires fcPlcfBteChpx > 0, unlike many other pairs.
    require(start > 0, word.location(pair), 'Invalid character BTE offset')
    require(length >= 12 and (length - 4) % 8 == 0,
            word.location(pair + 4), 'Invalid character BTE length')
    count = (length - 4) // 8
    require(count <= MAX_FKP_PAGES, word.location(pair + 4),
            'Character FKP page budget exceeded', 'resource_limit')
    bte = table.region(start, length)
    for left, right in table_ranges:
        require(start + length <= left or right <= start, bte.location(),
                'Character BTE overlaps another decoded table')
    offsets = [bte.number(i * 4) for i in range(count + 1)]
    require(all(a < b for a, b in zip(offsets, offsets[1:])),
            bte.location(), 'Character BTE FCs must strictly increase')
    require(offsets[-1] <= len(word.data), bte.location(count * 4),
            'Character BTE FC outside WordDocument')
    physical = sorted((a, b, stride, index) for a, b, stride, index in pieces)
    starts = [item[0] for item in physical]
    seen_pages = set()
    total_runs = 0
    property_bytes = 0
    for page_index in range(count):
        raw_pn = bte.number(4 * (count + 1) + page_index * 4)
        page_offset = (raw_pn & 0x3FFFFF) * 512  # upper ten bits MUST be ignored
        require(page_offset not in seen_pages, bte.location(4 * (count + 1) + page_index * 4),
                'Aliased character FKP page')
        seen_pages.add(page_offset)
        require(page_offset >= fib_end, word.location(page_offset), 'Character FKP overlaps FIB')
        nearest = max(0, bisect_right(starts, page_offset) - 1)
        for piece_index in range(nearest, min(len(physical), bisect_right(starts, page_offset + 511))):
            left, right, _, _ = physical[piece_index]
            require(page_offset + 512 <= left or right <= page_offset,
                    word.location(page_offset), 'Character FKP overlaps text piece')
        page = word.region(page_offset, 512)
        runs = page.number(511, 1)
        require(1 <= runs <= 101, page.location(511), 'Invalid character FKP run count')
        total_runs += runs
        require(total_runs <= MAX_CHPX_RUNS, page.location(511),
                'Character run budget exceeded', 'resource_limit')
        fc = [page.number(i * 4) for i in range(runs + 1)]
        require(all(a < b for a, b in zip(fc, fc[1:])), page.location(),
                'Character FKP FCs must strictly increase')
        require(fc[0] == offsets[page_index] and fc[-1] == offsets[page_index + 1],
                page.location(), 'Character BTE/FKP range mismatch')
        header_end = 4 * (runs + 1) + runs
        occupied = []
        for run in range(runs):
            pointer = page.number(4 * (runs + 1) + run, 1) * 2
            mods = []
            unknown = 0
            group_offset = None
            if pointer:
                require(header_end <= pointer < 511, page.location(pointer),
                        'Character property pointer overlaps FKP arrays')
                size = page.number(pointer, 1)
                stop = pointer + 1 + size
                require(stop <= 511, page.location(pointer), 'Character properties exceed FKP')
                for left, right in occupied:
                    require((pointer, stop) == (left, right) or stop <= left or right <= pointer,
                            page.location(pointer), 'Overlapping character property groups')
                occupied.append((pointer, stop))  # identical groups may be shared
                property_bytes += size
                require(property_bytes <= MAX_PROPERTY_BYTES, page.location(pointer),
                        'Character property byte budget exceeded', 'resource_limit')
                cursor = pointer + 1
                group_offset = page_offset + cursor
                while cursor < stop:
                    require(stop - cursor >= 2, page.location(cursor), 'Truncated character SPRM')
                    at = cursor
                    sprm = page.number(cursor, 2)
                    cursor += 2
                    sgc, spra = (sprm >> 10) & 7, sprm >> 13
                    if sgc != 2:
                        # Paragraph/table variable-length exceptions are not decoded.
                        unknown += 1
                        break
                    width = (1, 1, 2, 4, 2, 2, None, 3)[spra]
                    if width is None:
                        require(cursor < stop, page.location(cursor), 'Missing variable SPRM size')
                        width = page.number(cursor, 1)
                        cursor += 1
                    require(width <= stop - cursor, page.location(cursor), 'Truncated character SPRM operand')
                    if sprm in MODIFIERS:
                        operand = page.number(cursor, 1)
                        require(operand in (0, 1, 0x80, 0x81), page.location(cursor),
                                'Invalid character toggle operand')
                        mods.append({'property': MODIFIERS[sprm], 'sprm': hex(sprm),
                                     'operand_raw': operand, 'source': page.location(at),
                                     'effective_value': 'unknown'})
                    else:
                        unknown += 1
                    cursor += width
            first = max(0, bisect_right(starts, fc[run]) - 1)
            for physical_index in range(first, bisect_right(starts, fc[run + 1] - 1)):
                piece_start, piece_end, stride, piece_index = physical[physical_index]
                if piece_start >= fc[run + 1]:
                    break
                left, right = max(piece_start, fc[run]), min(piece_end, fc[run + 1])
                if left >= right:
                    continue
                cp_start = positions[piece_index] + (left-piece_start)//stride
                if cp_start >= cp_limit:
                    continue
                right = min(right, piece_start + (cp_limit-positions[piece_index])*stride)
                require((left - piece_start) % stride == 0 and (right - piece_start) % stride == 0,
                        word.location(left), 'Character run splits a UTF-16 code unit')
                require(len(result['runs']) < MAX_CHPX_RUNS, word.location(left),
                        'Character CP annotation budget exceeded', 'resource_limit')
                result['runs'].append({'cp_start': positions[piece_index] + (left-piece_start)//stride,
                    'cp_end': positions[piece_index] + (right-piece_start)//stride,
                    'source': {'stream': 'WordDocument', 'fc_start': left, 'fc_end': right,
                               'fkp_offset': page_offset, 'group_offset': group_offset,
                               'piece': piece_index}, 'modifiers': mods,
                    'uninterpreted_modifier_count': unknown, 'effective_visibility': 'unknown'})
    result['runs'].sort(key=lambda item: item['cp_start'])
    result['availability'] = 'chpx_evidence_only'
    result['fkp_ranges'] = [[offset, offset + 512] for offset in sorted(seen_pages)]
    result['bte_source'] = {'stream': table.stream, 'offset': start, 'length': length}
    return result
