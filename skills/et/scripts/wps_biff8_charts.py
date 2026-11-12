# SPDX-License-Identifier: MIT
"""Bounded, tolerant retention of BIFF8 drawings, objects and charts.

Nothing here executes macros, formulas or external links. The strict helpers
(``drawing_frame``) are kept for direct callers; the reader uses the tolerant
``*_report`` variants, which convert framing/whitelist problems into bounded
diagnostics so already parsed cell values are never discarded.
"""
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
DRAWING_ROOTS = {0xF000: 'DrawingGroup', 0xF001: 'DrawingSelection',
                 0xF002: 'Drawing', 0xF004: 'DgContainer'}
MAX_DRAWING_RECORDS = 4096
MAX_DRAWING_DEPTH = 16
MAX_OFFICEART_BYTES = 4 * 1024 * 1024
MAX_FRAGMENTS = 512
MAX_CHART_RECORDS = 4096
# Worksheet-only records are recovery boundaries even with unbalanced Begin/End.
# Number/BoolErr/Blank/Label also occur in chart SERIESDATA after SIIndex; those
# cached points must never leak into worksheet cells (MS-XLS 2.1.7.20.1).
CHART_TERMINATOR_RECORDS = frozenset((0x0006, 0x027E, 0x00BD, 0x00BE,
                                      0x00FD, 0x00E5, 0x0208, 0x007D, 0x0081,
                                      0x0085, 0x0809))
CHART_VALUE_RECORDS = frozenset((0x0201, 0x0203, 0x0204, 0x0205))
# Chart/object records that may reference saved external or executable content.
# They are retained raw and reported; nothing is resolved, loaded or run.
LINK_OR_EXECUTABLE_RECORDS = frozenset((0x0009, 0x0017, 0x0023, 0x0059,
                                        0x005A, 0x01AE, 0x00D6, 0x00D3))
EMBEDDED_CHART_BOF = b'\x00\x06\x20\x00'
# Obj record subrecord identifiers (MS-XLS 2.5.192 Obj). Detection only: the
# reader never interprets, resolves or executes a subrecord payload.
OBJ_SUBRECORD_NAMES = {
    0x0000: 'ftEnd', 0x0009: 'ftPictFmla', 0x000D: 'ftNts',
    0x0015: 'ftCmo', 0x0013: 'ftLbsData', 0x00F7: 'ftMacro',
    0x0012: 'ftCtl',
}
OBJ_EXECUTABLE_SUBRECORDS = frozenset((0x0009, 0x0013, 0x00F7))
OBJ_CHART_TYPE = 5
OBJ_NOTE_TYPE = 25


def _issue(code, message, offset=None, **extra):
    item = {'code': code, 'message': message, **extra}
    if offset is not None:
        item['record_offset'] = offset
    return item


def drawing_frame(data, expected_root, *, allow_unknown_atoms=False):
    """Check exact OfficeArt framing, bounded nesting and an explicit subset.

    Strict compatibility helper for direct callers; the tolerant reader uses
    ``drawing_frame_report`` instead.
    """
    count = 0
    unknown = []

    def walk(start, end, depth):
        nonlocal count
        if depth > MAX_DRAWING_DEPTH:
            raise ValueError('Drawing nesting exceeds 16')
        while start < end:
            if end - start < 8:
                raise ValueError('Truncated OfficeArt header')
            flags, kind, size = struct.unpack_from('<HHI', data, start)
            stop = start + 8 + size
            count += 1
            if count > MAX_DRAWING_RECORDS or stop > end:
                raise ValueError('Drawing count/length exceeds frame bounds')
            if kind in DRAWING_CONTAINERS:
                if flags & 15 != 15:
                    raise ValueError('OfficeArt container version must be 15')
                walk(start + 8, stop, depth + 1)
            elif kind not in DRAWING_ATOMS or flags & 15 == 15:
                if not allow_unknown_atoms or kind in DRAWING_ATOMS:
                    raise ValueError('Unsupported OfficeArt record ' + hex(kind))
                unknown.append({'record_type': hex(kind), 'offset': start,
                                'length': size})
                if flags & 15 == 15:
                    walk(start + 8, stop, depth + 1)
            start = stop

    if len(data) < 8:
        raise ValueError('Truncated drawing root')
    flags, kind, size = struct.unpack_from('<HHI', data)
    if kind != expected_root or flags != 15 or size != len(data) - 8:
        raise ValueError('Drawing root type/length/version mismatch')
    walk(0, len(data), 0)
    return unknown


