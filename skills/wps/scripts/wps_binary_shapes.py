"""Bounded MS-DOC drawing anchors and their stored shape metadata.

The caller must have independently validated the CLX piece table. This module
links stored U+0008 anchors to PlcfSpa and, when requested, OfficeArt records.
Effective sprmCFSpec and drawn-object display remain unresolved.
"""
from collections import Counter
import struct

from wps_access import ParseError

MAX_SHAPES = 1000
PLCF_SPA_PAIR = 40
HEADER_SPA_PAIR = 41
DGG_INFO_PAIR = 50
SPA_BYTES = 26
DRAWING_LABELS = {'main': 0, 'headers_footers': 1}
TEXTBOX_DRAWINGS = {'textboxes': 0, 'header_textboxes': 1}


class AnchorMap(dict):
    """Verified anchors plus bounded diagnostics for excluded mismatches."""

    def __init__(self):
        super().__init__()
        self.issues = []


def shape_anchors(word, table, story_start, story_count, read_unit, table_ranges=(), *,
                  header=False, include_placement=False, recover_mismatched=False):
    """Return global anchor CPs and the corresponding source metadata."""
    from read_wps import require

    pair = 154 + 8 * (HEADER_SPA_PAIR if header else PLCF_SPA_PAIR)
    start = word.number(pair)
    size = word.number(pair + 4)
    if not size:
        return {}
    require(start > 0 and size >= 34 and (size - 4) % (SPA_BYTES + 4) == 0,
            word.location(pair), 'Invalid PlcfSpa layout')
    count = (size - 4) // (SPA_BYTES + 4)
    require(count <= MAX_SHAPES, word.location(pair + 4),
            'PlcfSpa shape budget exceeded', 'resource_limit')
    region = table.region(start, size)
    require(all(start + size <= left or right <= start for left, right in table_ranges),
            region.location(), 'PlcfSpa aliases another decoded Table Stream region')
    cps = struct.unpack('<' + 'I' * (count + 1), region.view(0, (count + 1) * 4))
    require(all(a < b for a, b in zip(cps, cps[1:])),
            region.location(), 'PlcfSpa CPs must be strictly increasing')
    # MS-DOC: the final CP is undefined and MUST be ignored, except it
    # must be greater than the previous CP. Only actual anchors are bounded.
    require(all(cp < story_count for cp in cps[:-1]), region.location(),
            'PlcfSpa shape anchor outside its declared story')
    results = AnchorMap()
    for index, relative_cp in enumerate(cps[:-1]):
        cp = story_start + relative_cp
        matches = read_unit(cp) == 8
        if not matches and recover_mismatched:
            results.issues.append({
                'code': 'binary_shape_anchor_unresolved',
                **region.location(4 * index, shape=index, cp=cp),
                'story': 'headers_footers' if header else 'main',
                'message': 'Stored drawing reference does not point to an object marker; '
                           'drawing association omitted and document text retained',
                'anchor_status': 'unverified',
            })
            continue
        require(matches, region.location(4 * index, shape=index, cp=cp),
                'PlcfSpa anchor does not point to U+0008')
        record = (count + 1) * 4 + index * SPA_BYTES
        anchor = {
            'source_stream': table.stream,
            'plcf_offset': start,
            'spa_record_offset': start + record,
            'shape_index': index,
        }
        if include_placement:
            shape_id, left, top, right, bottom, flags = struct.unpack('<I4iH', region.view(record, 22))
            if shape_id:
                anchor['shape_id'] = shape_id
            # These coordinates are relative to the stored origins.
            bx, by = (flags >> 1) & 3, (flags >> 3) & 3
            if left <= right and top <= bottom and bx < 3 and by < 3:
                anchor['stored_rectangle_twips'] = {
                    'left': left, 'top': top, 'right': right, 'bottom': bottom,
                    'horizontal_origin_code': bx, 'vertical_origin_code': by,
                }
        results[cp] = anchor
    return results


def drawing_metadata_region(word, table, table_ranges, require):
    """Reserve the selected OfficeArt span before examining optional metadata."""
    pair = 154 + 8 * DGG_INFO_PAIR
    start, size = word.number(pair), word.number(pair + 4)
    if not size:
        return None
    region = table.region(start, size)
    require(all(start + size <= left or right <= start for left, right in table_ranges),
            region.location(), 'OfficeArtContent aliases another decoded Table Stream region')
    table_ranges.append((start, start + size))
    return region


