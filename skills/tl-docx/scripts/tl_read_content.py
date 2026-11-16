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
        if kind in STRUCTURAL_TYPES or (not details and kind == 'metadata') or id(item) in duplicate_paragraphs:
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
        if kind == 'image' and details:
            projected['notice'] = 'Image present; text inside the image has not been read by OCR.'
        if not details:
            projected = visible_record(projected)
        result.append(projected)
    return result


def diagnostic_summary(issues):
    """Bound repeated diagnostics without claiming their locations were retained."""
    counts = {}
    for issue in issues:
        code = issue.get('code', 'unspecified_diagnostic')
        counts[code] = counts.get(code, 0) + 1
    return [{'code': code, 'count': count, 'details_available': True} for code, count in counts.items()]


# Hide known implementation fields, not arbitrary unknown record content.
INTERNAL_KEYS = LAYOUT_KEYS | {
    'part', 'path', 'content_type', 'chart_part', 'resource_part', 'resource_sha256',
    'relationship_id', 'relationship_type', 'workbook_source', 'source_index',
    'category_reference', 'value_reference', 'x_reference', 'y_reference',
    'bubble_size_reference', 'category_cell', 'value_cell', 'x_cell', 'y_cell',
    'bubble_size_cell', 'name_formula', 'category_formula', 'value_formula',
    'x_formula', 'y_formula', 'bubble_size_formula', 'instruction',
}


def visible_record(item):
    """Keep stored visible values and meaningful context, without OPC internals."""
    kind = item.get('type')
    for key in INTERNAL_KEYS:
        item.pop(key, None)
    if kind == 'image':
        item.pop('target', None)  # The image's source is not visible image text.
    if kind == 'paragraph':
        if 'numbering' in item:
            number = item['numbering']
            item['numbering'] = {key: value for key, value in number.items()
                                 if key in ('label', 'level')}
        if 'fields' in item:
            # Field instructions are never visible prose. Keep only results not
            # already represented by this same source paragraph.
            results = [field.get('text', '') for field in item.pop('fields')
                       if field.get('text') and field['text'] not in item.get('text', '')]
            if results:
                item['field_results'] = results
    if item.get('story_kind') == 'main' and kind != 'story':
        item.pop('story_kind')
    if kind == 'table_row':
        item.pop('merged_groups', None)  # Already represented by cell origins/spans.
        for key in ('grid_before', 'grid_after'):
            if item.get(key) == 0:
                item.pop(key)
        if item.get('repeat_header') is False:
            item.pop('repeat_header')
        for cell in item.get('cells', []):
            if cell.get('colspan') == 1:
                cell.pop('colspan')
            if cell.get('vmerge') is None:
                cell.pop('vmerge', None)
            cell.pop('merge_anchor', None)  # The origin still links to its text.
    if kind == 'table' and isinstance(item.get('header'), dict):
        # Rows already contain every label. Preserve header certainty and range.
        for key in ('spans', 'columns'):
            item['header'].pop(key, None)
        item.pop('merged_groups', None)
        item.pop('header_candidate', None)  # The enriched header retains certainty.
    if kind == 'chart_series':
        summary = item.get('cache_summary', {})
        for key in ('description', 'diagnostics'):
            summary.pop(key, None)
        item.pop('category_levels', None)  # Each chart_point has its own levels.
    if kind == 'chart_point':
        for role, display in list(item.get('display', {}).items()):
            if display.get('status') in ('raw', 'missing') and display.get('text') == item.get(role):
                item['display'].pop(role)
            elif display.get('text') == item.get(role):
                display.pop('text', None)
        if not item.get('display'):
            item.pop('display', None)
    return visible_fields(item)


def visible_fields(value):
    """Strip known source fields recursively, including cells and revisions."""
    if isinstance(value, list):
        return [visible_fields(item) for item in value]
    if isinstance(value, dict):
        return {key: visible_fields(item) for key, item in value.items()
                if key not in INTERNAL_KEYS}
    return value


def content_coverage(coverage):
    """Keep coverage and omissions while avoiding repeated parser inventories."""
    result = deepcopy(coverage)
    for key in ('main_part', 'records_by_type'):
        result.pop(key, None)
    if 'availability' in result:
        result['availability'] = {key: value for key, value in result['availability'].items()
                                  if key not in ('styles', 'numbering', 'core_metadata')}
    return result
