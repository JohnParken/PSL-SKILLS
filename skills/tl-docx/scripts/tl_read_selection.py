"""Select report records and label selected versus whole-document coverage."""

from collections import Counter
import re

BODY_PARAGRAPH = re.compile(r'/w:document\[1\]/w:body\[1\]/w:p\[[0-9]+\]')


def body_paragraphs(records, main):
    """Return direct body paragraphs without conflating table or story paragraphs."""
    return [(index, item) for index, item in enumerate(records) if item['part'] == main
            and item['type'] == 'paragraph' and BODY_PARAGRAPH.fullmatch(item['path'])]


def select_body(records, main, args):
    """Select a paragraph range or all objects within the requested heading."""
    paragraphs = body_paragraphs(records, main)
    if args.paragraph_range:
        if not re.fullmatch(r'[1-9][0-9]{0,8}:[1-9][0-9]{0,8}', args.paragraph_range):
            raise ValueError('Paragraph range must be inclusive START:END with positive indexes')
        start, end = map(int, args.paragraph_range.split(':'))
        if not start <= end <= len(paragraphs):
            raise ValueError('Paragraph range exceeds the direct body paragraphs')
        return [item for _, item in paragraphs[start - 1:end]]
    headings = [(index, item) for index, item in paragraphs if item.get('heading_level') is not None]
    matches = [(index, item) for index, item in headings if item['text'].strip() == args.section.strip()]
    if args.section_occurrence is None and len(matches) != 1:
        raise ValueError('Section title must match exactly one direct body heading')
    occurrence = args.section_occurrence or 1
    if occurrence > len(matches):
        raise ValueError('Section occurrence exceeds the matching headings')
    start, heading = matches[occurrence - 1]
    end = next((index for index, item in headings if index > start and item['heading_level'] <= heading['heading_level']), len(records))
    end = min(end, next((index for index in range(start + 1, len(records))
                         if records[index]['type'] == 'story' and records[index].get('story_kind') != 'main'), len(records)))
    return [item for item in records[start:end] if item.get('story_kind', 'main') == 'main'
            and item['type'] not in ('story', 'metadata')]


def selected_issues(issues, records, main):
    """Keep located selected issues and conservatively retain unlocated diagnostics."""
    paths = {(item['part'], item['path']) for item in records}
    tables = {item['table_index'] for item in records if 'table_index' in item}
    charts = {item['chart_index'] for item in records if 'chart_index' in item}
    result = []
    for issue in issues:
        if 'chart_index' in issue:
            included = issue['chart_index'] in charts
        elif 'table_index' in issue:
            included = issue['table_index'] in tables
        elif issue.get('code') == 'alternate_content_branch_unavailable' and issue.get('part') == main:
            included = True  # Missing headings can change the selected section's boundary.
        elif not issue.get('path'):
            included = True  # No location is not evidence that the issue is unrelated.
        else:
            included = any(part == issue.get('part') and (
                path == issue['path'] or path.startswith(issue['path'] + '/') or issue['path'].startswith(path + '/')
            ) for part, path in paths)
        if included:
            result.append(issue)
    return result


def scoped_coverage(original, records, issues, scope):
    """Count the selected records, retaining original document coverage separately."""
    counts = dict(Counter(item['type'] for item in records))
    cells = [cell for item in records if item['type'] == 'table_row' for cell in item['cells']]
    images = [item for item in records if item['type'] == 'image']
    targets = {item['resource_part'] for item in images if item.get('resource_part')}
    return {
        'status': 'partial' if issues else 'ok', 'scope': scope, 'main_part': original.get('main_part'),
        'records_by_type': counts, 'counts': counts, 'document_complete': False,
        'tables': {'count': counts.get('table', 0), 'rows': counts.get('table_row', 0),
                   'physical_cells': len(cells),
                   'vertical_merge_origins': sum(cell.get('vmerge') == 'restart' for cell in cells),
                   'vertical_merge_continuations': sum(cell.get('vmerge') == 'continue' for cell in cells)},
        'charts': {'count': counts.get('chart', 0), 'series': counts.get('chart_series', 0),
                   'cached_numeric_values': sum(item.get('numeric_value_count', 0) for item in records if item['type'] == 'chart_series')},
        'images': {'references': len(images), 'unique_internal_targets': len(targets)},
        'textboxes': counts.get('textbox', 0),
    }