def _textbox_shapes(result):
    return {TEXTBOX_DRAWINGS[story['type']]: story['references']
            for story in result.get('related_stories', ()) if story['type'] in TEXTBOX_DRAWINGS}


def _link_textboxes(result, boxes, links, anchor_positions):
    for story in result.get('related_stories', ()):
        label = TEXTBOX_DRAWINGS.get(story['type'])
        linked = links.get(label, {}).get('textboxes', {})
        changed = False
        for reference in boxes.get(label, ()):
            relation = linked.get(reference['shape_id'])
            if relation is None:
                continue
            path, source = relation
            cp = anchor_positions[(label, path[0])]
            reference['officeart_source'] = source
            if 'anchor_cp' not in reference and len(path) > 1:
                reference.update(anchor_status='matched_officeart_group_shape_id',
                                 anchor_cp=cp, anchor_shape_id=path[0], group_shape_path=list(path[:-1]))
            changed = True
        if changed:
            story['anchor_status'] = 'stored_shape_id_matches'


def add_drawing_descriptions(result, region, anchors_by_story, require):
    """Link optional shape content to visible anchors outside field instructions."""
    from wps_binary_officeart import read_officeart

    stories = {'main': result}
    stories.update((story['type'], story) for story in result.get('related_stories', ()))
    visible = []
    wanted = {}
    blocked = {}
    anchor_positions = {}
    for kind, label in DRAWING_LABELS.items():
        anchors = anchors_by_story.get(kind, {})
        counts = {}
        for anchor in anchors.values():
            shape_id = anchor.get('shape_id')
            if shape_id:
                counts[shape_id] = counts.get(shape_id, 0) + 1
        for issue in stories.get(kind, {}).get('issues', ()):
            if issue['code'] != 'drawn_shape_anchor_not_rendered':
                continue
            for location in issue['locations']:
                # The top-level issue list also includes related issues.
                if location['placeholder_emitted'] and location['cp'] in anchors:
                    visible.append((kind, location))
                    shape_id = location.get('shape_id')
                    if counts.get(shape_id) == 1:
                        wanted.setdefault(label, set()).add(shape_id)
                        anchor_positions[(label, shape_id)] = location['cp']
        blocked[label] = set(counts).difference(wanted.get(label, ()))
    if not visible:
        return [], []

    metadata = {}
    links = {}
    boxes = _textbox_shapes(result)
    status = 'officeart_not_present'
    if region is not None:
        status = 'no_unique_shape_match'
        if wanted:
            try:
                textbox_ids = {}
                for label, records in boxes.items():
                    counts = Counter(record['shape_id'] for record in records)
                    textbox_ids[label] = {record['shape_id']: record['textbox_index'] for record in records
                                          if counts[record['shape_id']] == 1}
                metadata = read_officeart(region, wanted, require, textbox_ids_by_label=textbox_ids,
                                          object_links=links, blocked_by_label=blocked)
            except ParseError as exc:
                if exc.status == 'resource_limit':
                    raise
                result['issues'].append({'code': 'officeart_metadata_invalid', **exc.location, 'message': str(exc)})
                status = 'officeart_metadata_invalid'
                links.clear()

    entries = []
    for kind, location in visible:
        shape_id = location.get('shape_id')
        data = metadata.get((DRAWING_LABELS[kind], shape_id))
        entry = {'story': kind, 'anchor': {key: value for key, value in location.items()
                                         if key != 'placeholder_emitted'}}
        if data is not None:
            if data.get('is_group'):
                data.setdefault('shape_type', 'group')
            entry['officeart'] = data
        entries.append(entry)
    matched = sum('officeart' in entry for entry in entries)
    if matched:
        status = 'stored_metadata_partial'
    result['drawing_objects'] = {'status': status, 'anchor_count': len(entries),
                                 'officeart_matched_count': matched, 'objects': entries}
    _link_textboxes(result, boxes, links, anchor_positions)
    pictures = [(kind, anchor_positions[(label, path[0])], path, index, source)
                for kind, label in DRAWING_LABELS.items()
                for path, index, source in links.get(label, {}).get('pictures', ())]
    charts = [({'kind': 'floating', 'story': kind, 'anchor_cp': anchor_positions[(label, path[0])],
                'shape_id': path[-1], 'shape_path': list(path)}, blob)
              for kind, label in DRAWING_LABELS.items()
              for path, blob in links.get(label, {}).get('metro_blobs', ())]
    return pictures, charts
