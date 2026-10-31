"""Bounded, content-identified WPS-family document and spreadsheet extraction."""
import argparse
from bisect import bisect_right
import json
import zipfile
import zlib
import os
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wps_cfb import CompoundFile, MAX_FILE_BYTES, MAX_STREAM_BYTES, SIGNATURE

# MS-DOC 2.5.1/2.5.11/2.5.14, corroborated by local C1/101/10C samples.
# (fc/lcb pairs, extension words including nFibNew, exclusive FIB end).
# MS-DOC 2.5.1/2.5.11-13; all retain the FibRgFcLcb97 CLX prefix.
FIB_LAYOUTS = {0x00C1: (93, 0, 900), 0x00D9: (108, 2, 1024),
               0x0101: (136, 2, 1248), 0x010C: (164, 2, 1472),
               0x0112: (183, 5, 1630)}
CLX_PAIR_INDEX = 33
FC_LCB_START = 154
MAX_UNITS = 8 * 1024 * 1024
MAX_PIECES = 100000
MAX_CLX_BYTES = 4 * 1024 * 1024
MAX_PRC_RECORDS = 4096
MAX_PARAGRAPHS = 100000
MAX_FIELD_DEPTH = 64
MAX_FIELDS = 100000
MAX_OBJECT_MARKERS = 1000
# MS-DOC FcCompressed substitutions recorded by the local binary_doc.py.
COMPRESSED_MAPPING = dict(zip(
    (0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x8B, 0x8C,
     0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0x9B, 0x9C, 0x9F),
    (0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030,
     0x0160, 0x2039, 0x0152, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022,
     0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x0178)))


from wps_access import ParseError, read_input, load_compound, resolve_chart_policy
from wps_paragraph_sources import paragraph_cp_ranges

def require(condition, location, message, status='corrupt'):
    if not condition:
        raise ParseError(status, location, message)


class Region:
    """A bounded view retaining absolute stream offsets for diagnostics."""

    def __init__(self, data, stream, base=0):
        self.data = memoryview(data)
        self.stream = stream
        self.base = base

    def location(self, offset=0, **extra):
        return {'stream': self.stream, 'offset': self.base + offset, **extra}

    def view(self, offset, size):
        require(0 <= offset <= len(self.data) and 0 <= size <= len(self.data) - offset,
                self.location(offset), 'Structure outside stream/region')
        return self.data[offset:offset + size]

    def number(self, offset, width=4):
        return int.from_bytes(self.view(offset, width), 'little')

    def region(self, offset, size):
        return Region(self.view(offset, size), self.stream, self.base + offset)


