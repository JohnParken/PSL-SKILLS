"""Read bounded OfficeArt shape records linked to requested MS-DOC IDs."""
from collections import Counter
import struct


MAX_OFFICEART_RECORDS = 20000
MAX_OFFICEART_DEPTH = 32
MAX_SHAPE_TEXT_BYTES = 8192
MAX_TOTAL_TEXT_BYTES = 262144

DGG_CONTAINER = 0xF000
BSTORE_CONTAINER = 0xF001
DG_CONTAINER = 0xF002
SPGR_CONTAINER = 0xF003
SP_CONTAINER = 0xF004
FSP_RECORD = 0xF00A
FOPT_RECORD = 0xF00B
TERTIARY_FOPT_RECORD = 0xF122
GROUP_SHAPE = 1 << 0
PATRIARCH_SHAPE = 1 << 2
DELETED_SHAPE = 1 << 3
OLE_SHAPE = 1 << 4
HIDDEN_FROM_LINKING = PATRIARCH_SHAPE | DELETED_SHAPE | OLE_SHAPE
TEXTBOX_ID_PROPERTY = 0x0080
PICTURE_INDEX_PROPERTY = 0x0104
METRO_BLOB_PROPERTY = 0x03A9

SHAPE_TYPES = {
    0: 'custom',
    1: 'rectangle',
    2: 'rounded_rectangle',
    3: 'ellipse',
    4: 'diamond',
    13: 'arrow',
    20: 'line',
    41: 'callout_1',
    42: 'callout_2',
    43: 'callout_3',
    61: 'rectangular_callout',
    62: 'rounded_rectangular_callout',
    63: 'elliptical_callout',
    75: 'picture_frame',
    136: 'plain_text',
    202: 'text_box',
}

FOPT_TEXT_PROPERTIES = {
    0x00C0: 'wordart_text',
    0x0380: 'name',
    0x0381: 'description',
}


def _read_record(officeart, offset, parent_end, state, require):
    """Read one record header and verify its length within its parent."""
    require(parent_end - offset >= 8, officeart.location(offset),
            'Truncated OfficeArt record header')
    ver_instance, record_type, payload_size = struct.unpack_from(
        '<HHI', officeart.data, offset)
    payload_start = offset + 8
    require(payload_size <= parent_end - payload_start,
            officeart.location(offset), 'OfficeArt record exceeds its parent')
    require(state['records'] < MAX_OFFICEART_RECORDS,
            officeart.location(offset), 'OfficeArt record budget exceeded',
            'resource_limit')
    state['records'] += 1
    return (record_type, ver_instance & 0xF, ver_instance >> 4,
            payload_start, payload_start + payload_size)


def _check_container(record, record_type, officeart, offset, require):
    """Require a standard zero-instance OfficeArt container header."""
    actual_type, version, instance = record[:3]
    require(actual_type == record_type and version == 0xF and instance == 0,
            officeart.location(offset), 'Invalid OfficeArt container header')


def _shape_candidate(officeart, record, offset, state, require):
    """Read direct FSP and option record ranges from one SpContainer."""
    _check_container(record, SP_CONTAINER, officeart, offset, require)
    _, _, _, cursor, parent_end = record
    fsp = None
    fopt = None
    fopt_count = 0
    tertiary = []
    while cursor < parent_end:
        child_offset = cursor
        child = _read_record(officeart, cursor, parent_end, state, require)
        record_type, version, instance, payload_start, cursor = child
        payload_end = cursor
        if record_type == FSP_RECORD:
            require(fsp is None, officeart.location(child_offset), 'SpContainer has repeated FSP records')
            require(version == 2 and payload_end - payload_start == 8, officeart.location(child_offset), 'Invalid FSP record')
            shape_id, flags = struct.unpack_from('<II', officeart.data, payload_start)
            fsp = (shape_id, flags, instance, child_offset)
        elif record_type == FOPT_RECORD:
            fopt_count += 1
            if fopt_count == 1:
                fopt = (version, instance, payload_start, payload_end)
        elif record_type == TERTIARY_FOPT_RECORD:
            tertiary.append((version, instance, payload_start, payload_end))
    if fsp is None:
        return None
    shape_id, flags, shape_type_code, source_offset = fsp
    return {
        'shape_id': shape_id,
        'flags': flags,
        'shape_type_code': shape_type_code,
        'source_offset': source_offset,
        'fopt': fopt,
        'fopt_count': fopt_count,
        'tertiary': tertiary,
    }


