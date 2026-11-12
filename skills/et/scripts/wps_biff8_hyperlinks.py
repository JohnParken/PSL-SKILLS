"""Bounded BIFF8 HLink/HLinkTooltip metadata extraction.

Only saved hyperlink metadata is read. Targets are never resolved, followed,
executed or fetched; formulas and external content are not evaluated. OLE
monikers are kept opaque because their variable binary layout is not decoded.
"""
import struct

from wps_biff8_headers import column_name

HLINK = 0x01B8
HLINK_TOOLTIP = 0x0800
MAX_HYPERLINKS = 4096
MAX_LINK_TEXT = 32767
MAX_TOOLTIP_TEXT = 255
_STREAM_VERSION = 2
_FLAG_HAS_MONIKER = 0x0001
_FLAG_ABSOLUTE = 0x0002
_FLAG_SITE_DISPLAY_NAME = 0x0004
_FLAG_HAS_LOCATION = 0x0008
_FLAG_HAS_DISPLAY = 0x0010
_FLAG_HAS_GUID = 0x0020
_FLAG_HAS_CREATION = 0x0040
_FLAG_HAS_FRAME = 0x0080
_FLAG_MONIKER_AS_STRING = 0x0100
_FLAG_ABS_FROM_REL = 0x0200
_KNOWN_FLAGS = (_FLAG_HAS_MONIKER | _FLAG_ABSOLUTE | _FLAG_SITE_DISPLAY_NAME
                | _FLAG_HAS_LOCATION | _FLAG_HAS_DISPLAY | _FLAG_HAS_GUID
                | _FLAG_HAS_CREATION | _FLAG_HAS_FRAME
                | _FLAG_MONIKER_AS_STRING | _FLAG_ABS_FROM_REL)


def _ref_bounds(payload, position, record):
    if position + 8 > len(payload):
        raise ValueError('Truncated Ref8U at ' + str(record[0]))
    first_row, last_row, first_col, last_col = struct.unpack_from(
        '<4H', payload, position)
    if not (first_row <= last_row <= 65535 and first_col <= last_col <= 255):
        raise ValueError('Invalid HLink Ref8U bounds at ' + str(record[0]))
    return first_row, last_row, first_col, last_col


def _ref_text(bounds):
    first_row, last_row, first_col, last_col = bounds
    start = column_name(first_col + 1) + str(first_row + 1)
    end = column_name(last_col + 1) + str(last_row + 1)
    return start if start == end else start + ':' + end


def _hyperlink_string(payload, position, name, record, limit=MAX_LINK_TEXT,
                      allow_empty=False):
    if position + 4 > len(payload):
        raise ValueError('Truncated ' + name + ' length at ' + str(record[0]))
    units = struct.unpack_from('<I', payload, position)[0]
    position += 4
    if units < 1 or units > limit + 1:
        raise ValueError('Invalid ' + name + ' length at ' + str(record[0]))
    byte_count = units * 2
    if position + byte_count > len(payload):
        raise ValueError('Truncated ' + name + ' at ' + str(record[0]))
    raw = payload[position:position + byte_count]
    position += byte_count
    if raw[-2:] != b'\x00\x00':
        raise ValueError('Unterminated ' + name + ' at ' + str(record[0]))
    try:
        value = raw[:-2].decode('utf-16le')
    except UnicodeDecodeError as exc:
        raise ValueError('Malformed ' + name + ' Unicode at '
                         + str(record[0])) from exc
    if '\x00' in value:
        raise ValueError('Embedded NUL in ' + name + ' at ' + str(record[0]))
    if not value and not allow_empty:
        raise ValueError('Empty ' + name + ' at ' + str(record[0]))
    return value, position


