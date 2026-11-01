"""Bounded MS-XLS Globals TableStyle / TableStyleElement group validator.

Accepts only a complete, directly contiguous BIFF8 formatting collection with
verified FrtHeader, name, component count, type, stripe size, and existing DXF
indexes. This does not apply a style or decode cell values or formula results.
"""
import struct

MAX_TABLE_STYLE_ELEMENTS = 28
TABLE_STYLE_RECORD = 0x088F
TABLE_STYLE_ELEMENT_RECORD = 0x0890


def _header(data, expected):
    if len(data) < 12:
        raise ValueError('Truncated TableStyle future record header')
    record_type, flags = struct.unpack_from('<HH', data)
    if record_type != expected or flags & 0x0003 or data[4:12] != bytes(8):
        raise ValueError('Invalid TableStyle FrtHeader scope/type/reserved fields')
    # Our bounded observed subset requires all future flags to be zero.
    if flags:
        raise ValueError('Unrecognized TableStyle FrtHeader flags')


def table_style_group(records, index, dxf_count):
    """Return next index, raw-only report, and participating BIFF records.

    Index points to the TableStyle record. Consume exactly ctse immediately
    following TableStyleElement records, not a recovered or searched sequence.
    """
    offset, kind, payload = records[index]
    if kind != TABLE_STYLE_RECORD:
        raise ValueError('Expected TableStyle record')
    _header(payload, TABLE_STYLE_RECORD)
    if len(payload) < 20:
        raise ValueError('Truncated TableStyle fixed fields')
    flags, count, name_units = struct.unpack_from('<HIH', payload, 12)
    if flags & ~0x0006:
        raise ValueError('TableStyle reserved flags nonzero')
    if count > MAX_TABLE_STYLE_ELEMENTS:
        raise ValueError('TableStyle element count beyond spec')
    if not 1 <= name_units <= 255 or len(payload) != 20 + name_units * 2:
        raise ValueError('TableStyle name length mismatch')
    try:
        name = payload[20:].decode('utf-16le')
    except UnicodeDecodeError as exc:
        raise ValueError('TableStyle name is malformed UTF-16') from exc
    if not name or '\0' in name:
        raise ValueError('TableStyle name contains invalid scalar')
    if index + count >= len(records):
        raise ValueError('Truncated TableStyleElement collection')
    elements = []
    participating = [records[index]]
    for ordinal in range(count):
        item = records[index + 1 + ordinal]
        item_offset, item_kind, data = item
        if item_kind != TABLE_STYLE_ELEMENT_RECORD or len(data) != 24:
            raise ValueError('TableStyleElement sequence/type/length mismatch')
        _header(data, TABLE_STYLE_ELEMENT_RECORD)
        area, band, dxf_id = struct.unpack_from('<III', data, 12)
        if area > 27:
            raise ValueError('Unsupported TableStyleElement region')
        if not 1 <= band <= 9:
            raise ValueError('TableStyleElement stripe size outside spec')
        if dxf_id >= dxf_count:
            raise ValueError('TableStyleElement references nonexistent prior DXF')
        elements.append({'record_offset': item_offset,
                         'area_type_raw': area, 'stripe_size_raw': band,
                         'dxf_index_raw': dxf_id})
        participating.append(item)
    return (index + count + 1,
            {'source_record_offset': offset, 'style_name_raw': name,
             'table_style_flags_raw': flags, 'declared_elements': count,
             'elements': elements, 'effective_formatting': 'unknown'},
            participating)
