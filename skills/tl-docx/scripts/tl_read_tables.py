"""Enrich report tables with explicit header and merged-cell coordinates."""

from collections import defaultdict
import re

from docx.oxml.ns import qn

MAX_COLUMNS = 256
MAX_HEADER_LABELS = 4096
PATH_STEP = re.compile(r'w:([A-Za-z][A-Za-z0-9]*)\[([1-9][0-9]*)\]')
SPAN_STEP = re.compile(r'([A-Za-z][A-Za-z0-9]*):([A-Za-z][A-Za-z0-9]*)\[([1-9][0-9]*)\]')
NAMESPACES = {'mc': 'http://schemas.openxmlformats.org/markup-compatibility/2006',
              'v': 'urn:schemas-microsoft-com:vml', 'wps': 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape',
              'wpg': 'http://schemas.microsoft.com/office/word/2010/wordprocessingGroup'}


def table_header_arg(value):
    """Parse a one-based table index and a zero-or-more header row count."""
    match = re.fullmatch(r'([1-9][0-9]{0,8}):([0-9]{1,9})', value)
    if match is None:
        raise ValueError('Table header override must have the form TABLE:ROWS')
    return tuple(map(int, match.groups()))


def resolve_path(root, location):
    """Resolve a Word row or table path in its original OOXML part."""
    steps = location.strip('/').split('/')
    # Some paths pass through compatibility wrappers or an XML drawing.
    current = root
    for depth, step in enumerate(steps):
        standard = PATH_STEP.fullmatch(step)
        if standard:
            tag, index = qn('w:' + standard[1]), int(standard[2])
        else:
            match = SPAN_STEP.fullmatch(step)
            if match is None:
                return None
            namespace = NAMESPACES.get(match[1])
            if namespace is None:
                try:
                    tag = qn(f'{match[1]}:{match[2]}')
                except KeyError:
                    return None
            else:
                tag = f'{{{namespace}}}{match[2]}'
            index = int(match[3])
        if depth == 0:
            if current.tag != tag or index != 1:
                return None
        else:
            current = next((node for ordinal, node in enumerate(current.iterchildren(tag), 1) if ordinal == index), None)
            if current is None:
                return None
    return current


def grid_value(row_node, name):
    """Read a nonnegative omitted-column count, preserving invalid metadata."""
    item = row_node.find(f'./{qn("w:trPr")}/{qn("w:" + name)}') if row_node is not None else None
    if item is None:
        return 0
    raw = item.get(qn('w:val'), '')
    return int(raw) if re.fullmatch(r'[0-9]{1,9}', raw) else None


def assign_geometry(package, table, rows):
    """Populate logical positions and return geometry issues for the table."""
    root = package.xml(table['part'])
    table_node = resolve_path(root, table['path'])
    grid = table_node.find(qn('w:tblGrid')) if table_node is not None else None
    declared_width = len(grid.findall(qn('w:gridCol'))) if grid is not None else 0
    width = declared_width
    issues = set()
    row_widths = set()
    for row in rows:
        node = resolve_path(root, row['path'])
        before, after = (grid_value(node, key) for key in ('gridBefore', 'gridAfter'))
        if node is None or before is None or after is None:
            issues.add('table_grid_offsets_unavailable')
        row.update(grid_before=before, grid_after=after)
        cells = row['cells']
        row_width = (cells[-1]['column'] + cells[-1]['colspan'] - 1 if cells else (before or 0)) + (after or 0)
        row_widths.add(row_width)
        width = max(width, row_width)
        if declared_width and row_width != declared_width:
            issues.add('table_row_grid_differs')
        if node is not None:
            for local, code in (('hMerge', 'legacy_horizontal_merge_unresolved'), ('cellMerge', 'table_merge_revision_unresolved')):
                for marker in node.iter(qn('w:' + local)):
                    closest_row = next(marker.iterancestors(qn('w:tr')), None)
                    if closest_row is node:
                        issues.add(code)
                        break
    if not declared_width:
        table['grid_inferred'] = True
        if len(row_widths) > 1:
            issues.add('table_row_grid_differs')
    table['column_count'] = width
    return issues


