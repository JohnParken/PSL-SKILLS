"""Content-first projection of complete records; no new document parsing."""
from copy import deepcopy

STRUCTURAL_TYPES = {'style', 'style_definition', 'numbering_definition', 'section'}
LAYOUT_KEYS = {'style_id', 'style_name', 'style_type', 'style_based_on', 'outline_level',
               'paragraph_count', 'text_format', 'relationship_type', 'relationship_id', 'anchor'}


def content_records(records, *, details=False):
    """Remove proven duplicate representations while preserving unknown records.

    Identity is a source part/path or a specific table cell, never text equality
    across unrelated locations. All projection happens before pagination.
    """
    records = deepcopy(records)
    containers = {(item.get('part'), item.get('path')): item for item in records if item['type'] in ('paragraph', 'textbox')}
    cells = {}
    paragraphs = {}
    for item in records:
        if item['type'] == 'table_row':
            for cell in item.get('cells', []):
                key = (item.get('part'), item.get('table_index'), item.get('row_index'), cell.get('column'))
                cells[key] = cell
        elif item['type'] == 'paragraph' and 'table_index' in item:
            key = (item.get('part'), item.get('table_index'), item.get('row_index'), item.get('column'))
            paragraphs.setdefault(key, []).append(item)
    nested_cells = {(item.get('part'), item.get('parent_table_index'), item.get('parent_row'), item.get('parent_column'))
                    for item in records if item['type'] == 'table' and 'parent_table_index' in item}
    for key in nested_cells:
        direct = paragraphs.get(key, [])
        if key in cells and direct:
            prefix = direct[0].get('path', '').rsplit('/', 1)[0] + '/'
            descendants = [item.get('text', '') for item in records if item['type'] == 'paragraph'
                           and item.get('part') == key[0] and item.get('path', '').startswith(prefix)]
            if cells[key].get('text', '') == '\n'.join(descendants):
                cells[key]['text'] = ''
                cells[key]['content_in_following_records'] = True
    duplicate_paragraphs = set()
    for key, values in paragraphs.items():
        cell = cells.get(key)
        if cell is None or cell.get('content_in_following_records'):
            continue
        texts = [item.get('text', '') for item in values]
        if cell.get('text', '') in (''.join(texts), '\n'.join(texts)):
            # Revision details or field provenance need to survive in the cell.
            duplicate_paragraphs.update(id(item) for item in values if not any(
                item.get(field) for field in ('revisions', 'fields', 'deleted_text', 'numbering')))
    result = []
    for item in records:
        kind = item['type']
        if kind in STRUCTURAL_TYPES or id(item) in duplicate_paragraphs:
            continue
        duplicate = False
        ancestor = item.get('path', '').rsplit('/', 1)[0]
        while kind in ('paragraph', 'math') and ancestor:
            parent = containers.get((item.get('part'), ancestor))
            if (parent is not None and item.get('text', '') in parent.get('text', '')
                    and (kind == 'math' or parent['type'] == 'textbox' and 'table_index' not in item)
                    and not any(item.get(field) for field in ('revisions', 'fields', 'deleted_text', 'numbering'))):
                duplicate = True
                break
            ancestor = ancestor.rsplit('/', 1)[0] if '/' in ancestor else ''
        if duplicate:
            continue
        projected = deepcopy(item)
        if kind == 'image':
            projected['notice'] = 'Image present; text inside the image has not been read by OCR.'
        if not details:
            for key in LAYOUT_KEYS | {'part', 'path'}:
                projected.pop(key, None)
            for cell in projected.get('cells', []):
                cell.pop('part', None)
                cell.pop('path', None)
        result.append(projected)
    return result


def diagnostic_summary(issues):
    """Bound repeated diagnostics without claiming their locations were retained."""
    counts = {}
    for issue in issues:
        code = issue.get('code', 'unspecified_diagnostic')
        counts[code] = counts.get(code, 0) + 1
    return [{'code': code, 'count': count, 'details_available': True} for code, count in counts.items()]