def drawing_frame_report(data, expected_root):
    """Tolerant OfficeArt frame validation; returns diagnostics, never raises.

    Unknown atoms/containers are retained and reported. Structural violations
    (truncated headers, out-of-frame lengths, excessive depth/count) are
    reported so the caller can keep the raw bytes as opaque evidence.
    """
    report = {'complete': False, 'unknown': [], 'issues': [], 'record_count': 0}
    count = 0

    def problem(message, offset=None):
        if len(report['issues']) < 256:
            report['issues'].append(_issue('drawing_frame_invalid', message,
                                           offset))

    def walk(start, end, depth):
        nonlocal count
        if depth > MAX_DRAWING_DEPTH:
            problem('Drawing nesting exceeds 16', start)
            return
        while start < end:
            if end - start < 8:
                problem('Truncated OfficeArt header', start)
                return
            flags, kind, size = struct.unpack_from('<HHI', data, start)
            stop = start + 8 + size
            count += 1
            if count > MAX_DRAWING_RECORDS:
                problem('Drawing record count exceeds frame bounds', start)
                return
            if stop > end:
                problem('Drawing record length exceeds frame bounds', start)
                return
            if kind in DRAWING_CONTAINERS:
                if flags & 15 != 15:
                    problem('OfficeArt container version must be 15', start)
                walk(start + 8, stop, depth + 1)
            elif kind in DRAWING_ATOMS:
                if flags & 15 == 15:
                    problem('OfficeArt atom cannot be a container', start)
            elif flags & 15 == 15:
                report['unknown'].append({'record_type': hex(kind),
                                          'offset': start, 'length': size})
                walk(start + 8, stop, depth + 1)
            else:
                report['unknown'].append({'record_type': hex(kind),
                                          'offset': start, 'length': size})
            start = stop

    if not isinstance(data, (bytes, bytearray)) or len(data) < 8:
        problem('Truncated drawing root')
        return report
    flags, kind, size = struct.unpack_from('<HHI', data)
    if flags != 15 or size != len(data) - 8:
        problem('Drawing root length/version mismatch')
    elif kind != expected_root:
        problem('Drawing root type mismatch')
    walk(0, len(data), 0)
    report['record_count'] = count
    report['complete'] = not report['issues']
    return report


def officeart_fragments(records, index):
    """Concatenate 0x00EC fragments by the declared OfficeArt root length.

    Returns ``(next_index, joined_bytes, fragment_records, issues)``. The first
    record must be a MsoDrawing(0x00EC) record. Fragments stop at the declared
    root length; an unexpected record before that length is reported and the
    caller continues from it.
    """
    issues = []
    parts = []
    used = []
    if index >= len(records) or records[index][1] != 0x00EC:
        return index, b'', used, [
            _issue('drawing_frame_invalid', 'Expected MsoDrawing record',
                   records[index][0] if index < len(records) else None)]
    first = records[index]
    payload = first[2]
    if len(payload) < 8:
        return index + 1, b'', [first], [
            _issue('drawing_frame_invalid', 'Truncated drawing root',
                   first[0])]
    flags, kind, size = struct.unpack_from('<HHI', payload)
    if flags != 15 or kind not in DRAWING_ROOTS:
        return index + 1, payload, [first], [
            _issue('drawing_frame_invalid',
                   'Drawing root type/version mismatch', first[0],
                   record_id=hex(first[1]))]
    total = 8 + size
    if total > MAX_OFFICEART_BYTES:
        return index + 1, payload, [first], [
            _issue('drawing_frame_invalid',
                   'Declared drawing length exceeds OfficeArt budget',
                   first[0], record_id=hex(first[1]))]
    cursor = index
    joined = 0
    while joined < total:
        if cursor >= len(records) or records[cursor][1] != 0x00EC:
            issues.append(_issue(
                'drawing_fragments_incomplete',
                'Drawing fragments end before the declared root length',
                records[cursor][0] if cursor < len(records) else first[0]))
            break
        item = records[cursor]
        if len(used) >= MAX_FRAGMENTS:
            issues.append(_issue('drawing_fragments_incomplete',
                                 'Drawing fragment count exceeds budget',
                                 item[0]))
            break
        parts.append(item[2])
        used.append(item)
        joined += len(item[2])
        cursor += 1
    joined_bytes = b''.join(parts)
    if joined > total:
        issues.append(_issue('drawing_frame_invalid',
                             'Concatenated drawing fragments exceed the '
                             'declared root length',
                             used[-1][0] if used else None))
    return cursor, joined_bytes, used, issues


def obj_subrecords(payload):
    """Scan Obj subrecord framing without interpreting any subrecord payload.

    Never resolves or executes macro, control, list or picture-formula
    subrecords; only their identifier and length are reported.
    """
    report = {'object_type_raw': None, 'object_id_raw': None,
              'subrecords': [], 'executable_subrecords': [],
              'complete': True, 'issues': []}
    position = 0
    count = 0
    while position < len(payload):
        if len(payload) - position < 4:
            report['complete'] = False
            report['issues'].append(_issue(
                'obj_subrecord_invalid', 'Truncated Obj subrecord header',
                position))
            break
        ft, size = struct.unpack_from('<HH', payload, position)
        count += 1
        if count > 256:
            report['complete'] = False
            report['issues'].append(_issue(
                'obj_subrecord_invalid', 'Obj subrecord count exceeds budget',
                position))
            break
        stop = position + 4 + size
        if stop > len(payload):
            report['complete'] = False
            report['issues'].append(_issue(
                'obj_subrecord_invalid',
                'Obj subrecord payload crosses record boundary', position))
            break
        report['subrecords'].append({'ft_raw': ft, 'size': size,
                                     'offset': position})
        if ft == 0x0015:
            if size >= 4:
                report['object_type_raw'] = struct.unpack_from(
                    '<H', payload, position + 4)[0]
                report['object_id_raw'] = struct.unpack_from(
                    '<H', payload, position + 6)[0]
        elif ft in OBJ_EXECUTABLE_SUBRECORDS:
            report['executable_subrecords'].append(
                OBJ_SUBRECORD_NAMES.get(ft, hex(ft)))
        position = stop
    return report


