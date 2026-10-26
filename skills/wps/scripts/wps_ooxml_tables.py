"""Bounded logical WordprocessingML table grid; never a page-layout engine."""
import re

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
MAX_TABLES = 1024
MAX_TABLE_DEPTH = 16
MAX_TABLE_CELLS = 100000
MAX_GRID_COLUMNS = 4096
MAX_TABLE_TEXT = 8 * 1024 * 1024


def tag(name):
    return '{' + W + '}' + name


class TableCollector:
    def __init__(self, revisions, paragraph_text):
        self.revisions = revisions
        self.paragraph_text = paragraph_text
        self.tables = []
        self.issues = []
        self.cells = 0
        self.text_units = 0
        self.blocked = {'accepted': {'del', 'moveFrom'}, 'original': {'ins', 'moveTo'}, 'all': set()}[revisions]

    def issue(self, code, part, path, message):
        if len(self.issues) >= 1000:
            raise ValueError('Table diagnostic count limit exceeded')
        self.issues.append({'code': code, 'stream': part, 'location': path, 'offset': None, 'message': message})

    def children(self, node, path):
        counts = {}
        for child in node:
            local = child.tag.rsplit('}', 1)[-1]
            counts[child.tag] = counts.get(child.tag, 0) + 1
            yield child, path + '/' + local + '[' + str(counts[child.tag]) + ']'

    def collect(self, node, part, path='/', parent_table=None, parent_cell=None, depth=0):
        for child, child_path in self.children(node, path.rstrip('/')):
            if not child.tag.startswith('{' + W + '}'):
                continue
            local = child.tag.rsplit('}', 1)[-1]
            if local in self.blocked or local == 'txbxContent':
                continue
            if local == 'tbl':
                self.table(child, part, child_path, parent_table, parent_cell, depth)
            elif local in ('body', 'sdt', 'sdtContent', 'customXml', 'ins', 'del', 'moveFrom', 'moveTo', 'smartTag', 'footnote', 'endnote', 'comment'):
                self.collect(child, part, child_path, parent_table, parent_cell, depth)

    def structural_children(self, node, path, wanted):
        for child, child_path in self.children(node, path):
            if child.tag == tag(wanted):
                yield child, child_path
            elif child.tag.startswith('{' + W + '}'):
                local = child.tag.rsplit('}', 1)[-1]
                if local in self.blocked:
                    continue
                if local in ('sdt', 'sdtContent', 'customXml', 'ins', 'del', 'moveFrom', 'moveTo'):
                    yield from self.structural_children(child, child_path, wanted)

    def integer(self, node, name, default, minimum=0):
        items = node.findall(tag(name)) if node is not None else []
        if len(items) > 1:
            raise ValueError('Duplicate table property ' + name)
        if not items:
            return default
        value = items[0].get(tag('val'))
        if value is None or not re.fullmatch(r'[0-9]{1,6}', value):
            raise ValueError('Invalid table integer ' + name)
        value = int(value)
        if value < minimum or value > MAX_GRID_COLUMNS:
            raise ValueError('Invalid/excessive table span ' + name)
        return value

    def paragraph(self, node, part, path):
        value = self.paragraph_text(node, self.revisions)
        self.text_units += len(value.encode('utf-16le')) // 2
        if self.text_units > MAX_TABLE_TEXT:
            raise ValueError('Table text limit exceeded')
        runs = []
        def walk(current, current_path):
            for child, child_path in self.children(current, current_path):
                if not child.tag.startswith('{' + W + '}'):
                    continue
                local = child.tag.rsplit('}', 1)[-1]
                if local in self.blocked or local in ('txbxContent', 'p', 'tbl'):
                    continue
                if local == 'r':
                    runs.append({'text': self.paragraph_text(child, self.revisions), 'source': {'part': part, 'path': child_path}})
                else:
                    walk(child, child_path)
        walk(node, path)
        return {'text': value, 'runs': runs, 'source': {'part': part, 'path': path}}

    def table(self, node, part, path, parent_table, parent_cell, depth):
        if depth >= MAX_TABLE_DEPTH or len(self.tables) >= MAX_TABLES:
            raise ValueError('Table count/depth limit exceeded')
        identity = 'table-' + str(len(self.tables) + 1)
        grids = node.findall(tag('tblGrid'))
        if len(grids) > 1:
            raise ValueError('Duplicate table grid')
        columns = len(grids[0].findall(tag('gridCol'))) if grids else None
        if columns is not None and columns > MAX_GRID_COLUMNS:
            raise ValueError('Table grid column limit exceeded')
        if columns == 0 and any(self.structural_children(node, path, 'tr')):
            columns = None
        first_issue = len(self.issues)
        if columns is None:
            self.issue('table_grid_unknown', part, path, 'No usable explicit grid; text retained without inferred column coordinates')
        table = {'id': identity, 'parent_table_id': parent_table, 'parent_cell_id': parent_cell,
                 'depth': depth, 'source': {'part': part, 'path': path}, 'grid_columns': columns,
                 'rows': [], 'status': 'ok', 'grid_complete': columns is not None}
        self.tables.append(table)
        active = {}
        rows = list(self.structural_children(node, path, 'tr'))
        if any(x.tag != tag('tr') and x.tag in (tag('sdt'), tag('customXml'), tag('ins'), tag('del'), tag('moveFrom'), tag('moveTo')) for x in node):
            self.issue('table_wrapped_content_legacy_text_omission', part, path, 'Wrapped rows/cells retained in structured tables; legacy text may omit wrapped content')
        for row_index, (row, row_path) in enumerate(rows):
            if len(row.findall(tag('trPr'))) > 1:
                raise ValueError('Duplicate table row properties')
            props = row.find(tag('trPr'))
            before = self.integer(props, 'gridBefore', 0)
            after = self.integer(props, 'gridAfter', 0)
            cursor = before
            output_row = {'index': row_index, 'grid_before': before, 'grid_after': after, 'source': {'part': part, 'path': row_path}, 'cells': []}
            next_active = {}
            if any(x.tag in (tag('sdt'), tag('customXml'), tag('ins'), tag('del'), tag('moveFrom'), tag('moveTo')) for x in row):
                self.issue('table_wrapped_content_legacy_text_omission', part, row_path, 'Wrapped cells retained in structured tables; legacy text may omit wrapped content')
            for cell_index, (cell, cell_path) in enumerate(self.structural_children(row, row_path, 'tc')):
                self.cells += 1
                if self.cells > MAX_TABLE_CELLS:
                    raise ValueError('Table cell limit exceeded')
                cell_id = identity + '-r' + str(row_index) + '-c' + str(cell_index)
                if len(cell.findall(tag('tcPr'))) > 1:
                    raise ValueError('Duplicate table cell properties')
                cell_props = cell.find(tag('tcPr'))
                span = self.integer(cell_props, 'gridSpan', 1, 1)
                legacy_merges = cell_props.findall(tag('hMerge')) if cell_props is not None else []
                if len(legacy_merges) > 1:
                    raise ValueError('Duplicate legacy horizontal merge property')
                legacy_merge = legacy_merges[0].get(tag('val'), 'continue') if legacy_merges else None
                if legacy_merges:
                    self.issue('table_legacy_horizontal_merge_not_decoded', part, cell_path, 'Legacy hMerge retained as raw value; gridSpan does not restore its merge semantics')
                merges = cell_props.findall(tag('vMerge')) if cell_props is not None else []
                if len(merges) > 1:
                    raise ValueError('Duplicate vertical merge property')
                merge = (merges[0].get(tag('val'), 'continue') if merges else None)
                if merge not in (None, 'restart', 'continue'):
                    raise ValueError('Invalid vertical merge value')
                if cursor + span > MAX_GRID_COLUMNS:
                    raise ValueError('Table logical column limit exceeded')
                if columns is not None and cursor + span > columns:
                    table['grid_complete'] = False
                    self.issue('table_grid_augmentation_not_applied', part, cell_path, 'Cell span extends declared grid; logical start/span retained, implicit grid augmentation not reconstructed')
                anchor = None
                if merge == 'continue':
                    if columns is None:
                        self.issue('table_merge_anchor_unknown', part, cell_path, 'Vertical merge cannot be anchored without an explicit grid')
                    else:
                        previous = [active.get(c) for c in range(cursor, cursor + span)]
                        if not previous or previous[0] is None or any(x != previous[0] for x in previous) or previous[0][1:] != (cursor, span):
                            raise ValueError('Non-contiguous or mismatched vertical merge')
                        anchor = previous[0][0]
                elif merge == 'restart':
                    anchor = cell_id
                if merge and columns is not None:
                    for c in range(cursor, cursor + span):
                        next_active[c] = (anchor, cursor, span)
                paragraphs = []
                blocks = []
                nested = []
                def cell_blocks(container, container_path):
                    for item, item_path in self.children(container, container_path):
                        local = item.tag.rsplit('}', 1)[-1]
                        if not item.tag.startswith('{' + W + '}') or local in self.blocked:
                            continue
                        if local == 'p':
                            para = self.paragraph(item, part, item_path)
                            paragraphs.append(para)
                            blocks.append({'kind': 'paragraph', 'paragraph_index': len(paragraphs)-1})
                        elif local == 'tbl':
                            nested_id = self.table(item, part, item_path, identity, cell_id, depth+1)
                            nested.append(nested_id)
                            blocks.append({'kind': 'table', 'table_id': nested_id})
                        elif local in ('sdt', 'sdtContent', 'customXml', 'ins', 'del', 'moveFrom', 'moveTo', 'smartTag'):
                            cell_blocks(item, item_path)
                cell_blocks(cell, cell_path)
                output_row['cells'].append({'id': cell_id, 'row': row_index, 'column': cursor if columns is not None else None,
                    'grid_span': span, 'legacy_h_merge_raw': legacy_merge, 'v_merge': merge, 'merge_anchor_id': anchor, 'source': {'part': part, 'path': cell_path},
                    'text': '\n'.join(x['text'] for x in paragraphs), 'paragraphs': paragraphs, 'blocks': blocks, 'nested_table_ids': nested})
                cursor += span
            if cursor + after > MAX_GRID_COLUMNS:
                raise ValueError('Table logical column limit exceeded')
            if columns is not None and cursor + after != columns:
                table['grid_complete'] = False
                self.issue('table_row_grid_incomplete', part, row_path, 'Row extent differs from declared grid; omitted cells are not synthesized')
            table['rows'].append(output_row)
            active = next_active
        table['status'] = 'partial' if len(self.issues) != first_issue else 'ok'
        return identity
