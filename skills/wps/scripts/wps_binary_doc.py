"""Bounded binary MS-DOC stream parsing and story extraction."""
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Optional

from wps_access import ParseError
from wps_binary_fib import (
    CLX_PAIR_INDEX,
    COMPRESSED_MAPPING,
    FC_LCB_START,
    FIB_LAYOUTS,
    MAX_CLX_BYTES,
    MAX_FIELD_DEPTH,
    MAX_FIELDS,
    MAX_OBJECT_MARKERS,
    MAX_PARAGRAPHS,
    MAX_PIECES,
    MAX_PRC_RECORDS,
    MAX_TEXTBOX_COPY_UNITS,
    MAX_UNITS,
    TEXTBOX_STORIES,
    FibHeader as _FibHeader,
    Region,
    parse_clx_pieces as _parse_clx_pieces,
    parse_fib_header as _parse_fib_header,
    require,
    runtime_limit as _runtime_limit,
    validate_stream_inputs as _validate_stream_inputs,
)
import wps_binary_tables
from wps_binary_images import BinaryImageOptions, add_binary_images
from wps_binary_recovery import read_mapping, read_story
from wps_binary_tables import TableScanOptions
from wps_paragraph_sources import paragraph_cp_ranges


@dataclass
class _DocScanState:
    fib: _FibHeader
    clx_offset: int
    clx_size: int
    pieces: list
    positions: tuple
    piece_properties: bool
    piece_prms: Any
    table_spans: list
    anchors_by_story: dict
    drawing_region: Any
    mapping_issues: dict
    header_mapping: Any
    note_mappings: dict
    automatic_note_markers: set
    comment_markers: set
    data_region: Any
    data_ownership: Optional[set]
    field_policy: str
    object_policy: str
    include_related: bool
    hidden_text: str
    binary_paragraph_sources: bool
    text_policy: str
    maximum: bool
    include_image_bytes: bool
    content_only: bool
    remaining_paragraphs: int
    remaining_fields: int
    remaining_objects: int
    remaining_controls: int
    hidden_shape_cps: set = field(default_factory=set)

    def text_location(self, cp):
        index = bisect_right(self.positions, cp) - 1
        file_offset, _, stride, _ = self.pieces[index]
        return self.fib.word.location(file_offset + (cp - self.positions[index]) * stride, piece=index, cp=cp)

    def read_unit(self, cp):
        index = bisect_right(self.positions, cp) - 1
        offset, _, stride, _ = self.pieces[index]
        return self.fib.word.number(offset + (cp - self.positions[index]) * stride, stride)