def officeart_root(data):
    """Return the declared root kind and whether the frame claims completeness."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 8:
        return None
    return struct.unpack_from('<HHI', data)[1]


def chart_records_end(records, index):
    """Tolerant embedded-chart scan from its BOF to its matching EOF.

    Returns ``(next_index, report_or_None, issues)``. BOF/EOF and Begin/End
    balance are still checked and reported, but a failure no longer aborts the
    worksheet: the records are retained raw and parsing resumes at the
    returned index (after EOF, or at the first record that could not be
    attributed to the chart).
    """
    start = index
    issues = []
    if index >= len(records):
        return index, None, [_issue('embedded_chart_invalid',
                                    'Missing embedded chart record')]
    offset, kind, payload = records[index]
    if kind != 0x0809 or len(payload) != 16 or payload[:4] != EMBEDDED_CHART_BOF:
        return index, None, [_issue('embedded_chart_invalid',
                                    'Expected embedded BIFF8 chart BOF',
                                    offset)]
    depth = 0
    begins = charts = units = 0
    payload_issues = []
    unknown_records = []
    series_data = False
    index += 1
    while index < len(records):
        if index - start > MAX_CHART_RECORDS:
            issues.append(_issue('embedded_chart_invalid',
                                 'Embedded chart exceeds record budget',
                                 records[index][0]))
            return index, None, issues
        record = records[index]
        _, kind, payload = record
        if kind in CHART_TERMINATOR_RECORDS or (kind in CHART_VALUE_RECORDS and not series_data):
            issues.append(_issue('embedded_chart_invalid',
                                 'Embedded chart was not terminated before a '
                                 'worksheet record; chart bytes end retained '
                                 'raw and do not consume later cells.',
                                 record[0]))
            return index, None, issues
        index += 1
        if kind == 0x000A:
            if payload:
                issues.append(_issue('embedded_chart_invalid',
                                     'Embedded chart EOF carries a payload',
                                     record[0]))
            if depth:
                issues.append(_issue('embedded_chart_invalid',
                                     'Unbalanced Begin/End in embedded chart',
                                     record[0]))
            if not begins or not charts or not units:
                issues.append(_issue(
                    'embedded_chart_invalid',
                    'Embedded chart lacks a required Begin/Chart/Units record',
                    record[0]))
            raw = b''.join(
                struct.pack('<HH', item[1], len(item[2])) + item[2]
                for item in records[start:index])
            report = {'offset': offset,
                      'end_offset': record[0] + 4,
                      'record_count': index - start,
                      'sha256': hashlib.sha256(raw).hexdigest(),
                      'decoded': False, 'rendered': False,
                      'grammar_validation': 'bounded_record_framing_only'}
            if payload_issues:
                report['payload_issues'] = payload_issues
            if unknown_records:
                report['unknown_records'] = unknown_records[:256]
            if issues:
                report['validation_issues'] = issues[:256]
            return index, report, issues
        if kind == 0x1065:
            series_data = True
        if kind in CHART_VALUE_RECORDS and series_data:
            continue
        if kind not in CHART_RECORDS:
            if kind in LINK_OR_EXECUTABLE_RECORDS:
                issues.append(_issue(
                    'embedded_chart_link_not_followed',
                    'Chart record references saved external/executable '
                    'content; retained raw and never followed or executed',
                    record[0], record_id=hex(kind)))
            else:
                issues.append(_issue(
                    'embedded_chart_unknown_record',
                    'Unrecognized chart record retained raw', record[0],
                    record_id=hex(kind)))
            if len(unknown_records) < 256:
                unknown_records.append({'record_offset': record[0],
                                        'record_id': hex(kind)})
            continue
        if kind == 0x1033:
            if payload or depth >= 32:
                issues.append(_issue('embedded_chart_invalid',
                                     'Invalid/deep chart Begin', record[0]))
                if depth < 32:
                    depth += 1
            else:
                depth += 1
            begins += 1
        elif kind == 0x1034:
            if payload or depth == 0:
                issues.append(_issue('embedded_chart_invalid',
                                     'Unbalanced chart End', record[0]))
            else:
                depth -= 1
        elif kind == 0x1002:
            if len(payload) != 16:
                payload_issues.append({'record_offset': record[0],
                                       'message': 'Malformed Chart rectangle'})
            charts += 1
        elif kind == 0x1001:
            if len(payload) != 2:
                payload_issues.append({'record_offset': record[0],
                                       'message': 'Malformed chart Units'})
            units += 1
    issues.append(_issue('embedded_chart_invalid',
                         'Truncated embedded chart without EOF',
                         records[start][0]))
    return index, None, issues
