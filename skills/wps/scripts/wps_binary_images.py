"""Associate bounded stored PNG records with MS-DOC picture markers."""

from base64 import b64encode
from bisect import bisect_left
from hashlib import sha256

from wps_access import ParseError, require
from wps_binary_officeart import inline_metro_blob, picture_store_entries
from wps_binary_png import inline_picture_shape, read_inline_png_preview, read_stored_png

PAPX_BTE_PAIR = 154 + 13 * 8
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_REFERENCES = 2000
MAX_IMAGE_SCAN_BYTES = 32 * 1024 * 1024
INLINE_MARKER_ISSUE = 'picture_or_data_marker_not_decoded'
RECOVERED_INLINE_ISSUE = 'inline_png_placeholder_not_rendered'


def _picture_markers(result):
    """Return only inline placeholders exposed by the existing text policy."""
    stories = [('main', result), *((story['type'], story) for story in result.get('related_stories', ()))]
    markers = []
    for kind, story in stories:
        for issue in story.get('issues', ()):
            if issue['code'] != INLINE_MARKER_ISSUE:
                continue
            for location in issue['locations']:
                cp = location['cp']
                if location['placeholder_emitted'] and (kind != 'main' or cp < result['main_cp_units']):
                    markers.append((kind, cp, location))
    return markers


def _picture_character_evidence(word, table, pieces, positions, fib_end, table_spans, word_spans, piece_prms, markers):
    """Read CHPX positions for single-character, unmodified pieces only."""
    from wps_msdoc_charprops import inspect_character_properties

    targets = {cp for _, cp, location in markers if not piece_prms[location['piece']]}
    papx_start = word.number(PAPX_BTE_PAIR)
    papx_size = word.number(PAPX_BTE_PAIR + 4)
    table_ranges = (*table_spans, (papx_start, papx_start + papx_size)) if papx_size else table_spans
    evidence = inspect_character_properties(word, table, pieces, positions, fib_end, table_ranges=table_ranges, picture_cps=targets)
    ordered = sorted((begin, end) for begin, end in word_spans)
    starts = [begin for begin, _ in ordered]
    furthest = []
    for _, end in ordered:
        furthest.append(max(end, furthest[-1] if furthest else 0))
    for begin, end in evidence.get('fkp_ranges', ()):
        index = bisect_left(starts, end) - 1
        require(index < 0 or furthest[index] <= begin, word.location(begin), 'Character FKP overlaps decoded WordDocument metadata')
    return evidence


def _overlap(start, end, ranges):
    return any(start < stop and begin < end for begin, stop in ranges)


def _word_metadata_spans(result, word, pieces, fib_end, cb_mac, header_mapping):
    spans = [(0, fib_end), (cb_mac, len(word.data))]
    spans.extend((begin, end) for begin, end, _, _ in pieces)
    if header_mapping:
        spans.extend((item['offset'], item['offset'] + item['size']) for item in header_mapping.get('word_ranges', ()))
    for story in (result, *result.get('related_stories', ())):
        for key in ('binary_table_structure', 'binary_paragraph_layout'):
            spans.extend(story.get(key, {}).get('fkp_ranges', ()))
    return spans


def _checked_image(read, source, issues):
    """Keep a usable text result when optional image records are unsupported."""
    try:
        return read()
    except ParseError as exc:
        if exc.status == 'resource_limit':
            raise
        issues.append({'code': 'binary_image_record_unread', 'source': source,
                       'record_location': exc.location, 'record_status': exc.status, 'message': str(exc)})
        return None


def _image_scan_budget(word):
    scanned = 0

    def reserve(size):
        nonlocal scanned
        scanned += size
        require(scanned <= MAX_IMAGE_SCAN_BYTES, word.location(), 'Binary image scan byte budget exceeded', 'resource_limit')
    return reserve