def _collect_mappings(state):
    fib = state.fib
    word, table = fib.word, fib.table
    table_spans = [(state.clx_offset, state.clx_offset + state.clx_size)]
    decoded_pairs = (2, 3, 4, 5, 6, 11, 36, 46, 47) if state.include_related else ()
    if state.maximum and state.include_related:
        decoded_pairs += tuple(pair for pair, _ in TEXTBOX_STORIES.values())
    if state.hidden_text == 'annotate':
        decoded_pairs += (12,)
    for pair in decoded_pairs:
        offset = word.number(FC_LCB_START + pair * 8)
        size = word.number(FC_LCB_START + pair * 8 + 4)
        if size:
            table.view(offset, size)
            require(all(offset + size <= left or right <= offset for left, right in table_spans),
                    table.location(offset), 'Overlapping decoded table/CLX ranges')
            table_spans.append((offset, offset + size))
    state.table_spans = table_spans
    if state.object_policy == 'placeholder':
        from wps_binary_shapes import HEADER_SPA_PAIR, PLCF_SPA_PAIR, drawing_metadata_region, shape_anchors
        shape_stories = [('main', 0, fib.main_count, PLCF_SPA_PAIR)]
        if state.maximum and state.include_related:
            shape_stories.append(('headers_footers', fib.main_count + fib.other_counts[0],
                                  fib.other_counts[1], HEADER_SPA_PAIR))
        for kind, start_cp, count, pair_index in shape_stories:
            anchors = shape_anchors(word, table, start_cp, count, state.read_unit, table_spans,
                                    header=kind == 'headers_footers',
                                    include_placement=state.maximum, recover_mismatched=state.maximum)
            state.anchors_by_story[kind] = anchors
            if anchors or getattr(anchors, 'issues', None):
                pair = FC_LCB_START + pair_index * 8
                start, size = word.number(pair), word.number(pair + 4)
                table_spans.append((start, start + size))
        if state.maximum and any(state.anchors_by_story.values()):
            state.drawing_region = drawing_metadata_region(word, table, table_spans, require)
    if state.include_related:
        from wps_binary_refs import note_references
        from wps_binary_comments import comment_references
        from wps_binary_headers import header_sections
        state.header_mapping = read_mapping(
            header_sections, word, table, fib.main_count + fib.other_counts[0],
            fib.other_counts[1], fib.main_count, state.read_unit, require,
            fib_end=fib.fib_end, cb_mac=fib.cb_mac,
            text_spans=[(p[0], p[1]) for p in state.pieces],
            maximum=state.maximum, story='headers_footers', issues=state.mapping_issues)
        story_start = fib.main_count
        story_names = ('footnotes', 'headers_footers', 'comments', 'endnotes', 'textboxes', 'header_textboxes')
        for story_type, count in zip(story_names, fib.other_counts):
            if story_type == 'comments':
                mapping = read_mapping(comment_references, word, table, story_start, count,
                                       fib.main_count, state.read_unit, require,
                                       maximum=state.maximum, story=story_type, issues=state.mapping_issues)
                for reference in mapping or []:
                    state.comment_markers.add(reference['anchor']['cp'])
                    state.comment_markers.add(reference['text_range']['cp_start'])
            else:
                mapping = read_mapping(note_references, word, table, story_type, story_start, count,
                                       fib.main_count, state.read_unit, require,
                                       maximum=state.maximum, story=story_type, issues=state.mapping_issues)
            state.note_mappings[story_type] = mapping
            for reference in mapping or []:
                if reference.get('numbering') == 'automatic':
                    state.automatic_note_markers.add(reference['anchor']['cp'])
                    start = reference['text_range']['cp_start']
                    if state.read_unit(start) == 2:
                        state.automatic_note_markers.add(start)
            story_start += count


