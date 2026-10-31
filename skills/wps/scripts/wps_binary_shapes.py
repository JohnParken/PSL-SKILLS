"""Bounded MS-DOC main-story PlcfSpa anchor validation; never decode shapes.

The caller must have independently validated the CLX piece table. This module
only links a stored U+0008 anchor to a precisely bounded PlcfSpa entry.
It does not establish effective sprmCFSpec or drawn-object display.
"""
import struct

MAX_SHAPES = 1000
PLCF_SPA_PAIR = 40
SPA_BYTES = 26


def main_shape_anchors(word, table, main_count, read_unit, table_ranges=()):
    """Return {main-story CP: source metadata}, not inferred object contents."""
    from read_wps import require

    pair = 154 + 8 * PLCF_SPA_PAIR
    start = word.number(pair)
    size = word.number(pair + 4)
    if not size:
        return {}
    require(start > 0 and size >= 34 and (size - 4) % (SPA_BYTES + 4) == 0,
            word.location(pair), 'Invalid main-story PlcfSpa layout')
    count = (size - 4) // (SPA_BYTES + 4)
    require(count <= MAX_SHAPES, word.location(pair + 4),
            'Main-story PlcfSpa shape budget exceeded', 'resource_limit')
    region = table.region(start, size)
    require(all(start + size <= left or right <= start for left, right in table_ranges),
            region.location(), 'PlcfSpa aliases another decoded Table Stream region')
    cps = struct.unpack('<' + 'I' * (count + 1), region.view(0, (count + 1) * 4))
    require(all(a < b for a, b in zip(cps, cps[1:])),
            region.location(), 'PlcfSpa CPs must be strictly increasing')
    # MS-DOC: the final CP is undefined and MUST be ignored, except it
    # must be greater than the previous CP. Only actual anchors are bounded.
    require(all(cp < main_count for cp in cps[:-1]), region.location(),
            'PlcfSpa shape anchor outside declared main document story')
    results = {}
    for index, cp in enumerate(cps[:-1]):
        require(read_unit(cp) == 8, region.location(4 * index, shape=index, cp=cp),
                'PlcfSpa anchor does not point to U+0008')
        results[cp] = {
            'source_stream': table.stream,
            'plcf_offset': start,
            'spa_record_offset': start + (count + 1) * 4 + index * SPA_BYTES,
            'shape_index': index,
        }
    return results
