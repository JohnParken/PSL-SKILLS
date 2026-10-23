"""Bounded opaque retention of an embedded BIFF8 chart; no rendering or execution."""
import hashlib
import struct

CHART_POLICIES = ('reject', 'opaque')
# Explicit MS-XLS records observed in the original Schedule.ett chart.
# Variable payloads remain opaque; this is not complete chart grammar validation.
CHART_RECORDS = {
    0x0014, 0x0015, 0x0083, 0x0084, 0x00A1, 0x0033, 0x0012,
    0x00A0, 0x0200, 0x0856, 0x1001, 0x1002, 0x1003, 0x1006,
    0x1007, 0x1009, 0x100A, 0x100B, 0x100D, 0x1014, 0x1017,
    0x101D, 0x101E, 0x101F, 0x1020, 0x1021, 0x1024, 0x1025,
    0x1026, 0x1027, 0x1032, 0x1033, 0x1034, 0x1035, 0x1041,
    0x1044, 0x1045, 0x1046, 0x104E, 0x104F, 0x1051, 0x105F,
    0x1060, 0x1062, 0x1064, 0x1065,
}
DRAWING_CONTAINERS = {0xF000, 0xF002, 0xF003, 0xF004}
DRAWING_ATOMS = {0xF006, 0xF008, 0xF009, 0xF00A, 0xF00B, 0xF010, 0xF011}


def drawing_frame(data, expected_root):
    """Check exact OfficeArt framing, bounded nesting and an explicit type subset."""
    count = 0

    def walk(start, end, depth):
        nonlocal count
        if depth > 16:
            raise ValueError('Drawing nesting exceeds 16')
        while start < end:
            if end - start < 8:
                raise ValueError('Truncated OfficeArt header')
            flags, kind, size = struct.unpack_from('<HHI', data, start)
            stop = start + 8 + size
            count += 1
            if count > 4096 or stop > end:
                raise ValueError('Drawing count/length exceeds frame bounds')
            if kind in DRAWING_CONTAINERS:
                if flags & 15 != 15:
                    raise ValueError('OfficeArt container version must be 15')
                walk(start + 8, stop, depth + 1)
            elif kind not in DRAWING_ATOMS or flags & 15 == 15:
                raise ValueError('Unsupported OfficeArt record ' + hex(kind))
            start = stop

    if len(data) < 8:
        raise ValueError('Truncated drawing root')
    flags, kind, size = struct.unpack_from('<HHI', data)
    if kind != expected_root or flags != 15 or size != len(data) - 8:
        raise ValueError('Drawing root type/length/version mismatch')
    walk(0, len(data), 0)


def chart_end(records, index):
    """Return the exclusive end index and diagnostics for one embedded chart."""
    offset, kind, payload = records[index]
    if kind != 0x0809 or len(payload) != 16 or payload[:4] != b'\x00\x06\x20\x00':
        raise ValueError('Expected embedded BIFF8 chart BOF')
    start = index
    depth = 0
    begins = charts = units = 0
    while index + 1 < len(records):
        index += 1
        _, kind, payload = records[index]
        if index - start > 4096:
            raise ValueError('Embedded chart exceeds 4096 records')
        if kind == 0x000A:
            if payload or depth or not begins or charts != 1 or units != 1:
                raise ValueError('Incomplete/unbalanced embedded chart')
            raw = b''.join(struct.pack('<HH', k, len(p)) + p for _, k, p in records[start:index + 1])
            return index + 1, {'offset': offset, 'end_offset': records[index][0] + 4,
                               'record_count': index - start + 1, 'sha256': hashlib.sha256(raw).hexdigest(),
                               'decoded': False, 'rendered': False, 'grammar_validation': 'bounded_record_framing_only'}
        if kind not in CHART_RECORDS:
            raise ValueError('Unsupported embedded chart record ' + hex(kind))
        if kind == 0x1033:
            if payload or depth >= 32:
                raise ValueError('Invalid/deep chart Begin')
            depth += 1
            begins += 1
        elif kind == 0x1034:
            if payload or depth == 0:
                raise ValueError('Unbalanced chart End')
            depth -= 1
        elif kind == 0x1002:
            if len(payload) != 16:
                raise ValueError('Malformed Chart rectangle')
            charts += 1
        elif kind == 0x1001:
            if len(payload) != 2:
                raise ValueError('Malformed chart Units')
            units += 1
    raise ValueError('Truncated embedded chart without EOF')
