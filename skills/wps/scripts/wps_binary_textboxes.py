"""Read bounded MS-DOC textbox text-range references without decoding shapes."""

MAX_TEXTBOX_RECORDS = 4096
MAX_TEXTBOX_COPY_UNITS = 65536
PLC_FIXED_BYTES = 4
CP_BYTES = 4
FTXBXS_BYTES = 22
PLC_ENTRY_BYTES = CP_BYTES + FTXBXS_BYTES


def textbox_references(plc, story_start, story_count, read_unit, shape_anchors, require):
    """Return validated textbox CP ranges and unambiguous PlcfSpa matches.

    ``plc`` is a range-checked Region containing a PlcftxbxTxt or
    PlcfHdrtxbxTxt. ``read_unit`` accepts a global UTF-16 CP. The final PLC
    record is reserved and its FTXBXS fields and text are ignored.
    """
    location = plc.location()
    require(type(story_start) is int and story_start >= 0, location,
            'Invalid textbox story start CP')
    require(type(story_count) is int and story_count > 0, location,
            'Invalid textbox story CP count')
    require(callable(read_unit), location, 'Missing textbox UTF-16 reader')
    require(isinstance(shape_anchors, dict), location,
            'Textbox shape anchors must be a mapping')

    size = len(plc.data)
    require(size >= PLC_FIXED_BYTES and
            (size - PLC_FIXED_BYTES) % PLC_ENTRY_BYTES == 0,
            location, 'Invalid PlcftxbxTxt/PlcfHdrtxbxTxt size')
    record_count = (size - PLC_FIXED_BYTES) // PLC_ENTRY_BYTES
    require(record_count >= 1, location,
            'Textbox PLC must contain a final reusable record')
    require(record_count <= MAX_TEXTBOX_RECORDS, plc.location(0, count=record_count),
            'Textbox PLC record budget exceeded', 'resource_limit')

    cp_count = record_count + 1
    cp_array_bytes = cp_count * CP_BYTES
    ftxbxs_start = cp_array_bytes
    cps = [plc.number(index * CP_BYTES) for index in range(cp_count)]
    require(all(first < second for first, second in zip(cps, cps[1:])),
            location, 'Textbox PLC CPs must be strictly increasing')
    # The final CP only terminates the ignored FTXBXS range. The start of
    # that range still bounds the preceding real or reusable record.
    require(all(cp <= story_count for cp in cps[:-1]), location,
            'Textbox PLC CP outside declared story')

    units = {}

    def unit_at(relative_cp, field_location):
        global_cp = story_start + relative_cp
        if global_cp not in units:
            unit = read_unit(global_cp)
            require(type(unit) is int and 0 <= unit <= 0xFFFF,
                    field_location, 'Textbox reader returned an invalid UTF-16 unit')
            units[global_cp] = unit
        return units[global_cp]

    def splits_surrogate_pair(relative_cp, field_location):
        if relative_cp <= 0 or relative_cp >= story_count:
            return False
        previous = unit_at(relative_cp - 1, field_location)
        following = unit_at(relative_cp, field_location)
        return 0xD800 <= previous <= 0xDBFF and 0xDC00 <= following <= 0xDFFF

    records = []
    for index in range(record_count - 1):
        record_offset = ftxbxs_start + index * FTXBXS_BYTES
        record_location = plc.location(record_offset, textbox_index=index + 1)
        reusable = plc.number(record_offset + 8, 2)
        shape_id = plc.number(record_offset + 14)
        cp_start = cps[index]
        cp_end = cps[index + 1]
        cp_length = cp_end - cp_start

        require(reusable == 0 or reusable & 1, record_location,
                'Invalid FTXBXS fReusable value')
        if reusable:
            require(cp_length == 1, record_location,
                    'Reusable textbox range must contain one CP')
            require(shape_id == 0, record_location,
                    'Reusable textbox lid must be zero')
            continue

        require(cp_length > 1, record_location,
                'Real textbox range must contain more than one CP')
        require(shape_id > 0, record_location,
                'Real textbox lid must contain a nonzero shape ID')
        require(not splits_surrogate_pair(cp_start, record_location),
                record_location, 'Textbox range starts inside a UTF-16 surrogate pair')
        require(not splits_surrogate_pair(cp_end, record_location),
                record_location, 'Textbox range ends inside a UTF-16 surrogate pair')
        require(unit_at(cp_end - 1, record_location) == 0x000D,
                record_location, 'Real textbox range must end with U+000D')
        records.append({
            'textbox_index': index + 1,
            'shape_id': shape_id,
            'text_range': {
                'stream': 'WordDocument',
                'cp_start': story_start + cp_start,
                'cp_end': story_start + cp_end,
            },
            'source': plc.location(record_offset),
        })

    anchors_by_shape = {}
    for anchor_cp, anchor in shape_anchors.items():
        anchor_location = plc.location(0, anchor_cp=anchor_cp)
        require(type(anchor_cp) is int and anchor_cp >= 0, anchor_location,
                'Invalid validated shape anchor CP')
        require(isinstance(anchor, dict) and 'shape_id' in anchor,
                anchor_location, 'Validated shape anchor is missing shape_id')
        shape_id = anchor['shape_id']
        require(type(shape_id) is int and 0 < shape_id <= 0xFFFFFFFF,
                anchor_location, 'Invalid validated shape anchor shape_id')
        anchors_by_shape.setdefault(shape_id, []).append(anchor_cp)

    textbox_shape_counts = {}
    for record in records:
        shape_id = record['shape_id']
        textbox_shape_counts[shape_id] = textbox_shape_counts.get(shape_id, 0) + 1

    output = []
    for record in records:
        shape_id = record['shape_id']
        matching_anchors = anchors_by_shape.get(shape_id, ())
        if textbox_shape_counts[shape_id] == 1 and len(matching_anchors) == 1:
            record['anchor_status'] = 'matched_spa_shape_id'
            record['anchor_cp'] = matching_anchors[0]
        else:
            record['anchor_status'] = 'unknown'
        output.append(record)
    return output