def _walk_container(officeart, container, container_type, container_offset,
                    depth, state, candidates, require, group_path=()):
    """Visit only SpgrContainer and SpContainer descendants."""
    require(depth <= MAX_OFFICEART_DEPTH,
            officeart.location(container_offset),
            'OfficeArt container depth exceeded', 'resource_limit')
    _check_container(container, container_type, officeart,
                     container_offset, require)
    _, _, _, cursor, parent_end = container
    group_head = container_type == SPGR_CONTAINER
    while cursor < parent_end:
        child_offset = cursor
        child = _read_record(officeart, cursor, parent_end, state, require)
        record_type = child[0]
        candidate = None
        if record_type == SPGR_CONTAINER:
            _walk_container(officeart, child, SPGR_CONTAINER, child_offset,
                            depth + 1, state, candidates, require,
                            None if group_head else group_path)
        elif record_type == SP_CONTAINER:
            candidate = _shape_candidate(
                officeart, child, child_offset, state, require)
            if candidate is not None:
                candidate['group_path'] = group_path
                candidates.append(candidate)
        if group_head:
            group_path = (group_path + (candidate['shape_id'],)
                          if group_path is not None and candidate and candidate['flags'] & GROUP_SHAPE else None)
            group_head = False
        cursor = child[4]


def _read_text_field(officeart, start, size):
    """Decode one bounded NUL-terminated UTF-16LE field or return None."""
    if size < 2 or size > MAX_SHAPE_TEXT_BYTES or size % 2:
        return None
    end = start + size
    if bytes(officeart.data[end - 2:end]) != b'\x00\x00':
        return None
    for offset in range(start, end - 2, 2):
        if bytes(officeart.data[offset:offset + 2]) == b'\x00\x00':
            return None
    try:
        return bytes(officeart.data[start:end - 2]).decode('utf-16le', 'strict')
    except UnicodeDecodeError:
        return None


def _read_fopt(officeart, fopt):
    """Return supported text and sources, or None for unsupported FOPT."""
    version, property_count, payload_start, payload_end = fopt
    if version != 3:
        return None
    table_size = property_count * 6
    if table_size > payload_end - payload_start:
        return None
    properties = []
    for index in range(property_count):
        entry_offset = payload_start + index * 6
        opid_word, value = struct.unpack_from('<HI', officeart.data, entry_offset)
        opid = opid_word & 0x3FFF
        is_complex = bool(opid_word & 0x8000)
        properties.append((opid, is_complex, value))

    complex_cursor = payload_start + table_size
    recognized = {opid: [] for opid in FOPT_TEXT_PROPERTIES}
    for opid, is_complex, value in properties:
        if is_complex:
            if value > payload_end - complex_cursor:
                return None
            if opid in recognized:
                recognized[opid].append((True, complex_cursor, value))
            complex_cursor += value
        elif opid in recognized:
            recognized[opid].append((False, value, 0))
    if complex_cursor != payload_end:
        return None

    text = {}
    sources = {}
    byte_sizes = {}
    for opid, values in recognized.items():
        if len(values) > 1:
            return None
        if not values:
            continue
        is_complex, value_or_start, size = values[0]
        if not is_complex:
            if value_or_start != 0:
                return None
            continue
        decoded = _read_text_field(officeart, value_or_start, size)
        if decoded is None:
            return None
        field = FOPT_TEXT_PROPERTIES[opid]
        text[field] = decoded
        sources[field] = officeart.location(value_or_start)
        byte_sizes[field] = size
    return text, sources, byte_sizes


def _reference_properties(officeart, candidate):
    """Read unique, simple properties used by the stored object links."""
    if candidate['fopt_count'] != 1:
        return {}
    version, count, start, end = candidate['fopt']
    if version != 3 or start + count * 6 > end:
        return {}
    matches = {}
    complex_remaining = end - start - count * 6
    for index in range(count):
        offset = start + index * 6
        opid_word, value = struct.unpack_from('<HI', officeart.data, offset)
        opid = opid_word & 0x3FFF
        if opid_word & 0x8000:
            complex_remaining -= value
            if complex_remaining < 0:
                return {}
        if opid in (TEXTBOX_ID_PROPERTY, PICTURE_INDEX_PROPERTY):
            if opid in matches:
                matches[opid] = None
            elif opid_word == TEXTBOX_ID_PROPERTY and value and not value & 0xFFFF:
                matches[opid] = (value >> 16, officeart.location(offset))
            elif opid_word == (PICTURE_INDEX_PROPERTY | 0x4000) and value:
                matches[opid] = (value, officeart.location(offset))
            else:
                matches[opid] = None
    return {key: item for key, item in matches.items() if item is not None} if complex_remaining == 0 else {}


