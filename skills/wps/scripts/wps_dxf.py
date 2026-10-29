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


def validate_dxf(payload):
    """Validate and expose the bounded structure of an MS-XLS DXF payload.

    The returned XFProp payloads are raw hex strings. This function does not
    interpret formatting properties or determine an effective style.
    """
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
        raise ValueError("DXF FrtHeader fFrtRef and fFrtAlert must be zero")

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
    for property_index in range(property_count):
        if property_offset + XFPROP_HEADER_SIZE > len(data):
            raise ValueError("XFProp {} header is truncated".format(property_index))

        property_id = int.from_bytes(data[property_offset:property_offset + 2], "little")
        property_size = int.from_bytes(data[property_offset + 2:property_offset + 4], "little")
        if property_id > XFPROP_TYPE_MAX or property_id in XFPROP_TYPE_RESERVED:
            raise ValueError("XFProp {} has unsupported type 0x{:04X}".format(
                property_index, property_id
            ))
        if property_size < XFPROP_HEADER_SIZE:
            raise ValueError("XFProp {} cb is smaller than its header".format(property_index))

        property_end = property_offset + property_size
        if property_end > len(data):
            raise ValueError("XFProp {} extends beyond the DXF payload".format(property_index))

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
        raise ValueError("XFProps cannot mix fill pattern and gradient properties")

    f_new_border = bool((dxf_flags >> 1) & 1)
    if not f_new_border and property_ids.intersection(XFPROP_INTERNAL_BORDERS):
        raise ValueError("DXF fNewBorder is clear but XFProps contains an internal border")

    return {
        "record_type": record_type,
        "frt_flags": frt_flags,
        "frt_reserved_hex": data[4:DXF_FRT_HEADER_SIZE].hex(),
        "dxf_flags": dxf_flags,
        "f_new_border": f_new_border,
        "xfprops_reserved": xfprops_reserved,
        "property_count": property_count,
        "properties": properties,
    }