def _floating_images(region, word, word_spans, links, issues, reserve):
    if not links:
        return []
    entries = _checked_image(lambda: picture_store_entries(region, require), region.location(), issues)
    if entries is None:
        return []
    cache = {}
    output = []
    for story, cp, path, index, source in links:
        if index not in cache:
            def read(index=index, source=source):
                require(0 < index <= len(entries), source, 'Picture references a missing BStore entry')
                return read_stored_png(entries[index - 1], require, delay=word, delay_exclusions=word_spans, reserve=reserve)
            cache[index] = _checked_image(read, source, issues)
        image = cache[index]
        if image is not None:
            output.append((image, {'kind': 'floating', 'story': story, 'anchor_cp': cp, 'shape_id': path[-1],
                                   'anchor_shape_id': path[0], 'shape_path': list(path), 'source': source}))
    return output


def _inline_objects(data, data_spans, markers, offsets, issues, reserve):
    cache = {}
    images = []
    charts = []
    picture_sources = []
    if data is None:
        return images, charts, picture_sources
    for story, cp, location in markers:
        evidence = offsets.get(cp)
        if evidence is None:
            continue
        offset = evidence['data_offset']
        if offset not in cache:
            def read_picture(offset=offset):
                require(not _overlap(offset, offset + 4, data_spans), data.location(offset),
                        'Inline PICF overlaps external paragraph properties')
                size = data.number(offset)
                require(not _overlap(offset, offset + size, data_spans), data.location(offset),
                        'Inline PICF overlaps external paragraph properties')
                return inline_picture_shape(data, offset, require)
            picture = _checked_image(read_picture, location, issues)
            if picture is None:
                cache[offset] = (None, None)
            else:
                picture_sources.append(picture[0].location(size=len(picture[0].data)))
                cache[offset] = (_checked_image(lambda: read_inline_png_preview(*picture, require, reserve=reserve), location, issues),
                                 _inline_chart(*picture))
        image, chart = cache[offset]
        reference = {'kind': 'inline', 'story': story, 'anchor_cp': cp, 'source': location,
                     'character_properties_source': evidence['source']}
        if image is not None:
            images.append((image, reference))
        if chart is not None:
            shape_id, blob, picf_source = chart
            chart_ref = {**reference, 'picf_source': picf_source, 'shape_id': shape_id}
            charts.append((chart_ref, blob))
    return images, charts, picture_sources


def _inline_chart(picture, shape):
    """Keep the alternative XML link independent of the preview image."""
    try:
        chart = inline_metro_blob(shape, require)
    except ParseError as exc:
        if exc.status == 'resource_limit':
            raise
        # Preview recovery has historically treated the shape body as opaque.
        # Without a valid SpContainer we cannot attribute a property to it.
        return None
    if chart is None:
        return None
    return (*chart, picture.location(size=len(picture.data)))


def _extent(source):
    return source['stream'], source['offset'], source['offset'] + source['size']


def _exclude_overlapping_records(pairs, charts, picture_sources, issues):
    spans = {_extent(image['record_source']) for image, _ in pairs}
    spans.update(map(_extent, picture_sources))
    conflicts = set()
    previous = ('', 0, 0)
    for current in sorted(spans):
        stream, start, stop = current
        if previous[0] == stream and start < previous[2]:
            conflicts.update((previous, current))
            if stop <= previous[2]:
                continue
        previous = current
    if conflicts:
        issues.append({'code': 'binary_image_records_overlap', 'count': len(conflicts),
                       'message': 'Overlapping stored picture ranges and their linked content were not returned'})
    return ([(image, ref) for image, ref in pairs if _extent(image['record_source']) not in conflicts],
            [(ref, blob) for ref, blob in charts if _extent(ref['picf_source']) not in conflicts])


