"""Read-only framing of Escher note drawings split by BIFF Obj/TxO records.

Only note objects without macro/link formulas are admitted. Text and author
bytes remain opaque metadata, never worksheet values or executed content.
"""
import hashlib
import struct

from wps_biff8_charts import DRAWING_ATOMS, DRAWING_CONTAINERS


class DrawingResourceLimit(ValueError):
    """The existing raw metadata budget cannot hold the drawing group."""
    status = 'resource_limit'


def _need(condition, message, record):
    if not condition:
        error = ValueError(message)
        error.location = {'record_offset': record[0], 'record_id': hex(record[1])}
        raise error


def _escher_markers(data, record):
    """Validate nested frame ends and locate real client marker leaves."""
    markers = []
    count = 0

    def walk(start, end, depth, parent=None):
        nonlocal count
        _need(depth <= 16, 'Drawing nesting exceeds 16', record)
        local_markers = []
        while start < end:
            _need(end - start >= 8, 'Truncated OfficeArt header', record)
            flags, kind, size = struct.unpack_from('<HHI', data, start)
            stop = start + 8 + size
            count += 1
            _need(count <= 4096 and stop <= end, 'Drawing count/length exceeds frame bounds', record)
            if kind in DRAWING_CONTAINERS:
                _need(flags & 15 == 15, 'OfficeArt container version must be 15', record)
                walk(start + 8, stop, depth + 1, kind)
            else:
                _need(kind in DRAWING_ATOMS or kind == 0xF00D, 'Unsupported OfficeArt note record', record)
                _need(flags & 15 != 15, 'Atom cannot be an OfficeArt container', record)
                if kind in (0xF011, 0xF00D):
                    _need(parent == 0xF004 and flags == 0 and size == 0,
                          'Invalid OfficeArt note client marker', record)
                    markers.append((stop, kind))
                    local_markers.append(kind)
            start = stop
        if parent == 0xF004 and local_markers:
            _need(local_markers == [0xF011, 0xF00D], 'Note shape requires ClientData then ClientTextbox', record)

    walk(0, len(data), 0)
    return markers