def attach_textbox_references(region, plc, count, read_unit, anchors, hidden_shape_cps, *, copy_limit=MAX_TEXTBOX_COPY_UNITS):
    """Add stored textbox ranges without discarding readable story text."""
    from wps_access import ParseError, require

    cp_start = region['source']['cp_start']
    try:
        references = textbox_references(plc, cp_start, count, read_unit, anchors, require)
    except ParseError as exc:
        if exc.status == 'resource_limit':
            raise
        region['issues'] = [*region['issues'], {'code': 'textbox_metadata_invalid', **exc.location, 'message': str(exc)}]
        return

    for reference in references:
        if reference.get('anchor_cp') in hidden_shape_cps:
            del reference['anchor_cp']
            reference['anchor_status'] = 'unknown'
    issues = [issue for issue in region['issues'] if issue['code'] != 'story_references_not_decoded']
    issues.append({'code': 'textbox_layout_unverified', 'stream': plc.stream,
                   'message': 'Stored textbox ranges read; linked frame chains, display order and pages remain unresolved'})
    region.update(references=references, issues=issues, status='partial',
                  anchor_status='spa_shape_id_matches_only' if any('anchor_cp' in ref for ref in references) else 'unknown')
    if any(issue['code'] == 'stored_field_results_unverified' for issue in issues):
        region['textbox_text_status'] = 'combined_story_only_after_field_filtering'
        return
    data = None
    if count <= copy_limit:
        data = region['text'].encode('utf-16le')
    region['textbox_text_status'] = 'combined_story_with_utf16_ranges' if data is None else 'separate_text_with_utf16_ranges'
    # Without fields all supported marker substitutions preserve UTF-16 CPs.
    for reference in references:
        begin = reference['text_range']['cp_start'] - cp_start
        end = reference['text_range']['cp_end'] - cp_start
        reference['story_text_utf16_range'] = {'start': begin, 'end': end}
        if data is not None:
            reference['text'] = data[2 * begin:2 * end].decode('utf-16le')
