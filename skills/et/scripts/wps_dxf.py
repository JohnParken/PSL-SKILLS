"""Structural parser for the MS-XLS DXF record (record type 0x088D)."""

DXF_RECORD_TYPE = 0x088D
DXF_FRT_HEADER_SIZE = 12
DXF_FLAGS_SIZE = 2
XFPROPS_HEADER_SIZE = 4
XFPROP_HEADER_SIZE = 4
XFPROP_TYPE_MAX = 0x002C
XFPROP_TYPE_RESERVED = (0x0027, 0x0028)
XFPROP_COUNT_MAX = 1024
DXF_PAYLOAD_MAX_SIZE = 8224
XFPROP_FILL_PATTERN = 0x0000
XFPROP_GRADIENT = 0x0003
XFPROP_GRADIENT_STOP = 0x0004
XFPROP_INTERNAL_BORDERS = (0x000B, 0x000C)


# MS-XLS 2.5.283 XFProp: only fixed-width scalar and LPWideString properties
# whose value constraints are independently specified are interpreted here.
BOOLEAN_PROPERTIES = frozenset((0x000D, 0x000E, 0x0014, 0x0015, 0x0016, 0x0017,
                                0x001C, 0x001D, 0x001E, 0x001F, 0x0020, 0x0021, 0x002B, 0x002C))
SCALAR_PROPERTIES = {
    0x000F: (1, 'horizontal_alignment', frozenset(range(8)) | {255}),
    0x0010: (1, 'vertical_alignment', frozenset(range(5))),
    0x0012: (2, 'absolute_indentation', frozenset(range(16))),
    0x0013: (1, 'reading_order', frozenset(range(3))),
    0x0022: (1, 'character_set', None),
    0x0023: (1, 'font_family', frozenset(range(6))),
    0x0024: (4, 'text_size_twips', range(20, 8192)),
    0x002A: (2, 'relative_indentation', frozenset(range(-15, 16)) | {255}),
}


def interpret_property(property_id, raw, index):
    """Validate a selected property without applying it to a cell or style."""
    if property_id == 0x0018:
        if len(raw) < 2:
            raise ValueError('DXF font name lacks LPWideString length')
        units = int.from_bytes(raw[:2], 'little')
        if units > 32 or len(raw) != 2 + units * 2:
            raise ValueError('DXF font name length exceeds 32 units or disagrees with payload')
        try:
            value = raw[2:].decode('utf-16le')
        except UnicodeDecodeError as exc:
            raise ValueError('DXF font name contains malformed UTF-16') from exc
        return dict(index=index, property_id=property_id, kind='font_name', value=value)
    if property_id in BOOLEAN_PROPERTIES:
        width, kind, allowed = 1, 'boolean_flag', (0, 1)
    elif property_id in SCALAR_PROPERTIES:
        width, kind, allowed = SCALAR_PROPERTIES[property_id]
    else:
        return None
    if len(raw) != width:
        raise ValueError('DXF property 0x{:04X} requires exactly {} payload bytes'.format(property_id, width))
    value = int.from_bytes(raw, 'little', signed=property_id == 0x002A)
    if allowed is not None and value not in allowed:
        raise ValueError('DXF property 0x{:04X} has invalid value {}'.format(property_id, value))
    return dict(index=index, property_id=property_id, kind=kind, value=value)