def _decode_story_utf16(state, cp_start, cp_end, story_type):
    fib = state.fib
    body = bytearray()
    for file_offset, _, stride, index in state.pieces:
        begin = max(state.positions[index], cp_start)
        needed = max(0, min(state.positions[index + 1], cp_end) - begin)
        if not needed:
            continue
        piece = fib.word.view(file_offset + (begin - state.positions[index]) * stride, needed * stride)
        if stride == 1:
            body.extend(bytes(piece).decode('latin1').translate(COMPRESSED_MAPPING).encode('utf-16le'))
        else:
            body.extend(piece)
    require(len(body) == (cp_end - cp_start) * 2, fib.word.location(76), 'Decoded story unit count mismatch')
    try:
        text = body.decode('utf-16le')
    except UnicodeDecodeError as exc:
        raise ParseError('corrupt', state.text_location(cp_start + exc.start // 2), 'Invalid UTF-16 story text') from exc
    require(story_type != 'main' or not text or text.endswith('\r'),
            state.text_location(cp_end - 1) if cp_end > cp_start else fib.word.location(76),
            'Missing terminal paragraph')
    return text


def _scan_story_markers(state, text, cp_start, story_type):
    story_anchors = state.anchors_by_story.get(story_type, {})
    read_shapes = story_type in state.anchors_by_story
    max_field_depth = _runtime_limit('MAX_FIELD_DEPTH', MAX_FIELD_DEPTH)
    cp = cp_start
    fields = []
    field_count = 0
    field_spans = []
    hidden = 0
    object_locations = []
    shape_locations = []
    control_locations = []
    field_locations = []
    note_marker_locations = []
    needs_render = (
        (state.field_policy == 'cached_result' and any(char in text for char in '\x13\x14\x15'))
        or (state.object_policy == 'placeholder' and ('\x01' in text or (read_shapes and '\x08' in text)))
        or (bool(state.automatic_note_markers) and '\x02' in text)
        or (bool(state.comment_markers) and '\x05' in text)
        or (state.text_policy == 'recover' and any(ord(char) < 32 and char not in '\t\r\x07\x0b\x0c' for char in text))
    )
    rendered = [] if needs_render else None
    for char in text:
        if state.field_policy == 'cached_result' and char in '\x13\x14\x15':
            location = state.text_location(cp)
            if char == '\x13':
                require(len(fields) < max_field_depth and state.remaining_fields > 0,
                        location, 'Field nesting/count budget exceeded', 'resource_limit')
                fields.append((False, cp))
                if len(field_locations) < 20:
                    field_locations.append(location)
                hidden += 1
                field_count += 1
                state.remaining_fields -= 1
            elif char == '\x14':
                require(bool(fields) and not fields[-1][0], location,
                        'Field separator without start or repeated separator')
                fields[-1] = (True, fields[-1][1])
                hidden -= 1
            else:
                require(bool(fields), location, 'Field end without start')
                has_result, field_start = fields.pop()
                if not has_result:
                    hidden -= 1
                if state.maximum and not fields:
                    field_spans.append((field_start, cp + 1))
        elif char == '\x05' and cp in state.comment_markers:
            note_marker_locations.append(state.text_location(cp))
            if not hidden:
                rendered.append('\ufffc')
        elif char == '\x02' and cp in state.automatic_note_markers:
            note_marker_locations.append(state.text_location(cp))
            if not hidden:
                rendered.append('\ufffc')
        elif char == '\x01' and state.object_policy == 'placeholder':
            require(state.remaining_objects > 0, state.text_location(cp),
                    'Object marker diagnostic budget exceeded', 'resource_limit')
            state.remaining_objects -= 1
            object_locations.append({**state.text_location(cp), 'placeholder_emitted': not bool(hidden)})
            if not hidden:
                rendered.append('\ufffc')
        elif char == '\x08' and read_shapes:
            require(cp in story_anchors or state.text_policy == 'recover', state.text_location(cp),
                    'Drawn object marker lacks validated main-story PlcfSpa anchor', 'unsupported')
            require(state.remaining_objects > 0, state.text_location(cp),
                    'Drawn object marker budget exceeded', 'resource_limit')
            state.remaining_objects -= 1
            if cp in story_anchors:
                anchor = story_anchors[cp]
                if state.maximum and hidden:
                    state.hidden_shape_cps.add(cp)
                    anchor = {key: value for key, value in anchor.items()
                              if key not in ('shape_id', 'stored_rectangle_twips')}
                shape_locations.append({**state.text_location(cp), **anchor,
                                        'placeholder_emitted': not bool(hidden)})
            else:
                control_locations.append({**state.text_location(cp), 'marker': 'U+0008',
                                          'placeholder_emitted': not bool(hidden), 'anchor_status': 'unverified'})
            if not hidden:
                rendered.append('\ufffc')
        else:
            if ord(char) < 32 and char not in '\t\r\x07\x0b\x0c':
                require(state.text_policy == 'recover' and char not in '\x13\x14\x15\x01\x08',
                        state.text_location(cp),
                        'Fields/objects/C0 control codes outside plain-text subset', 'unsupported')
                require(state.remaining_controls > 0, state.text_location(cp),
                        'Control marker budget exceeded', 'resource_limit')
                state.remaining_controls -= 1
                control_locations.append({**state.text_location(cp), 'marker': 'U+{:04X}'.format(ord(char)),
                                          'placeholder_emitted': not bool(hidden)})
                if not hidden:
                    rendered.append('\ufffc')
                cp += 1
                continue
            if rendered is not None and not hidden:
                rendered.append(char)
        cp += 2 if ord(char) > 0xFFFF else 1
    require(not fields, state.text_location(fields[-1][1]) if fields else state.fib.word.location(76),
            'Unclosed field in story')
    out_text = ''.join(rendered) if rendered is not None else text
    return (out_text, story_anchors, field_count, field_spans,
            object_locations, shape_locations, control_locations,
            field_locations, note_marker_locations)


def _render_story(state, cp_start, cp_end, story_type):
    fib = state.fib
    stored_story_text = _decode_story_utf16(state, cp_start, cp_end, story_type)
    (text, story_anchors, field_count, field_spans, object_locations, shape_locations,
     control_locations, field_locations, note_marker_locations) = _scan_story_markers(
         state, stored_story_text, cp_start, story_type)
    issues = list(getattr(story_anchors, 'issues', ()))
    if fib.missing_extension and story_type == 'main':
        issues.append({'code': 'fib_extension_absent_compatibility', 'stream': 'WordDocument',
                       'offset': fib.extension_offset, 'base_version': hex(fib.base_version),
                       'pair_count': fib.pair_count, 'csw_new': 0, 'expected_csw_new': fib.expected_new,
                       'message': 'Explicitly absent extension: known base version and exact array retained; '
                                  'noncanonical producer layout, extension metadata unavailable'})
    if control_locations:
        issues.append({'code': 'uninterpreted_control_markers_not_decoded', 'count': len(control_locations),
                       'locations': control_locations,
                       'message': 'Stored marker code units replaced by U+FFFC; marker semantics and possible linked content unknown'})
    if note_marker_locations:
        issues.append({'code': 'automatic_note_marker_not_rendered', 'locations': note_marker_locations,
                       'message': 'Validated annotation/note marker CP replaced by U+FFFC; numbering and character properties unverified'})
    if field_count:
        issues.append({'code': 'stored_field_results_unverified', 'stream': 'WordDocument',
                       'field_count': field_count, 'locations': field_locations,
                       'locations_truncated': max(0, field_count - len(field_locations)),
                       'message': 'Field instructions omitted; cached results retained without execution, '
                                  'freshness checks or sprmCFSpec/Plcfld validation'})
    if shape_locations:
        shape_message = ('Validated PlcfSpa CP and U+0008 anchor; sprmCFSpec and shape rendering unverified'
                         if state.maximum else
                         'Validated main-story PlcfSpa CP and U+0008 anchor replaced '
                         'by U+FFFC; sprmCFSpec, SPA properties and shape rendering unverified')
        issues.append({'code': 'drawn_shape_anchor_not_rendered',
                       'count': len(shape_locations), 'locations': shape_locations,
                       'message': shape_message})
    if object_locations:
        issues.append({'code': 'picture_or_data_marker_not_decoded',
                       'count': len(object_locations), 'locations': object_locations,
                       'message': 'U+0001 replaced by U+FFFC outside hidden field instructions; '
                                  'picture/data bytes, sprmCFSpec and object type not decoded'})
    table_structure = None
    metadata_text = stored_story_text if field_spans else text
    has_table_marks = '\x07' in metadata_text
    has_layout = (state.maximum and not state.content_only
                  and bool(fib.word.number(FC_LCB_START + 13 * 8 + 4)) and '\r' in metadata_text)
    if (has_table_marks or has_layout) and (not field_count or state.maximum):
        marker_replacements = None
        if field_spans:
            marker_replacements = {item['cp']: '\ufffc' for item in note_marker_locations}
            for locations in (shape_locations, object_locations, control_locations):
                marker_replacements.update({item['cp']: '\ufffc' for item in locations if item['placeholder_emitted']})
        word_ranges = ([(item['offset'], item['offset'] + item['size'])
                        for item in state.header_mapping.get('word_ranges', [])]
                       if state.header_mapping else ())
        scan_opts = TableScanOptions(
            table_ranges=state.table_spans, word_ranges=word_ranges,
            piece_properties=state.piece_properties, piece_prms=state.piece_prms,
            field_spans=field_spans, marker_replacements=marker_replacements,
            data_region=state.data_region, data_ownership=state.data_ownership,
            include_layout=not state.content_only,
        )
        table_structure = wps_binary_tables.inspect_tables(
            metadata_text, cp_start, fib.word, fib.table, state.pieces,
            state.positions, fib.fib_end, scan_opts,
        )
        issues.extend(table_structure['issues'])
    elif has_table_marks:
        issues.append({'code': 'binary_table_fields_unresolved', 'stream': 'WordDocument',
                       'cp_start': cp_start, 'cp_end': cp_end})
    paragraph_total = text.count('\r') + int(bool(text) and not text.endswith('\r'))
    require(paragraph_total <= state.remaining_paragraphs, fib.word.location(76),
            'Paragraph budget exceeded', 'resource_limit')
    state.remaining_paragraphs -= paragraph_total
    paragraphs = (text[:-1] if text.endswith('\r') else text).split('\r') if text else []

    def normalize(value):
        return value.replace('\x0b', '\n').replace('\x07', '\t')

    story_result = {
        'status': 'partial' if issues else 'ok', 'format': 'ms_doc_subset',
        'fib_version': hex(fib.version), 'fib_base_version': hex(fib.base_version),
        'main_cp_units': fib.main_count, 'text': normalize(text).replace('\r', '\n'),
        'paragraphs': [normalize(value) for value in paragraphs], 'partial': bool(issues),
        'field_policy': state.field_policy, 'fields_evaluated': False, 'issues': issues,
        'text_policy': state.text_policy, 'object_policy': state.object_policy, 'objects_decoded': False,
        'scope': 'stored_main_story_only', 'producer_verified': False,
    }
    if state.maximum and table_structure is not None and not state.content_only:
        story_result['binary_paragraph_layout'] = table_structure.pop('paragraph_layout')
        if not has_table_marks:
            story_result['binary_paragraph_layout']['fkp_ranges'] = table_structure['fkp_ranges']
    if table_structure is not None and has_table_marks:
        story_result['binary_table_structure'] = table_structure
    if state.binary_paragraph_sources:
        if text == stored_story_text:
            sources = paragraph_cp_ranges(text, cp_start)
            require(len(sources) == len(paragraphs), fib.word.location(76),
                    'Stored paragraph source count mismatch')
            require((sources[-1]['cp_end'] if sources else cp_start) == cp_end,
                    fib.word.location(76), 'Stored paragraph CP coverage mismatch')
            story_result['paragraph_sources'] = sources
            story_result['paragraph_source_status'] = 'exact_stored_cp'
        else:
            story_result['paragraph_sources'] = []
            story_result['paragraph_source_status'] = 'unavailable_after_text_transform'
            story_result['issues'].append({
                'code': 'binary_paragraph_source_alignment_unavailable',
                'stream': 'WordDocument', 'cp_start': cp_start, 'cp_end': cp_end,
                'message': 'Stored CP ranges cannot be aligned with transformed text; no ranges emitted',
            })
            story_result['partial'] = True
            story_result['status'] = 'partial'
    return story_result


def _attach_related_stories(state, result):
    fib = state.fib
    max_paragraphs = _runtime_limit('MAX_PARAGRAPHS', MAX_PARAGRAPHS)
    max_copy_units = _runtime_limit('MAX_TEXTBOX_COPY_UNITS', MAX_TEXTBOX_COPY_UNITS)
    story_names = ('footnotes', 'headers_footers', 'comments', 'endnotes', 'textboxes', 'header_textboxes')
    if not state.include_related:
        omitted = []
        story_start = fib.main_count
        for story_type, count in zip(story_names, fib.other_counts):
            if count:
                omitted.append({
                    'type': story_type, 'cp_units': count,
                    'source': {'stream': 'WordDocument', 'cp_start': story_start, 'cp_end': story_start + count},
                    'reason': 'include_related_not_requested',
                })
            story_start += count
        result['omitted_regions'] = omitted
        if omitted:
            result['issues'].append({
                'code': 'related_content_not_requested', 'stream': 'WordDocument',
                'region_count': len(omitted),
                'message': 'Nonempty related stories were not read; use include-related to request them',
            })
            result['status'], result['partial'] = 'partial', True
        return

    related = []
    cp_start = fib.main_count
    paragraph_count = len(result['paragraphs'])
    for story_type, count_offset, count in zip(story_names, (80, 84, 92, 96, 100, 104), fib.other_counts):
        cp_end = cp_start + count
        story = read_story(lambda s, e, k: _render_story(state, s, e, k),
                           cp_start, cp_end, story_type, maximum=state.maximum)
        paragraph_count += len(story['paragraphs'])
        require(paragraph_count <= max_paragraphs, fib.word.location(count_offset),
                'Combined story paragraph budget exceeded', 'resource_limit')
        story_issues = story['issues'] + state.mapping_issues.get(story_type, [])
        references = state.note_mappings.get(story_type)
        if count and references is None:
            story_issues = story_issues + [{
                'code': 'story_references_not_decoded', 'stream': 'WordDocument',
                'cp_start': cp_start, 'cp_end': cp_end,
                'message': 'Stored subdocument text only; individual note/header/textbox ranges, anchors and page mapping are unknown',
            }]
        if references is not None:
            story_issues = story_issues + [{
                'code': 'note_reference_properties_unverified', 'stream': fib.table_name,
                'message': 'Note CP mapping verified; character properties, numbering display and page mapping are not interpreted',
            }]
        region = {
            'type': story_type, 'empty': count == 0,
            'source': {'stream': 'WordDocument', 'cp_start': cp_start, 'cp_end': cp_end, 'count_offset': count_offset},
            'text': story['text'], 'paragraphs': story['paragraphs'],
            'references': references if references is not None else [],
            'anchor_status': ('cp_mapped_properties_unverified' if references is not None
                              else ('unknown' if count else 'not_applicable')),
            'status': 'partial' if story_issues else 'ok', 'issues': story_issues,
        }
        for key in ('binary_table_structure', 'binary_paragraph_layout'):
            if key in story:
                region[key] = story[key]
        if state.maximum and count and story_type in TEXTBOX_STORIES:
            pair_index, anchor_story = TEXTBOX_STORIES[story_type]
            pair = FC_LCB_START + pair_index * 8
            size = fib.word.number(pair + 4)
            if size:
                plc = fib.table.region(fib.word.number(pair), size)
                anchors = {cp: anchor for cp, anchor in state.anchors_by_story.get(anchor_story, {}).items()
                           if 'shape_id' in anchor}
                from wps_binary_textboxes import attach_textbox_references
                attach_textbox_references(region, plc, count, state.read_unit, anchors,
                                          state.hidden_shape_cps, copy_limit=max_copy_units)
                story_issues = region['issues']
        if state.binary_paragraph_sources:
            region['paragraph_sources'] = story['paragraph_sources']
            region['paragraph_source_status'] = story['paragraph_source_status']
        if story_type == 'headers_footers' and state.header_mapping is not None:
            region['section_mapping'] = state.header_mapping
            header_issue = {
                'code': 'section_header_properties_uninterpreted', 'stream': fib.table_name,
                'message': 'Stored section/slot ranges only; SEPX semantics and effective page headers are unknown',
            }
            region['issues'] = region['issues'] + [header_issue]
            story_issues = story_issues + [header_issue]
            region['status'] = 'partial'
            region['anchor_status'] = state.header_mapping['mapping_status']
        related.append(region)
        result['issues'].extend(story_issues)
        cp_start = cp_end
    result['related_stories'] = related
    result['scope'] = 'stored_main_and_related_subdocuments'
    result['partial'] = bool(result['issues'])
    result['status'] = 'partial' if result['partial'] else 'ok'


def _annotate_character_props(state, result):
    fib = state.fib
    from wps_msdoc_charprops import inspect_character_properties
    bte_pair_offset = FC_LCB_START + 12 * 8
    bte_start = fib.word.number(bte_pair_offset)
    bte_span = (bte_start, bte_start + fib.word.number(bte_pair_offset + 4))
    cp_limit = fib.main_count + sum(fib.other_counts) if state.include_related else fib.main_count
    result['character_properties'] = inspect_character_properties(
        fib.word, fib.table, state.pieces, state.positions, fib.fib_end,
        table_ranges=tuple(span for span in state.table_spans if span != bte_span),
        cp_limit=cp_limit,
    )
    if state.header_mapping is not None:
        for section_range in state.header_mapping.get('word_ranges', []):
            start = section_range['offset']
            stop = start + section_range['size']
            for left, right in result['character_properties'].get('fkp_ranges', []):
                require(stop <= left or right <= start, fib.word.location(start),
                        'Section properties overlap character FKP page')
    stories = (result, *result.get('related_stories', ()))
    structures = [story.get(key, {}) for story in stories
                  for key in ('binary_table_structure', 'binary_paragraph_layout')]
    for structure in structures:
        for left, right in structure.get('fkp_ranges', []):
            for char_left, char_right in result['character_properties'].get('fkp_ranges', []):
                require(right <= char_left or char_right <= left, fib.word.location(left),
                        'Paragraph FKP overlaps character FKP')
    result['character_properties']['annotation_scope'] = (
        'selected_stories' if state.include_related else 'declared_main'
    )
    result['issues'].append({
        'code': 'binary_character_properties_not_effective', 'stream': fib.table_name,
        'message': 'CHPX evidence only; styles, piece PRMs and effective visibility/revisions unresolved. No text filtered.',
    })
    result['partial'] = True
    result['status'] = 'partial'


def _attach_paragraph_evidence(state, result):
    from wps_paragraph_evidence import link_evidence
    for story in [result, *result.get('related_stories', ())]:
        if story.get('paragraph_source_status') != 'exact_stored_cp':
            continue
        char_runs = result.get('character_properties', {}).get('runs', ())
        table_rows = story.get('binary_table_structure', {}).get('rows', ())
        try:
            story['paragraph_evidence'] = link_evidence(
                story['paragraph_sources'], character_runs=char_runs,
                table_rows=table_rows, character_requested=state.hidden_text == 'annotate',
            )
        except (KeyError, TypeError, ValueError):
            issue = {
                'code': 'binary_paragraph_evidence_unresolved', 'stream': 'WordDocument',
                'message': 'Paragraph-to-CHPX/PAPX association is ambiguous; no links claimed',
            }
            if story is not result:
                story['issues'].append(issue)
                story['partial'] = True
                story['status'] = 'partial'
            result['issues'].append(issue)
            result['partial'] = True
            result['status'] = 'partial'


def parse_streams(streams, *, field_policy='reject', object_policy='reject', include_related=False,
                  hidden_text='include', binary_paragraph_sources=False, text_policy='strict',
                  maximum=False, include_image_bytes=False, content_only=False):
    """Parse validated CFB streams of a binary MS-DOC file into bounded story records."""
    _validate_stream_inputs(
        streams, text_policy=text_policy, field_policy=field_policy,
        object_policy=object_policy, hidden_text=hidden_text,
        binary_paragraph_sources=binary_paragraph_sources, content_only=content_only,
        maximum=maximum, include_image_bytes=include_image_bytes,
    )
    region_cls = _runtime_limit('Region', Region)
    fib = _parse_fib_header(streams, region_cls, maximum)
    clx_offset, clx_size, pieces, positions, piece_properties, piece_prms = _parse_clx_pieces(fib, maximum)
    max_objects = _runtime_limit('MAX_OBJECT_MARKERS', MAX_OBJECT_MARKERS)
    state = _DocScanState(
        fib=fib, clx_offset=clx_offset, clx_size=clx_size, pieces=pieces, positions=positions,
        piece_properties=piece_properties, piece_prms=piece_prms, table_spans=[],
        anchors_by_story={}, drawing_region=None, mapping_issues={}, header_mapping=None,
        note_mappings={}, automatic_note_markers=set(), comment_markers=set(),
        data_region=region_cls(streams[('Data',)], 'Data') if ('Data',) in streams else None,
        data_ownership=set() if maximum else None,
        field_policy=field_policy, object_policy=object_policy, include_related=include_related,
        hidden_text=hidden_text, binary_paragraph_sources=binary_paragraph_sources,
        text_policy=text_policy, maximum=maximum, include_image_bytes=include_image_bytes,
        content_only=content_only,
        remaining_paragraphs=_runtime_limit('MAX_PARAGRAPHS', MAX_PARAGRAPHS),
        remaining_fields=_runtime_limit('MAX_FIELDS', MAX_FIELDS),
        remaining_objects=max_objects, remaining_controls=max_objects,
    )
    _collect_mappings(state)
    result = _render_story(state, 0, fib.main_count, 'main')
    _attach_related_stories(state, result)
    if maximum:
        from wps_binary_shapes import add_drawing_descriptions
        from wps_binary_charts import add_binary_charts
        picture_links, chart_links = add_drawing_descriptions(
            result, state.drawing_region, state.anchors_by_story, require, content_only=content_only)
        inline_charts = add_binary_images(result, BinaryImageOptions(
            region=state.drawing_region, word=fib.word, data=state.data_region, table=fib.table,
            pieces=pieces, positions=positions, fib_end=fib.fib_end, cb_mac=fib.cb_mac,
            header_mapping=state.header_mapping, table_spans=state.table_spans,
            data_spans=state.data_ownership, links=picture_links, piece_prms=piece_prms,
            include_bytes=include_image_bytes, charts_only=content_only,
        ))
        add_binary_charts(result, chart_links + inline_charts)
    result['visibility_status'] = 'visibility_unknown'
    if hidden_text == 'annotate':
        _annotate_character_props(state, result)
    if binary_paragraph_sources:
        _attach_paragraph_evidence(state, result)
    return result
