"""Saved rich-text segments and limited font metadata, without rendering."""
import re
import struct

from wps_biff8_errors import require
from wps_biff8_values import unicode_string


MAX_SEGMENTS = 16384


def _biff_font(raw):
    if len(raw) < 16:
        return {}
    height, flags, color, weight = struct.unpack_from('<4H', raw)
    result = {'height_twips_raw': height, 'italic': bool(flags & 2), 'color_index_raw': color, 'weight_raw': weight}
    if weight in (400, 700):
        result['bold'] = weight == 700
    try:
        name, end = unicode_string(raw, 14, short=True)
    except ValueError:
        return result
    if end == len(raw) and name:
        result['font_name'] = name
    return result


def attach_biff_segments(rich_strings, strings, metadata, referenced):
    fonts = [_biff_font(bytes.fromhex(item['payload_hex'])) for item in metadata if item['name'] == 'Font' and item['scope'] == 'globals']
    referenced_fonts = {}
    remaining = MAX_SEGMENTS
    for rich in rich_strings:
        runs = [(offset, font) for offset, font in struct.iter_unpack('<HH', rich['runs_bytes']) if offset != rich['utf16_units']]
        for _, font in runs:
            require((font if font < 4 else font - 1) < len(fonts), 'Rich string FontIndex exceeds global font collection')
        if rich['sst_index'] not in referenced:
            continue
        if not runs or runs[0][0] != 0:
            runs.insert(0, (0, None))
        if len(runs) > remaining:
            rich['segments_status'] = 'segment_budget_exceeded'
            continue
        text = strings[rich['sst_index']].encode('utf-16le')
        ends = [offset for offset, _ in runs[1:]] + [rich['utf16_units']]
        segments = []
        try:
            for (start, font), end in zip(runs, ends):
                segments.append({'text': text[2 * start:2 * end].decode('utf-16le'), 'font_index': font})
        except UnicodeDecodeError:
            rich['segments_status'] = 'run_splits_surrogate_pair'
        else:
            rich['segments'] = segments
            remaining -= len(segments)
            referenced_fonts.update((str(font), fonts[font if font < 4 else font - 1]) for _, font in runs if font is not None)
    return referenced_fonts


def ooxml_run_font(run, tag):
    """Return explicitly saved attributes from a single run property container."""
    properties = run.findall(tag('rPr'))
    if len(properties) != 1:
        return {}
    properties = properties[0]
    result = {}
    for field, name in (('font_name', 'rFont'), ('bold', 'b'), ('italic', 'i'), ('color', 'color')):
        nodes = properties.findall(tag(name))
        if len(nodes) != 1 or len(nodes[0]):
            continue
        node = nodes[0]
        if name == 'rFont':
            value = node.get('val', '')
            if 1 <= len(value) <= 255:
                result[field] = value
        elif name in ('b', 'i'):
            value = node.get('val', '1')
            if value in ('0', '1', 'false', 'true'):
                result[field] = value in ('1', 'true')
        else:
            color = {key: node.get(key) for key, pattern in (
                ('rgb', r'[0-9A-Fa-f]{8}'), ('indexed', r'[0-9]{1,3}'), ('theme', r'[0-9]{1,3}'), ('auto', r'0|1|false|true')
            ) if re.fullmatch(pattern, node.get(key, ''))}
            if len(color) == 1:
                result[field] = color
    return result