def _metro_blob(officeart, options):
    """Find a uniquely framed metroBlob in one shape's tertiary properties."""
    match = None
    for _, count, start, end in options:
        for index in range(min(count, (end - start) // 6)):
            offset = start + index * 6
            opid, size = struct.unpack_from('<HI', officeart.data, offset)
            if opid & 0x3FFF == METRO_BLOB_PROPERTY:
                if match is not None or len(options) != 1:
                    return None, officeart.location(offset)
                match = (offset, opid, size)
    if match is None:
        return None
    property_offset, opid, size = match
    if not opid & 0x8000 and size == 0:
        return None
    invalid = (None, officeart.location(property_offset))
    if not opid & 0x8000 or not size:
        return invalid
    version, count, start, end = options[0]
    cursor = start + 6 * count
    if version != 3 or cursor > end:
        return invalid
    payload_start = None
    for offset in range(start, start + 6 * count, 6):
        flags, length = struct.unpack_from('<HI', officeart.data, offset)
        if flags & 0x8000:
            if length > end - cursor:
                return invalid
            if offset == property_offset:
                payload_start = cursor
            cursor += length
    if cursor != end or payload_start is None:
        return invalid
    source = officeart.location(payload_start, size=size, property_offset=officeart.base + property_offset)
    return officeart.view(payload_start, size), source


def inline_metro_blob(container, require):
    """Read alternative XML from the direct shape in a validated inline PICF."""
    state = {'records': 0}
    record = _read_record(container, 0, len(container.data), state, require)
    candidate = _shape_candidate(container, record, 0, state, require)
    if candidate is None or not candidate['shape_id'] or candidate['flags'] & HIDDEN_FROM_LINKING:
        return None
    blob = _metro_blob(container, candidate['tertiary'])
    if blob is not None:
        return candidate['shape_id'], blob
    return None


def _metadata_for(candidate, officeart, require, output_state):
    """Build verified metadata, decoding optional text only when eligible."""
    flags = candidate['flags']
    metadata = {'source': officeart.location(candidate['source_offset'])}
    if flags & GROUP_SHAPE:
        metadata['is_group'] = True
    if flags & OLE_SHAPE:
        metadata['is_ole_shape'] = True
    if flags & (1 << 8):
        metadata['is_connector'] = True
    if flags & (1 << 11):
        shape_type = candidate['shape_type_code']
        metadata['shape_type_code'] = shape_type
        if shape_type in SHAPE_TYPES:
            metadata['shape_type'] = SHAPE_TYPES[shape_type]

    if not candidate['fopt_count']:
        return metadata
    if candidate['fopt_count'] > 1:
        metadata['text_status'] = 'invalid_or_unsupported'
        return metadata
    decoded = _read_fopt(officeart, candidate['fopt'])
    if decoded is None:
        metadata['text_status'] = 'invalid_or_unsupported'
        return metadata
    text, sources, byte_sizes = decoded
    total_size = output_state['text_bytes'] + sum(byte_sizes.values())
    text_location = next(iter(sources.values()), metadata['source'])
    require(total_size <= MAX_TOTAL_TEXT_BYTES, text_location,
            'OfficeArt output text budget exceeded', 'resource_limit')
    output_state['text_bytes'] = total_size
    metadata.update(text)
    if sources:
        metadata['text_sources'] = sources
    return metadata


def _anchored_candidate_path(candidate, by_id, requested):
    """Find the closest unique anchor through validated stored group heads."""
    shape_id = candidate['shape_id']
    if shape_id in requested:
        return (shape_id,)
    trail = [shape_id]
    for group_id in reversed(candidate['group_path'] or ()):
        group = by_id.get(group_id)
        if not group or not group['flags'] & GROUP_SHAPE:
            return ()
        if group['flags'] & (DELETED_SHAPE | OLE_SHAPE):
            return ()
        trail.append(group_id)
        if group_id in requested and not group['flags'] & PATRIARCH_SHAPE:
            return tuple(reversed(trail))
    return ()


def _collect_object_links(officeart, by_id, requested, textboxes, links):
    """Retain stored links only for uniquely identified, anchored shapes."""
    for shape_id, candidate in by_id.items():
        if not shape_id or candidate['flags'] & HIDDEN_FROM_LINKING:
            continue
        path = _anchored_candidate_path(candidate, by_id, requested)
        if not path:
            continue
        refs = _reference_properties(officeart, candidate)
        textbox = refs.get(TEXTBOX_ID_PROPERTY)
        if textbox and textboxes.get(shape_id) == textbox[0]:
            links['textboxes'][shape_id] = (path, textbox[1])
        picture = refs.get(PICTURE_INDEX_PROPERTY)
        if picture:
            links['pictures'].append((path, *picture))
        blob = _metro_blob(officeart, candidate['tertiary'])
        if blob is not None:
            links['metro_blobs'].append((path, blob))


def read_officeart(officeart, wanted_by_label, require, *, textbox_ids_by_label=None, object_links=None, blocked_by_label=None):
    """Return metadata for unique requested shapes in validated drawings.

    ``officeart`` is a bounded Region already checked for FIB range and
    ownership. ``wanted_by_label`` maps MS-DOC drawing labels 0 and 1 to
    requested shape-ID sets. ``require`` is the reader's structural guard.
    Optional object links return evidence for unique, anchored descendants.
    """
    state = {'records': 0, 'text_bytes': 0}
    region_end = len(officeart.data)
    require(region_end >= 8, officeart.location(),
            'OfficeArt stream is empty or truncated')
    first = _read_record(officeart, 0, region_end, state, require)
    _check_container(first, DGG_CONTAINER, officeart, 0, require)
    cursor = first[4]
    results = {}
    labels_seen = set()
    while cursor < region_end:
        label_offset = cursor
        label = officeart.data[cursor]
        require(label in (0, 1) and label not in labels_seen,
                officeart.location(label_offset),
                'Invalid or repeated OfficeArt drawing label')
        labels_seen.add(label)
        cursor += 1
        drawing = _read_record(officeart, cursor, region_end, state, require)
        candidates = []
        _walk_container(officeart, drawing, DG_CONTAINER, cursor, 1,
                        state, candidates, require)
        shape_counts = Counter(candidate['shape_id'] for candidate in candidates)
        unique = {item['shape_id']: item for item in candidates if shape_counts[item['shape_id']] == 1}
        requested = wanted_by_label.get(label, ())
        for shape_id, candidate in unique.items():
            flags = candidate['flags']
            if shape_id not in requested or flags & (PATRIARCH_SHAPE | DELETED_SHAPE):
                continue
            results[(label, shape_id)] = _metadata_for(
                candidate, officeart, require, state)
        if object_links is not None and requested:
            links = object_links[label] = {'textboxes': {}, 'pictures': [], 'metro_blobs': []}
            blocked = (blocked_by_label or {}).get(label, ())
            linkable = {shape_id: candidate for shape_id, candidate in unique.items() if shape_id not in blocked}
            _collect_object_links(officeart, linkable, requested, (textbox_ids_by_label or {}).get(label, {}), links)
        cursor = drawing[4]
    require(labels_seen, officeart.location(cursor),
            'OfficeArt stream has no drawing container')
    return results


def picture_store_entries(officeart, require):
    """Read the bounded BStore entry ranges, preserving their one-based index."""
    state = {'records': 0}
    dgg = _read_record(officeart, 0, len(officeart.data), state, require)
    _check_container(dgg, DGG_CONTAINER, officeart, 0, require)
    cursor = dgg[3]
    store = None
    while cursor < dgg[4]:
        child = _read_record(officeart, cursor, dgg[4], state, require)
        if child[0] == BSTORE_CONTAINER:
            require(store is None and child[1] == 0xF, officeart.location(cursor),
                    'Invalid or repeated OfficeArt BStore')
            store = child
        cursor = child[4]
    if store is None:
        return ()
    cursor = store[3]
    entries = []
    while cursor < store[4]:
        entry = _read_record(officeart, cursor, store[4], state, require)
        require(entry[0] == 0xF007 or 0xF018 <= entry[0] <= 0xF117, officeart.location(cursor),
                'Invalid OfficeArt BStore file block')
        entries.append(officeart.region(cursor, entry[4] - cursor))
        cursor = entry[4]
    require(len(entries) == store[2], officeart.location(store[3] - 8),
            'OfficeArt BStore entry count mismatch')
    return entries