def parse_streams(streams, *, field_policy='reject', object_policy='reject', include_related=False, hidden_text='include', binary_paragraph_sources=False):
    require(field_policy in ('reject', 'cached_result'), {'stage': 'options'},
            'Unknown MS-DOC field policy', 'unsupported')
    require(object_policy in ('reject', 'placeholder'), {'stage': 'options'},
            'Unknown MS-DOC object policy', 'unsupported')
    require(hidden_text in ('include', 'annotate'), {'stage': 'options'},
            'Effective binary visibility is unknown; exclude unsupported', 'unsupported')
    require(type(binary_paragraph_sources) is bool, {'stage': 'options'},
            'binary_paragraph_sources must be boolean', 'unsupported')
    # CFB applies these budgets before materializing streams. Repeat at this
    # internal seam so direct tests/callers cannot bypass the parser budgets.
    require(len(streams) <= 4096, {'stage': 'streams'}, 'Stream count budget exceeded', 'resource_limit')
    aggregate = 0
    for path, data in streams.items():
        aggregate += len(data)
        require(len(data) <= MAX_STREAM_BYTES and aggregate <= MAX_FILE_BYTES,
                {'stream': '/'.join(path), 'offset': 0}, 'Stream byte budget exceeded', 'resource_limit')
    require(('WordDocument',) in streams, {'stream': 'WordDocument', 'offset': 0},
            'Missing MS-DOC stream', 'unsupported')
    word = Region(streams[('WordDocument',)], 'WordDocument')
    require(word.number(0, 2) == 0xA5EC, word.location(), 'Unknown stream signature', 'unsupported')
    base_version = word.number(2, 2)
    require(base_version in FIB_LAYOUTS, word.location(2),
            'Only evidenced C1/D9/101/10C/112 base FIB versions are supported', 'unsupported')
    flags = word.number(10, 2)
    require(not flags & 0x0100, word.location(10), 'Encrypted FIB', 'encrypted')
    # fObfuscated is ignored when fEncrypted is clear; lKey MUST still be zero.
    require(word.number(14) == 0, word.location(14), 'Nonzero lKey in unencrypted FIB', 'corrupt')
    require(not flags & 3 and word.number(8, 2) == 0, word.location(8),
            'Templates/AutoText outside subset', 'unsupported')
    require(flags & 0x1000, word.location(10), 'Non-Unicode FIB outside subset', 'unsupported')
    require(word.number(12, 2) in (0x00BF, 0x00C1), word.location(12),
            'Invalid nFibBack for MS-DOC', 'corrupt')
    require(word.number(18, 1) == 0, word.location(18), 'Non-Windows environment outside subset', 'unsupported')
    require(word.number(32, 2) == 14, word.location(32), 'Unsupported csw', 'unsupported')
    require(word.number(62, 2) == 22, word.location(62), 'Unsupported cslw', 'unsupported')
    pair_count = word.number(152, 2)
    require(pair_count in (93, 108, 136, 164, 183), word.location(152),
            'Unsupported fc/lcb pair count', 'unsupported')
    extension_offset = FC_LCB_START + pair_count * 8
    csw_new = word.number(extension_offset, 2)
    require(csw_new in (0, 2, 5), word.location(extension_offset),
            'Unsupported FIB extension length', 'unsupported')
    version = word.number(extension_offset + 2, 2) if csw_new else base_version
    require(version in FIB_LAYOUTS, word.location(extension_offset + 2 if csw_new else 2),
            'Unsupported effective nFibNew version', 'unsupported')
    expected_pairs, expected_new, fib_end = FIB_LAYOUTS[version]
    require(pair_count == expected_pairs and csw_new == expected_new,
            word.location(152), 'Inconsistent FIB version/array/extension layout', 'unsupported')
    word.view(0, fib_end)
    if csw_new:
        require(word.number(extension_offset + 4, 2) <= 15,
                word.location(extension_offset + 4), 'Invalid cQuickSavesNew')
        if version == 0x0112:
            # MS-DOC 2.5.13: all three theme language words MUST be ignored.
            # Read the exact six bytes to establish layout/truncation, not values.
            word.view(extension_offset + 6, 6)
    # MS-DOC 2.5.2: bytes 24/28 are reserved5/reserved6, not authoritative
    # text bounds, and MUST be ignored. 2.5.4 cbMac is the meaningful stream
    # extent. Keep an explicit subset policy excluding overlap with this FIB.
    cb_mac = word.number(64)
    require(fib_end <= cb_mac <= len(word.data), word.location(64), 'Invalid cbMac')
    main_count = word.number(76)
    require(word.number(88) == 0, word.location(88), 'Nonzero reserved macro-story count', 'unsupported')
    other_counts = [word.number(offset) for offset in (80, 84, 92, 96, 100, 104)]
    total_count = main_count + sum(other_counts) + int(any(other_counts))
    require(total_count <= MAX_UNITS, word.location(76), 'Text unit budget exceeded', 'resource_limit')
    table_name = '1Table' if flags & 0x0200 else '0Table'
    require((table_name,) in streams, {'stream': table_name, 'offset': 0}, 'Missing selected table')
    table = Region(streams[(table_name,)], table_name)
    pair_offset = FC_LCB_START + 8 * CLX_PAIR_INDEX
    clx_offset = word.number(pair_offset)
    clx_size = word.number(pair_offset + 4)
    require(clx_size <= MAX_CLX_BYTES, word.location(pair_offset + 4), 'CLX budget exceeded', 'resource_limit')
    clx = table.region(clx_offset, clx_size)
    cursor = 0
    prc_count = 0
    while cursor < len(clx.data) and clx.number(cursor, 1) == 1:
        prc_count += 1
        require(prc_count <= MAX_PRC_RECORDS, clx.location(cursor), 'Prc budget exceeded', 'resource_limit')
        length = clx.number(cursor + 1, 2)
        require(length <= 0x3FA2, clx.location(cursor + 1), 'Invalid Prc length')
        clx.view(cursor + 3, length)
        cursor += 3 + length
    require(cursor < len(clx.data) and clx.number(cursor, 1) == 2,
            clx.location(cursor), 'Missing Pcdt')
    length = clx.number(cursor + 1)
    require(length >= 4 and (length - 4) % 12 == 0 and cursor + 5 + length == len(clx.data),
            clx.location(cursor + 1), 'Invalid PlcPcd length/trailing CLX data')
    piece_count = (length - 4) // 12
    require(piece_count <= MAX_PIECES, clx.location(cursor + 1), 'Piece budget exceeded', 'resource_limit')
    plc = clx.region(cursor + 5, length)
    cp_bytes = 4 * (piece_count + 1)
    positions = struct.unpack_from('<' + 'I' * (piece_count + 1), plc.view(0, cp_bytes))
    require(positions[0] == 0, plc.location(), 'First CP must be zero')
    for index in range(piece_count):
        require(positions[index] < positions[index + 1], plc.location(4 * (index + 1), piece=index),
                'CP indexes must strictly increase')
    require(positions[-1] == total_count, plc.location(4 * piece_count), 'CP/story length mismatch')
    # Preflight ALL pieces and overlaps before decoding or allocating body text.
    pieces = []
    piece_properties = False
    for index in range(piece_count):
        descriptor = cp_bytes + 8 * index
        location = plc.location(descriptor + 2, piece=index)
        encoded_fc = plc.number(descriptor + 2)
        require(not encoded_fc & 0x80000000, location, 'Reserved FC flag')
        compressed = bool(encoded_fc & 0x40000000)
        file_offset = encoded_fc & 0x3FFFFFFF
        require(not compressed or file_offset % 2 == 0, location, 'Odd compressed FC')
        file_offset = file_offset // 2 if compressed else file_offset
        stride = 1 if compressed else 2
        units = positions[index + 1] - positions[index]
        end = file_offset + units * stride
        require(fib_end <= file_offset < end <= cb_mac, location,
                'Piece outside meaningful stream or overlaps FIB')
        # MS-DOC 2.9.216 Prm1: the high 15 bits index Clx.RgPrc. Validate
        # references without interpreting or executing formatting properties.
        prm = plc.number(descriptor + 6, 2)
        require(not prm & 1 or (prm >> 1) < prc_count,
                plc.location(descriptor + 6, piece=index), 'Invalid Prm1 Prc index')
        piece_properties = piece_properties or prm != 0
        pieces.append((file_offset, end, stride, index))
    ordered = sorted(pieces)
    for left, right in zip(ordered, ordered[1:]):
        require(left[1] <= right[0], word.location(right[0], piece=right[3]), 'Overlapping text pieces')

    def text_location(cp):
        index = bisect_right(positions, cp) - 1
        file_offset, _, stride, _ = pieces[index]
        return word.location(file_offset + (cp - positions[index]) * stride, piece=index, cp=cp)

    def read_unit(cp):
        index = bisect_right(positions, cp) - 1
        offset, _, stride, _ = pieces[index]
        return word.number(offset + (cp - positions[index]) * stride, stride)

    # Validate ownership jointly before decoding any optional table. Physical
    # Table Stream ranges may not alias across notes, authors, sections or BTE.
    table_spans = [(clx_offset, clx_offset + clx_size)]
    decoded_pairs = (2, 3, 4, 5, 6, 11, 36, 46, 47) if include_related else ()
    if hidden_text == 'annotate':
        decoded_pairs += (12,)
    for pair in decoded_pairs:
        offset = word.number(FC_LCB_START + pair * 8)
        size = word.number(FC_LCB_START + pair * 8 + 4)
        if size:
            table.view(offset, size)
            require(all(offset + size <= left or right <= offset for left, right in table_spans),
                    table.location(offset), 'Overlapping decoded table/CLX ranges')
            table_spans.append((offset, offset + size))
    header_mapping = None
    note_mappings = {}
    automatic_note_markers = set()
    comment_markers = set()
    if include_related:
        from wps_binary_refs import note_references
        from wps_binary_comments import comment_references
        from wps_binary_headers import header_sections
        header_mapping = header_sections(word, table, main_count + other_counts[0],
                                        other_counts[1], main_count, read_unit, require,
                                        fib_end=fib_end, cb_mac=cb_mac,
                                        text_spans=[(p[0], p[1]) for p in pieces])
        story_start = main_count
        for story_type, count in zip(('footnotes', 'headers_footers', 'comments', 'endnotes', 'textboxes', 'header_textboxes'), other_counts):
            mapping = note_references(word, table, story_type, story_start, count,
                                      main_count, read_unit, require)
            if story_type == 'comments':
                mapping = comment_references(word, table, story_start, count, main_count, read_unit, require)
                for reference in mapping or []:
                    comment_markers.add(reference['anchor']['cp'])
                    comment_markers.add(reference['text_range']['cp_start'])
            note_mappings[story_type] = mapping
            for reference in mapping or []:
                if reference.get('numbering') == 'automatic':
                    automatic_note_markers.add(reference['anchor']['cp'])
                    start = reference['text_range']['cp_start']
                    if read_unit(start) == 2:
                        automatic_note_markers.add(start)
            story_start += count

    remaining_paragraphs = MAX_PARAGRAPHS
    remaining_fields = MAX_FIELDS
    remaining_objects = MAX_OBJECT_MARKERS

    def render_story(cp_start, cp_end, story_type):
        nonlocal remaining_paragraphs, remaining_fields, remaining_objects
        body = bytearray()
        for file_offset, _, stride, index in pieces:
            begin = max(positions[index], cp_start)
            needed = max(0, min(positions[index + 1], cp_end) - begin)
            if not needed:
                continue
            piece = word.view(file_offset + (begin - positions[index]) * stride, needed * stride)
            if stride == 1:
                body.extend(bytes(piece).decode('latin1').translate(COMPRESSED_MAPPING).encode('utf-16le'))
            else:
                body.extend(piece)
        require(len(body) == (cp_end - cp_start) * 2, word.location(76), 'Decoded story unit count mismatch')
        try:
            text = body.decode('utf-16le')
        except UnicodeDecodeError as exc:
            raise ParseError('corrupt', text_location(cp_start + exc.start // 2), 'Invalid UTF-16 story text') from exc
        require(story_type != 'main' or not text or text.endswith('\r'), text_location(cp_end - 1) if cp_end > cp_start else word.location(76),
                'Missing terminal paragraph')
        # Opt-in delimiter interpretation only: no instructions are executed and
        # sprmCFSpec/Plcfld semantics are not decoded. Cached output is partial.
        cp = cp_start
        fields = []
        field_count = 0
        hidden = 0
        object_locations = []
        note_marker_locations = []
        rendered = [] if field_policy == 'cached_result' or object_policy == 'placeholder' or automatic_note_markers or comment_markers else None
        for char in text:
            if field_policy == 'cached_result' and char in '\x13\x14\x15':
                location = text_location(cp)
                if char == '\x13':
                    require(len(fields) < MAX_FIELD_DEPTH and remaining_fields > 0,
                            location, 'Field nesting/count budget exceeded', 'resource_limit')
                    fields.append((False, cp))
                    hidden += 1
                    field_count += 1
                    remaining_fields -= 1
                elif char == '\x14':
                    require(bool(fields) and not fields[-1][0], location,
                            'Field separator without start or repeated separator')
                    fields[-1] = (True, fields[-1][1])
                    hidden -= 1
                else:
                    require(bool(fields), location, 'Field end without start')
                    has_result, _ = fields.pop()
                    if not has_result:
                        hidden -= 1
            elif char == '\x05' and cp in comment_markers:
                note_marker_locations.append(text_location(cp))
                if not hidden:
                    rendered.append('\ufffc')
            elif char == '\x02' and cp in automatic_note_markers:
                note_marker_locations.append(text_location(cp))
                if not hidden:
                    rendered.append('\ufffc')
            elif char == '\x01' and object_policy == 'placeholder':
                require(remaining_objects > 0, text_location(cp),
                        'Object marker diagnostic budget exceeded', 'resource_limit')
                remaining_objects -= 1
                object_locations.append({**text_location(cp), 'placeholder_emitted': not bool(hidden)})
                if not hidden:
                    rendered.append('\ufffc')
            else:
                if ord(char) < 32 and char not in '\t\r\x07\x0b\x0c':
                    raise ParseError('unsupported', text_location(cp),
                                     'Fields/objects/C0 control codes outside plain-text subset')
                if rendered is not None and not hidden:
                    rendered.append(char)
            cp += 2 if ord(char) > 0xFFFF else 1
        require(not fields, text_location(fields[-1][1]) if fields else word.location(76),
                'Unclosed field in story')
        if rendered is not None:
            text = ''.join(rendered)
        issues = []
        if note_marker_locations:
            issues.append({'code': 'automatic_note_marker_not_rendered', 'locations': note_marker_locations,
                           'message': 'Validated annotation/note marker CP replaced by U+FFFC; numbering and character properties unverified'})
        if field_count:
            issues.append({'code': 'stored_field_results_unverified', 'stream': 'WordDocument',
                           'field_count': field_count,
                           'message': 'Field instructions omitted; cached results retained without execution, '
                                      'freshness checks or sprmCFSpec/Plcfld validation'})
        if object_locations:
            issues.append({'code': 'picture_or_data_marker_not_decoded',
                           'count': len(object_locations), 'locations': object_locations,
                           'message': 'U+0001 replaced by U+FFFC outside hidden field instructions; '
                                      'picture/data bytes, sprmCFSpec and object type not decoded'})
        table_structure = None
        if '\x07' in text and not field_count:
            from wps_binary_tables import inspect_tables
            table_structure = inspect_tables(text, cp_start, word, table, pieces, positions, fib_end,
                table_ranges=table_spans,
                word_ranges=[(item['offset'], item['offset'] + item['size'])
                             for item in header_mapping.get('word_ranges', [])] if header_mapping else (),
                piece_properties=piece_properties)
            issues.extend(table_structure['issues'])
        elif '\x07' in text:
            issues.append({'code': 'binary_table_fields_unresolved', 'stream': 'WordDocument',
                           'cp_start': cp_start, 'cp_end': cp_end})
        paragraph_total = text.count('\r') + int(bool(text) and not text.endswith('\r'))
        require(paragraph_total <= remaining_paragraphs, word.location(76), 'Paragraph budget exceeded', 'resource_limit')
        remaining_paragraphs -= paragraph_total
        # Avoid materializing up to millions of paragraph strings before the budget.
        paragraphs = (text[:-1] if text.endswith('\r') else text).split('\r') if text else []

        def normalize(value):
            return value.replace('\x0b', '\n').replace('\x07', '\t')

        story_result = {'status': 'partial' if issues else 'ok', 'format': 'ms_doc_subset', 'fib_version': hex(version),
                'fib_base_version': hex(base_version),
                'main_cp_units': main_count, 'text': normalize(text).replace('\r', '\n'),
                'paragraphs': [normalize(value) for value in paragraphs], 'partial': bool(issues),
                'field_policy': field_policy, 'fields_evaluated': False, 'issues': issues,
                'object_policy': object_policy, 'objects_decoded': False,
                'scope': 'stored_main_story_only', 'producer_verified': False}
        if table_structure is not None:
            story_result['binary_table_structure'] = table_structure
        if binary_paragraph_sources:
            if rendered is None:
                sources = paragraph_cp_ranges(text, cp_start)
                require(len(sources) == len(paragraphs), word.location(76),
                        'Stored paragraph source count mismatch')
                story_result['paragraph_sources'] = sources
                story_result['paragraph_source_status'] = 'exact_stored_cp'
            else:
                story_result['paragraph_sources'] = []
                story_result['paragraph_source_status'] = 'unavailable_after_text_transform'
                story_result['issues'].append({'code': 'binary_paragraph_source_alignment_unavailable',
                    'stream': 'WordDocument', 'cp_start': cp_start, 'cp_end': cp_end,
                    'message': 'Stored CP ranges cannot be aligned with transformed text; no ranges emitted'})
                story_result['partial'] = True
                story_result['status'] = 'partial'
        return story_result

    result = render_story(0, main_count, 'main')
    if not include_related:
        omitted = []
        story_start = main_count
        for story_type, count in zip(('footnotes', 'headers_footers', 'comments', 'endnotes', 'textboxes', 'header_textboxes'), other_counts):
            if count:
                omitted.append({'type': story_type, 'cp_units': count,
                    'source': {'stream': 'WordDocument', 'cp_start': story_start, 'cp_end': story_start + count},
                    'reason': 'include_related_not_requested'})
            story_start += count
        result['omitted_regions'] = omitted
        if omitted:
            result['issues'].append({'code': 'related_content_not_requested',
                'stream': 'WordDocument', 'region_count': len(omitted),
                'message': 'Nonempty related stories were not read; use include-related to request them'})
            result['status'], result['partial'] = 'partial', True
    if include_related:
        related = []
        cp_start = main_count
        paragraph_count = len(result['paragraphs'])
        for story_type, count_offset, count in zip(
                ('footnotes', 'headers_footers', 'comments', 'endnotes', 'textboxes', 'header_textboxes'),
                (80, 84, 92, 96, 100, 104), other_counts):
            cp_end = cp_start + count
            story = render_story(cp_start, cp_end, story_type)
            paragraph_count += len(story['paragraphs'])
            require(paragraph_count <= MAX_PARAGRAPHS, word.location(count_offset),
                    'Combined story paragraph budget exceeded', 'resource_limit')
            story_issues = story['issues']
            references = note_mappings.get(story_type)
            if count and references is None:
                story_issues = story_issues + [{'code': 'story_references_not_decoded',
                    'stream': 'WordDocument', 'cp_start': cp_start, 'cp_end': cp_end,
                    'message': 'Stored subdocument text only; individual note/header/textbox ranges, anchors and page mapping are unknown'}]
            if references is not None:
                story_issues = story_issues + [{'code': 'note_reference_properties_unverified',
                    'stream': table_name, 'message': 'Note CP mapping verified; character properties, numbering display and page mapping are not interpreted'}]
            region = {'type': story_type, 'empty': count == 0,
                'source': {'stream': 'WordDocument', 'cp_start': cp_start, 'cp_end': cp_end,
                           'count_offset': count_offset},
                'text': story['text'], 'paragraphs': story['paragraphs'],
                'references': references if references is not None else [],
                'anchor_status': 'cp_mapped_properties_unverified' if references is not None else ('unknown' if count else 'not_applicable'),
                'status': 'partial' if story_issues else 'ok', 'issues': story_issues}
            if 'binary_table_structure' in story:
                region['binary_table_structure'] = story['binary_table_structure']
            if binary_paragraph_sources:
                region['paragraph_sources'] = story['paragraph_sources']
                region['paragraph_source_status'] = story['paragraph_source_status']
            if story_type == 'headers_footers' and header_mapping is not None:
                region['section_mapping'] = header_mapping
                header_issue = {'code': 'section_header_properties_uninterpreted',
                                'stream': table_name,
                                'message': 'Stored section/slot ranges only; SEPX semantics and effective page headers are unknown'}
                region['issues'] = region['issues'] + [header_issue]
                story_issues = story_issues + [header_issue]
                region['status'] = 'partial'
                region['anchor_status'] = header_mapping['mapping_status']
            related.append(region)
            result['issues'].extend(story_issues)
            cp_start = cp_end
        result['related_stories'] = related
        result['scope'] = 'stored_main_and_related_subdocuments'
        result['partial'] = bool(result['issues'])
        result['status'] = 'partial' if result['partial'] else 'ok'
    result['visibility_status'] = 'visibility_unknown'
    if hidden_text == 'annotate':
        from wps_msdoc_charprops import inspect_character_properties
        bte_pair_offset = FC_LCB_START + 12 * 8
        bte_start = word.number(bte_pair_offset)
        bte_span = (bte_start, bte_start + word.number(bte_pair_offset + 4))
        result['character_properties'] = inspect_character_properties(word, table, pieces, positions, fib_end,
            table_ranges=tuple(span for span in table_spans if span != bte_span),
            cp_limit=main_count + sum(other_counts) if include_related else main_count)
        if header_mapping is not None:
            for section_range in header_mapping.get('word_ranges', []):
                start = section_range['offset']
                stop = start + section_range['size']
                for left, right in result['character_properties'].get('fkp_ranges', []):
                    require(stop <= left or right <= start, word.location(start),
                            'Section properties overlap character FKP page')
        structures = [result.get('binary_table_structure', {})]
        structures.extend(region.get('binary_table_structure', {}) for region in result.get('related_stories', []))
        for structure in structures:
            for left, right in structure.get('fkp_ranges', []):
                for char_left, char_right in result['character_properties'].get('fkp_ranges', []):
                    require(right <= char_left or char_right <= left, word.location(left),
                            'Paragraph FKP overlaps character FKP')
        result['character_properties']['annotation_scope'] = 'selected_stories' if include_related else 'declared_main'
        result['issues'].append({'code': 'binary_character_properties_not_effective',
            'stream': table_name, 'message': 'CHPX evidence only; styles, piece PRMs and effective visibility/revisions unresolved. No text filtered.'})
        result['partial'] = True
        result['status'] = 'partial'
    return result


def failure(status, location, message):
    return {'status': status, 'location': location, 'message': message,
            'partial': False, 'text': None, 'producer_verified': False}


def _read_bytes(data, *, source=None, compatibility='data', chart_policy=None,
               field_policy='reject', object_policy='reject', revisions=None, include_related=False,
               hidden_text="include", binary_paragraph_sources=False):
    """Identify content first; the suffix is provenance, never a format selector."""
    require(isinstance(data, bytes) and len(data) <= MAX_FILE_BYTES,
            {'stage': 'input'}, 'Input exceeds byte budget or is not bytes', 'resource_limit')
    require(compatibility in ('strict', 'legacy_excel_clsid', 'legacy_word_clsid', 'data'), {'stage': 'options'},
            'Unknown compatibility mode', 'unsupported')
    require(type(binary_paragraph_sources) is bool, {'stage': 'options'},
            'binary_paragraph_sources must be boolean', 'unsupported')
    requested_chart_policy = chart_policy
    chart_policy = resolve_chart_policy(chart_policy, 'strict')
    require(revisions in (None, 'accepted', 'original', 'all'), {'stage': 'options'},
            'Unknown revisions policy', 'unsupported')
    if data.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        require(not binary_paragraph_sources, {'stage': 'options'},
                'Binary paragraph sources do not apply to OOXML', 'unsupported')
        require(compatibility in ('strict', 'data') and chart_policy == 'reject' and field_policy == 'reject' and object_policy == 'reject', {'stage': 'options'},
                'Binary compatibility/field/chart options do not apply to OOXML', 'unsupported')
        from wps_ooxml_sheet import package_kind, read_bytes as read_sheet_bytes
        try:
            family = package_kind(data)
        except (ValueError, KeyError, zipfile.BadZipFile, zlib.error) as exc:
            raise ParseError('invalid_or_unsupported', {'stage': 'OOXML package'}, str(exc)) from exc
        if family == 'spreadsheet':
            require(revisions is None and not include_related and hidden_text == 'include',
                    {'stage': 'options'}, 'Document options do not apply to spreadsheets', 'unsupported')
            return read_sheet_bytes(data)
        from wps_ooxml import read_bytes as read_ooxml_bytes
        return read_ooxml_bytes(data, revisions=revisions or 'accepted', include_related=include_related, hidden_text=hidden_text)
    require(data.startswith(SIGNATURE), {'stage': 'container'},
            'Unknown container; native private WPS/Works/ET formats unsupported', 'unsupported')
    require(hidden_text in ('include', 'annotate'), {'stage': 'options'}, 'Binary visibility is unknown; exclude unsupported', 'unsupported')
    compound = load_compound(data, compatibility)
    word = ('WordDocument',) in compound.streams
    workbooks = [path for path in compound.streams if len(path) == 1 and path[0].casefold() in ('workbook', 'book')]
    require(not (word and workbooks), {'stage': 'routing'},
            'Ambiguous document and workbook payloads; no branch chosen', 'unsupported')
    if word:
        require(compatibility in ('strict', 'legacy_word_clsid', 'data') and chart_policy == 'reject', {'stage': 'options'},
                'BIFF compatibility/chart options do not apply to MS-DOC', 'unsupported')
        require(revisions in (None, 'all'), {'stage': 'options'},
                'MS-DOC reads stored text; revision filtering unsupported', 'unsupported')
        result = parse_streams(compound.streams, field_policy=field_policy, object_policy=object_policy,
                               include_related=include_related, hidden_text=hidden_text,
                               binary_paragraph_sources=binary_paragraph_sources)
        result['compatibility_mode'] = compatibility
        result['container_compatibility'] = compound.compatibility
        result['container_issues'] = compound.issues
        if compound.issues:
            result['issues'] = result.get('issues', []) + compound.issues
            result['status'] = 'partial'
            result['partial'] = True
        return result
    require(not binary_paragraph_sources, {'stage': 'options'},
            'Binary paragraph sources only apply to MS-DOC', 'unsupported')
    require(len(workbooks) == 1, {'stage': 'routing'},
            'Require exactly one supported document or workbook payload', 'unsupported')
    require(revisions is None and not include_related and hidden_text == 'include' and field_policy == 'reject' and object_policy == 'reject', {'stage': 'options'},
            'Document revision/related-story options do not apply to BIFF', 'unsupported')
    from wps_biff8 import read_container
    try:
        result = read_container(compound, path=source, compatibility=compatibility,
                                chart_policy=resolve_chart_policy(requested_chart_policy, compatibility))
    except ValueError as exc:
        location = {'stage': 'BIFF', 'stream': workbooks[0][0]}
        location.update(getattr(exc, 'location', {}))
        raise ParseError(getattr(exc, 'status', 'invalid_or_unsupported'),
                         location, str(exc)) from exc
    from wps_biff8 import normalize_result
    return normalize_result(result)


def read_bytes(data, **options):
    """Read immutable bytes and add scope-limited confidence diagnostics."""
    from wps_diagnostics import summarize
    return summarize(_read_bytes(data, **options))


def read(path, **options):
    try:
        data = read_input(path)
        return read_bytes(data, source=str(path), **options)
    except (ParseError, OSError) as exc:
        from wps_diagnostics import summarize
        return summarize(failure(getattr(exc, 'status', 'io_error'),
                       getattr(exc, 'location', {'stage': 'input'}), str(exc)))


MAX_JSON_OUTPUT_BYTES = 16 * 1024 * 1024


def encode_result(result):
    # Retain the public limit and function used by existing callers/tests.
    from wps_result import encode_result as encode
    return encode(result, limit=MAX_JSON_OUTPUT_BYTES)


def check_environment():
    """Report import/runtime readiness; never read or accept a task document."""
    try:
        if sys.version_info < (3, 9):
            raise RuntimeError('Python >=3.9 required')
        import wps_biff8
        import wps_ooxml
        from xml.parsers import expat
    except (ImportError, OSError, RuntimeError) as exc:
        print('check_kind=environment artifact_validation=not_run', file=sys.stderr)
        print('environment-failed: ' + str(exc), file=sys.stderr, flush=True)
        return 2 if sys.version_info < (3, 9) else 1
    print('check_kind=environment artifact_validation=not_run')
    print('ooxml-word=' + ('available' if expat.version_info >= (2, 6, 0)
                          else 'unavailable: Expat >=2.6 required'))
    print('environment-ok', flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?')
    parser.add_argument('--format', choices=('json', 'text'), default='json')
    parser.add_argument('--compatibility', choices=('data', 'strict', 'legacy_excel_clsid', 'legacy_word_clsid'), default='data')
    parser.add_argument('--chart-policy', choices=('reject', 'opaque'), default=None)
    parser.add_argument('--object-policy', choices=('reject', 'placeholder'), default='reject')
    parser.add_argument('--field-policy', choices=('reject', 'cached_result'), default='reject')
    parser.add_argument('--revisions', choices=('accepted', 'original', 'all'))
    parser.add_argument('--include-related', action='store_true')
    parser.add_argument('--binary-paragraph-sources', action='store_true')
    parser.add_argument('--hidden-text', choices=('include','exclude','annotate'), default='include')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.include_related and args.format == 'text':
        parser.error('--include-related requires --format json to preserve separate story regions')
    if args.check:
        return check_environment()
    if sys.version_info < (3, 9):
        parser.error('Python >=3.9 required')
    if not args.input:
        parser.error('input required')
    result = read(args.input, compatibility=args.compatibility, chart_policy=args.chart_policy,
                  field_policy=args.field_policy, object_policy=args.object_policy, revisions=args.revisions,
                  include_related=args.include_related, hidden_text=args.hidden_text,
                  binary_paragraph_sources=args.binary_paragraph_sources)
    if args.format == 'text' and result['status'] in ('ok', 'partial') and 'text' not in result:
        result = failure('unsupported', {'stage': 'output'}, 'Spreadsheet extraction requires JSON output')
    if args.format == 'text' and result['status'] in ('ok', 'partial'):
        print(result['text'], end='')
        if result['status'] == 'partial':
            print(json.dumps({'status': 'partial', 'issues': result.get('issues', result.get('diagnostics', []))}, ensure_ascii=False), file=sys.stderr)
    else:
        try:
            encoded = encode_result(result)
        except ParseError as exc:
            result = failure(exc.status, exc.location, str(exc))
            encoded = json.dumps(result, ensure_ascii=False)
        print(encoded, file=sys.stderr if args.format == 'text' else sys.stdout)
    return 0 if result['status'] == 'ok' else 3 if result['status'] == 'partial' else 1


if __name__ == '__main__':
    sys.exit(main())