def validate_dxf(payload, *, formatting_policy="strict"):
    """Validate and expose the bounded structure of an MS-XLS DXF payload.

    The returned XFProp payloads are raw hex strings. This function does not
    determine an effective style. Selected property values are checked separately.
    """
    if formatting_policy not in ("strict", "preserve"):
        raise ValueError("Unknown DXF formatting policy")
    formatting_issues = []

    def formatting_problem(message, **location):
        if formatting_policy == "strict":
            raise ValueError(message)
        formatting_issues.append(dict(code="dxf_formatting_uninterpreted", message=message, **location))

    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise ValueError("DXF payload must be bytes-like")

    payload_size = payload.nbytes if isinstance(payload, memoryview) else len(payload)
    if payload_size > DXF_PAYLOAD_MAX_SIZE:
        raise ValueError("DXF payload exceeds the 8224-byte BIFF record limit")

    data = bytes(payload)
    minimum_size = DXF_FRT_HEADER_SIZE + DXF_FLAGS_SIZE + XFPROPS_HEADER_SIZE
    if len(data) < minimum_size:
        raise ValueError("DXF payload is truncated before the XFProps header")

    record_type = int.from_bytes(data[0:2], "little")
    if record_type != DXF_RECORD_TYPE:
        raise ValueError("DXF FrtHeader rt must be 0x088D")

    frt_flags = int.from_bytes(data[2:4], "little")
    # For DXF, the FrtHeader scope and alert flags are both required to be 0.
    if frt_flags & 0x0003:
        formatting_problem("DXF FrtHeader fFrtRef/fFrtAlert are unsupported; no reference or alert semantics applied", offset=2)

    dxf_flags_offset = DXF_FRT_HEADER_SIZE
    dxf_flags = int.from_bytes(
        data[dxf_flags_offset:dxf_flags_offset + DXF_FLAGS_SIZE], "little"
    )
    xfprops_offset = dxf_flags_offset + DXF_FLAGS_SIZE
    xfprops_reserved = int.from_bytes(data[xfprops_offset:xfprops_offset + 2], "little")
    property_count = int.from_bytes(data[xfprops_offset + 2:xfprops_offset + 4], "little")
    property_offset = xfprops_offset + XFPROPS_HEADER_SIZE

    if property_count > XFPROP_COUNT_MAX:
        raise ValueError("XFProps cprops exceeds the per-record property limit")

    # Every XFProp needs at least its 4-byte type/size header. Check this
    # before iterating so an impossible count cannot drive unbounded parsing.
    remaining_size = len(data) - property_offset
    if property_count > remaining_size // XFPROP_HEADER_SIZE:
        raise ValueError("XFProps cprops exceeds the available property data")

    properties = []
    interpretations = []
    for property_index in range(property_count):
        if property_offset + XFPROP_HEADER_SIZE > len(data):
            raise ValueError("XFProp {} header is truncated".format(property_index))

        property_id = int.from_bytes(data[property_offset:property_offset + 2], "little")
        property_size = int.from_bytes(data[property_offset + 2:property_offset + 4], "little")
        if property_id > XFPROP_TYPE_MAX or property_id in XFPROP_TYPE_RESERVED:
            formatting_problem("XFProp has unsupported type 0x{:04X}".format(property_id),
                               index=property_index, property_id=property_id, offset=property_offset)
        if property_size < XFPROP_HEADER_SIZE:
            raise ValueError("XFProp {} cb is smaller than its header".format(property_index))

        property_end = property_offset + property_size
        if property_end > len(data):
            raise ValueError("XFProp {} extends beyond the DXF payload".format(property_index))

        try:
            interpreted = (None if frt_flags & 0x0003 else
                           interpret_property(property_id, data[property_offset + XFPROP_HEADER_SIZE:property_end], property_index))
        except ValueError as exc:
            formatting_problem(str(exc), index=property_index, property_id=property_id, offset=property_offset, size=property_size)
            interpreted = None
        if interpreted is not None:
            interpretations.append(interpreted)
        properties.append({
            "property_id": property_id,
            "payload_hex": data[property_offset + XFPROP_HEADER_SIZE:property_end].hex(),
            "offset": property_offset,
            "size": property_size,
        })
        property_offset = property_end

    if property_offset != len(data):
        raise ValueError("DXF payload has trailing bytes after XFProps cprops")

    property_ids = {property_item["property_id"] for property_item in properties}
    if XFPROP_FILL_PATTERN in property_ids and property_ids.intersection(
        (XFPROP_GRADIENT, XFPROP_GRADIENT_STOP)
    ):
        formatting_problem("XFProps mixes fill pattern and gradient properties; neither applied")

    f_new_border = bool((dxf_flags >> 1) & 1)
    if not f_new_border and property_ids.intersection(XFPROP_INTERNAL_BORDERS):
        formatting_problem("DXF fNewBorder is clear with internal border properties; borders not applied")

    # justifyDistributed=true requires a horizontal distributed alignment.
    distributed = [item for item in interpretations if item['property_id'] == 0x0015 and item['value'] == 1]
    horizontal = [item['value'] for item in interpretations if item['property_id'] == 0x000F]
    if distributed and 7 not in horizontal:
        formatting_problem('DXF justify distributed lacks horizontal alignment 0x07; alignment not applied')
        interpretations = [item for item in interpretations if item['property_id'] != 0x0015]

    return {
        "validation_scope": "framing_and_selected_property_values",
        "formatting_policy": formatting_policy,
        "formatting_issues": formatting_issues,
        "formatting_status": "uninterpreted_due_to_issues" if formatting_issues else "selected_values_checked_not_applied",
        "effective_formatting": "unknown",
        "property_interpretations": interpretations,
        "uninterpreted_property_count": len(properties) - len(interpretations),
        "record_type": record_type,
        "frt_flags": frt_flags,
        "frt_reserved_hex": data[4:DXF_FRT_HEADER_SIZE].hex(),
        "dxf_flags": dxf_flags,
        "f_new_border": f_new_border,
        "xfprops_reserved": xfprops_reserved,
        "property_count": property_count,
        "properties": properties,
    }