def vertical_groups(rows):
    """Resolve merge spans and preserve source labels without virtual cells."""
    groups = {}
    invalid = False
    for row in rows:
        for cell in row['cells']:
            origin = cell.get('origin')
            mode = cell.get('vmerge')
            if mode is None:
                continue
            key = (origin['row'], origin['column']) if origin else None
            if mode == 'restart' and key:
                groups[key] = {'row': key[0], 'column': key[1], 'column_span': cell['colspan'], 'row_span': 1}
                if cell['text'].strip() and len(cell['text']) <= 128:
                    groups[key]['label'] = cell['text']
                groups[key]['_cell'] = cell
            elif mode == 'continue' and key in groups:
                group = groups[key]
                if group['row'] + group['row_span'] != row['row_index'] or group['column_span'] != cell['colspan']:
                    invalid = True
                else:
                    group['row_span'] += 1
                    cell['merge_anchor'] = {field: group[field] for field in ('row', 'column', 'label') if field in group}
            else:
                invalid = True
    for group in groups.values():
        group.pop('_cell')['row_span'] = group['row_span']
    return list(groups.values()), invalid


def header_metadata(table, rows, override, safe):
    """Return the selected rows and both coordinate and column label paths."""
    repeated = table.get('repeat_header_rows', [])
    if override is not None:
        if override > len(rows):
            raise ValueError(f'Table {table["table_index"]} header row count exceeds its physical row count')
        source, count = 'explicit', override
    elif 0 < len(repeated) < len(rows) and repeated == list(range(1, len(repeated) + 1)):
        source, count = 'repeat_header', len(repeated)
    elif rows:
        source, count = 'first_row_candidate', 1
    else:
        source, count = 'empty', 0
    header = {'source': source, 'rows': list(range(1, count + 1)), 'spans': [], 'columns': []}
    if not safe:
        header['mapping'] = 'unavailable'
        return header
    spans = [{'row': row['row_index'], 'column': cell['column'], 'column_span': cell['colspan'], 'text': cell['text']}
             for row in rows[:count] for cell in row['cells'] if cell['text'].strip() and cell.get('vmerge') != 'continue']
    header['spans'] = spans
    if table['column_count'] > MAX_COLUMNS or sum(span['column_span'] for span in spans) > MAX_HEADER_LABELS:
        header['mapping'] = 'limit_exceeded'
        return header
    labels = defaultdict(list)
    for span in spans:
        for column in range(span['column'], span['column'] + span['column_span']):
            labels[column].append(span['text'])
    header['columns'] = [{'column': column, 'labels': labels[column]} for column in range(1, table['column_count'] + 1)]
    header['mapping'] = 'available'
    return header


def enrich_tables(package, records, overrides):
    """Add opt-in report metadata without changing the legacy default view."""
    tables = {record['table_index']: record for record in records if record['type'] == 'table'}
    if overrides.keys() - tables.keys():
        raise ValueError('A table-header override references a nonexistent table')
    grouped_rows = defaultdict(list)
    for record in records:
        if record['type'] == 'table_row':
            grouped_rows[record['table_index']].append(record)
    issues = []
    for index, table in tables.items():
        rows = grouped_rows[index]
        diagnostics = assign_geometry(package, table, rows)
        groups, unresolved = vertical_groups(rows)
        if unresolved:
            diagnostics.add('unresolved_vertical_merge')
        header = header_metadata(table, rows, overrides.get(index), not diagnostics)
        table['header'] = header
        merged = [group for group in groups if group['row'] not in header['rows'] or header['source'] == 'first_row_candidate']
        table['merged_groups'] = merged
        by_origin = {(group['row'], group['column']): group for group in merged}
        for row in rows:
            if row['row_index'] in header['rows'] and header['source'] != 'first_row_candidate':
                continue
            row['merged_groups'] = [by_origin[(cell['origin']['row'], cell['origin']['column'])] for cell in row['cells']
                                    if 'origin' in cell and (cell['origin']['row'], cell['origin']['column']) in by_origin]
        repeated = table.get('repeat_header_rows', [])
        if repeated and repeated != list(range(1, len(repeated) + 1)):
            diagnostics.add('noncontiguous_repeat_headers')
        if repeated and repeated == list(range(1, len(rows) + 1)):
            diagnostics.add('all_rows_marked_repeat_header')
        if header['mapping'] == 'limit_exceeded':
            diagnostics.add('table_header_mapping_limit_exceeded')
        issues.extend({'code': code, 'part': table['part'], 'path': table['path'], 'table_index': index} for code in sorted(diagnostics))
    return issues
