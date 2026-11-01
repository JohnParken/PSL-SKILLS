"""Read-only, bounded links between stored paragraph CPs and explicit MS-DOC evidence.

CHPX overlaps and PAPX row-end markers do not establish effective formatting,
page coordinates, or a complete table model.
"""
from bisect import bisect_right

MAX_LINKS = 100000


def link_evidence(paragraph_sources, *, character_runs=(), table_rows=(),
                  character_requested=False):
    """Use only observed half-open CP spans, preserving unknown distinctions."""
    paragraphs = []
    previous_end = 0
    for item in paragraph_sources:
        left, right = item['cp_start'], item['cp_end']
        if (type(left) is not int or type(right) is not int or
                left < previous_end or right <= left):
            raise ValueError('Ambiguous paragraph CP range')
        paragraphs.append((left, right, item['index']))
        previous_end = right
    result = {
        'schema_version': 'ms_doc_paragraph_evidence_v1',
        'effective_formatting': 'unknown', 'table_grid': 'unknown',
        'character_status': 'not_requested' if not character_requested else 'no_observed_runs',
        'papx_row_status': 'no_observed_row_ends',
        'character_links': [], 'papx_row_end_links': [],
    }
    if len(paragraphs) > MAX_LINKS:
        result['character_status'] = result['papx_row_status'] = 'resource_limited'
        return result
    if character_requested and character_runs:
        spans = []
        for index, item in enumerate(character_runs):
            left, right = item['cp_start'], item['cp_end']
            if type(left) is not int or type(right) is not int or left < 0 or right <= left:
                result['character_status'] = 'ambiguous_source_ranges'
                break
            spans.append((left, right, index))
        else:
            spans.sort()
            if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
                result['character_status'] = 'ambiguous_source_ranges'
            else:
                result['character_status'] = 'direct_chpx_overlaps_only'
                i = 0
                for left, right, paragraph_index in paragraphs:
                    while i < len(spans) and spans[i][1] <= left:
                        i += 1
                    j = i
                    while j < len(spans) and spans[j][0] < right:
                        run_left, run_right, run_index = spans[j]
                        begin, end = max(left, run_left), min(right, run_right)
                        if begin < end:
                            if len(result['character_links']) >= MAX_LINKS:
                                result['character_links'] = []
                                result['character_status'] = 'resource_limited'
                                break
                            result['character_links'].append({
                                'paragraph_index': paragraph_index, 'run_index': run_index,
                                'cp_start': begin, 'cp_end': end,
                            })
                        j += 1
                    if result['character_status'] == 'resource_limited':
                        break
    if table_rows:
        result['papx_row_status'] = 'direct_row_end_markers_only'
        ends = [right for _, right, _ in paragraphs]
        for row_index, row in enumerate(table_rows):
            marker = row.get('source', {}).get('row_mark_cp')
            if type(marker) is not int:
                result['papx_row_end_links'] = []
                result['papx_row_status'] = 'ambiguous_row_end_source'
                break
            i = bisect_right(ends, marker)
            if i < len(paragraphs) and paragraphs[i][0] <= marker < paragraphs[i][1]:
                if len(result['papx_row_end_links']) >= MAX_LINKS:
                    result['papx_row_end_links'] = []
                    result['papx_row_status'] = 'resource_limited'
                    break
                result['papx_row_end_links'].append({
                    'paragraph_index': paragraphs[i][2], 'row_index': row_index,
                    'row_mark_cp': marker,
                })
    return result