def parse_hlink(payload, record):
    """Return one bounded hyperlink descriptor; never resolve or follow it."""
    if len(payload) < 32:
        raise ValueError('Truncated HLink header at ' + str(record[0]))
    bounds = _ref_bounds(payload, 0, record)
    clsid = payload[8:24]
    version, flags = struct.unpack_from('<II', payload, 24)
    if version != _STREAM_VERSION:
        raise ValueError('Unsupported HLink stream version at '
                         + str(record[0]))
    position = 32
    display = None
    frame = None
    moniker = None
    location = None
    opaque_moniker = False
    unparsed = False
    if flags & _FLAG_HAS_DISPLAY:
        display, position = _hyperlink_string(payload, position, 'display name',
                                             record)
    if flags & _FLAG_HAS_FRAME:
        frame, position = _hyperlink_string(payload, position, 'target frame',
                                            record)
    if flags & _FLAG_HAS_MONIKER:
        if flags & _FLAG_MONIKER_AS_STRING:
            moniker, position = _hyperlink_string(payload, position, 'moniker',
                                                  record)
        else:
            opaque_moniker = True
            unparsed = True
    if flags & _FLAG_HAS_LOCATION:
        if opaque_moniker:
            unparsed = True
        else:
            location, position = _hyperlink_string(payload, position, 'location',
                                                   record)
    if not opaque_moniker:
        if flags & _FLAG_HAS_GUID:
            if position + 16 > len(payload):
                raise ValueError('Truncated HLink GUID at ' + str(record[0]))
            position += 16
        if flags & _FLAG_HAS_CREATION:
            if position + 8 > len(payload):
                raise ValueError('Truncated HLink creation time at '
                                 + str(record[0]))
            position += 8
        if position != len(payload):
            unparsed = True
    if opaque_moniker:
        target_kind = 'ole_moniker_opaque'
        target = None
    elif moniker is not None:
        target_kind = 'external_string'
        target = moniker
    elif location is not None:
        target_kind = 'internal'
        target = location
    else:
        target_kind = 'none'
        target = None
    item = {
        'kind': 'biff8_hlink',
        'cell': column_name(bounds[2] + 1) + str(bounds[0] + 1),
        'target_kind': target_kind,
        'display': display,
        'target_frame': frame,
        'moniker': moniker,
        'location': location,
        'target': target,
        'target_resolved': False,
        'followed': False,
        'link_executed': False,
        'ole_moniker_opaque': opaque_moniker,
        'unparsed_remaining_fields': unparsed,
        'source': {
            'record_offset': record[0],
            'record_id': hex(HLINK),
            'ref_raw_hex': payload[:8].hex(),
            'ref_text': _ref_text(bounds),
            'hlink_clsid_hex': clsid.hex(),
            'stream_version': version,
            'flags_raw': flags,
            'unknown_flag_bits': flags & ~_KNOWN_FLAGS,
        },
    }
    if bounds[0] != bounds[1] or bounds[2] != bounds[3]:
        item['range'] = _ref_text(bounds)
    return item


def parse_hlink_tooltip(payload, record):
    """Return tooltip text and its Ref8U key; no target is followed."""
    if len(payload) < 12:
        raise ValueError('Truncated HLinkTooltip at ' + str(record[0]))
    rt = struct.unpack_from('<H', payload, 0)[0]
    if rt != HLINK_TOOLTIP:
        raise ValueError('HLinkTooltip rt is not 0x0800 at '
                         + str(record[0]))
    bounds = _ref_bounds(payload, 2, record)
    text = payload[10:]
    if len(text) < 2 or len(text) % 2:
        raise ValueError('Invalid HLinkTooltip string length at '
                         + str(record[0]))
    if text[-2:] != b'\x00\x00':
        raise ValueError('HLinkTooltip string is not terminated at '
                         + str(record[0]))
    try:
        value = text[:-2].decode('utf-16le')
    except UnicodeDecodeError as exc:
        raise ValueError('Malformed HLinkTooltip Unicode at '
                         + str(record[0])) from exc
    if len(value) > MAX_TOOLTIP_TEXT or '\x00' in value:
        raise ValueError('Unsupported HLinkTooltip text at ' + str(record[0]))
    return {
        'ref_raw_hex': payload[2:10].hex(),
        'ref_text': _ref_text(bounds),
        'tooltip': value,
        'source': {'record_offset': record[0], 'record_id': hex(HLINK_TOOLTIP),
                   'ref_raw_hex': payload[2:10].hex()},
    }