def note_drawing_group(records, index, *, byte_budget, record_budget, existing_ids):
    """Consume one exact DgContainer and its linked Note records, without scanning.

    Each MsoDrawing fragment ends at a ClientData or ClientTextbox marker.
    Only the immediately required Obj/TxO/Continue records may intervene.
    All lengths, marker endpoints and object links are checked before return.
    """
    first = records[index]
    raw = first[2]
    _need(first[1] == 0xEC and len(raw) >= 8, 'Truncated note drawing root', first)
    flags, kind, size = struct.unpack_from('<HHI', raw)
    _need(flags == 15 and kind == 0xF002, 'Invalid note drawing root', first)
    total = size + 8
    if total > byte_budget:
        raise DrawingResourceLimit('Note drawing exceeds remaining metadata budget')
    start = index
    used_bytes = 0
    fragments = []
    endpoints = []
    joined_length = 0
    objects = []
    pending_object = None
    details = {}

    def take(expected):
        nonlocal index, used_bytes
        _need(index < len(records), 'Truncated note drawing group', first)
        item = records[index]
        _need(item[1] == expected, 'Unexpected BIFF record within note drawing group', item)
        if index - start >= record_budget or len(item[2]) > byte_budget - used_bytes:
            raise DrawingResourceLimit('Note drawing metadata byte/record budget exceeded')
        used_bytes += len(item[2])
        index += 1
        return item

    while joined_length < total:
        drawing = take(0xEC)
        payload = drawing[2]
        _need(len(payload) >= 8 and len(payload) <= total - joined_length,
              'Drawing fragment crosses declared root boundary', drawing)
        marker_flags, marker, marker_size = struct.unpack_from('<HHI', payload, len(payload) - 8)
        _need(marker_flags == 0 and marker_size == 0 and marker in (0xF011, 0xF00D),
              'Unsupported drawing split: expected a complete client marker', drawing)
        fragments.append(payload)
        joined_length += len(payload)
        endpoints.append((joined_length, marker))
        if marker == 0xF011:
            _need(pending_object is None and len(objects) < 128, 'Unpaired or excessive note objects', drawing)
            obj = take(0x5D)
            payload = obj[2]
            _need(len(payload) == 52 and payload[:6] == struct.pack('<3H', 0x15, 18, 25) and
                  payload[22:26] == struct.pack('<2H', 0x0D, 22) and payload[48:] == bytes(4),
                  'Only FtCmo/FtNts/FtEnd note Obj without macro/link payload supported', obj)
            identity = struct.unpack_from('<H', payload, 6)[0]
            _need(0 < identity <= 32767 and identity not in existing_ids and identity not in objects,
                  'Duplicate/invalid note object ID', obj)
            objects.append(identity)
            pending_object = identity
            details[obj[0]] = {'object_id': identity, 'object_type_raw': 25, 'macro_or_link_formula_present': False}
        else:
            _need(pending_object is not None, 'ClientTextbox has no preceding note object', drawing)
            text = take(0x1B6)
            payload = text[2]
            _need(len(payload) == 18 and payload[16:] == bytes(2),
                  'Only fixed note TxO without linked formula supported', text)
            text_units, run_bytes = struct.unpack_from('<2H', payload, 10)
            _need((text_units == 0 and run_bytes == 0) or
                  (text_units > 0 and run_bytes >= 16 and run_bytes % 8 == 0), 'Invalid TxO text/run lengths', text)
            remaining = text_units
            unicode_bytes = bytearray()
            while remaining:
                part = take(0x3C)
                payload = part[2]
                _need(len(payload) >= 2 and payload[0] in (0, 1), 'Invalid TxO text continuation header', part)
                width = 2 if payload[0] else 1
                _need((len(payload) - 1) % width == 0, 'Split UTF-16 unit in TxO continuation', part)
                units = (len(payload) - 1) // width
                _need(0 < units <= remaining, 'TxO continuation exceeds declared text length', part)
                unicode_bytes.extend(payload[1:] if width == 2 else b''.join(bytes((value, 0)) for value in payload[1:]))
                remaining -= units
            try:
                unicode_bytes.decode('utf-16le')
            except UnicodeDecodeError as error:
                _need(False, 'Invalid TxO Unicode text: ' + error.reason, text)
            remaining = run_bytes
            while remaining:
                part = take(0x3C)
                _need(0 < len(part[2]) <= remaining, 'TxO formatting runs exceed declared boundary', part)
                remaining -= len(part[2])
            details[text[0]] = {'object_id': pending_object, 'text_utf16_units': text_units,
                               'formatting_run_bytes': run_bytes, 'text_exposed_as_cell_value': False,
                               'formatting_runs_validated': False, 'formula_executed': False}
            pending_object = None
    _need(pending_object is None, 'Incomplete final note object', first)
    combined = b''.join(fragments)
    _need(_escher_markers(combined, first) == endpoints,
          'MsoDrawing boundaries do not match real Escher client markers', first)
    linked = set()
    cells = set()
    for _ in objects:
        note = take(0x1C)
        payload = note[2]
        _need(len(payload) >= 11, 'Truncated Note header', note)
        row, column, note_flags, identity, author_units = struct.unpack_from('<5H', payload)
        _need(column < 256 and identity in objects and identity not in linked and (row, column) not in cells,
              'Invalid/duplicate Note cell or object link', note)
        option = payload[10]
        _need(1 <= author_units <= 54 and option in (0, 1), 'Unsupported Note author string', note)
        end = 11 + author_units * (2 if option else 1)
        # This original producer omits the unused terminal byte. Retain that
        # fact instead of discarding the otherwise exactly bounded author.
        _need(len(payload) in (end, end + 1), 'Note author length crosses record boundary', note)
        try:
            payload[11:end].decode('utf-16le' if option else 'latin1')
        except UnicodeDecodeError:
            _need(False, 'Invalid Note author Unicode', note)
        linked.add(identity)
        cells.add((row, column))
        details[note[0]] = {'object_id': identity, 'row_zero_based': row, 'column_zero_based': column,
                           'flags_raw': note_flags, 'author_utf16_units': author_units,
                           'unused_terminal_byte_present': len(payload) == end + 1, 'display_applied': False}
    report = {'drawing_bytes': len(combined), 'drawing_sha256': hashlib.sha256(combined).hexdigest(),
              'drawing_fragments': len(fragments), 'object_count': len(objects),
              'record_count': index - start, 'text_decoded_for_output': False,
              'rendered': False, 'semantics_validated': False}
    details[first[0]] = report
    return index, details, objects, report