def _image_records(pairs, include_bytes):
    by_source = {}
    images = {}
    bytes_emitted = 0
    omitted = 0
    for image, reference in pairs:
        source = image['source']
        key = (source['stream'], source['offset'], source['size'])
        if key not in by_source:
            by_source[key] = sha256(image['payload']).hexdigest()
        digest = by_source[key]
        if digest not in images:
            item = images[digest] = {
                'sha256': digest, 'format': 'png', 'mime_type': 'image/png', 'byte_count': source['size'],
                'width': image['width'], 'height': image['height'], 'source': source, 'references': [],
                'bytes_status': 'not_requested',
            }
            if include_bytes:
                if bytes_emitted + source['size'] <= MAX_IMAGE_BYTES:
                    item.update(bytes_status='included', data_base64=b64encode(image['payload']).decode('ascii'))
                    bytes_emitted += source['size']
                else:
                    item['bytes_status'] = 'omitted_budget'
                    omitted += 1
        images[digest]['references'].append({**reference, 'record_source': image['record_source'], 'image_source': source})
    return list(images.values()), bytes_emitted, omitted


def _mark_recovered_placeholders(result, pairs):
    recovered = {ref['anchor_cp'] for _, ref in pairs if ref['kind'] == 'inline'}
    visited = set()
    for story in (result, *result.get('related_stories', ())):
        for issue in story.get('issues', ()):
            if id(issue) in visited or issue['code'] != INLINE_MARKER_ISSUE:
                continue
            visited.add(id(issue))
            count = 0
            for location in issue['locations']:
                if location['cp'] in recovered:
                    location['stored_image_status'] = 'png_available'
                    count += 1
            if not count:
                continue
            issue['recovered_png_count'] = count
            if count == len(issue['locations']):
                issue['code'] = RECOVERED_INLINE_ISSUE
                issue['message'] = 'Stored PNGs are listed in binary_images; U+0001 positions remain text placeholders.'
            else:
                issue['message'] = 'U+0001 positions remain placeholders; PNG availability is recorded per location.'


def add_binary_images(result, *, region, word, data, table, pieces, positions, fib_end, cb_mac,
                      header_mapping, table_spans, data_spans, links, piece_prms, include_bytes):
    """Publish PNG metadata and return independently framed inline chart links."""
    markers = _picture_markers(result)
    if not (links or markers):
        return []
    require(len(links) + len(markers) <= MAX_IMAGE_REFERENCES, word.location(),
            'Binary image reference budget exceeded', 'resource_limit')
    issues = []
    word_spans = _word_metadata_spans(result, word, pieces, fib_end, cb_mac, header_mapping)
    evidence = _checked_image(lambda: _picture_character_evidence(word, table, pieces, positions, fib_end, table_spans,
                              word_spans, piece_prms, markers), word.location(), issues)
    floating_available = evidence is not None
    evidence = evidence or {}
    offsets = evidence.get('stored_picture_offsets', {})
    word_spans.extend(evidence.get('fkp_ranges', ()))
    reserve = _image_scan_budget(word)
    pairs = _floating_images(region, word, word_spans, links, issues, reserve) if floating_available else []
    inline_images, charts, picture_sources = _inline_objects(data, data_spans, markers, offsets, issues, reserve)
    pairs.extend(inline_images)
    pairs, charts = _exclude_overlapping_records(pairs, charts, picture_sources, issues)
    images, bytes_emitted, omitted = _image_records(pairs, include_bytes)
    _mark_recovered_placeholders(result, pairs)
    result['binary_images'] = {
        'scope': 'stored_png_only', 'status': 'partial' if issues or len(pairs) != len(links) + len(markers) or omitted else 'extracted',
        'image_count': len(images), 'reference_count': len(pairs),
        'inline_marker_count': len(markers), 'floating_reference_count': len(links),
        'unresolved_reference_count': len(links) + len(markers) - len(pairs),
        'bytes_requested': include_bytes, 'bytes_emitted': bytes_emitted, 'images_omitted_by_byte_budget': omitted,
        'images': images,
    }
    if omitted:
        issues.append({'code': 'binary_image_payload_budget', 'count': omitted,
                       'message': 'PNG metadata retained; some image bytes exceeded the total byte budget'})
    result['issues'].extend(issues)
    return charts
