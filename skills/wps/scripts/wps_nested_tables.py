"""Conservative MS-DOC nested row fragments from verified direct PAPX marks."""
MAX_DEPTH = 32
MAX_CELLS = 100000
MAX_EVENTS = 100000


class NestedRows:
    """Keep hierarchy separate from legacy flat rows; never certify layout."""

    def __init__(self):
        self.stack = []
        self.rows = []
        self.issues = []
        self.issue_count = 0
        self.cells = 0
        self.events = 0

    def reject(self, cp, reason):
        self.issue_count += 1
        if len(self.issues) < 100:
            self.issues.append({'cp': cp, 'reason': reason})
        self.stack.clear()

    @staticmethod
    def frame(depth, cp):
        return {'depth': depth, 'start': cp, 'cell_start': cp, 'chunks': [],
                'children': [], 'cells': [], 'rows': [], 'has_nested': False}

    @staticmethod
    def pending(frame):
        return bool(frame['cells'] or frame['chunks'] or frame['children'])

    def feed(self, text, mark, start, end, props, field_overlap=False):
        from read_wps import require
        self.events += 1
        require(self.events <= MAX_EVENTS, {'stage': 'nested_tables'}, 'Nested event budget exceeded', 'resource_limit')
        depth = props.get('depth', 0) if props else 0
        if not props or props.get('structure_unknown') or field_overlap or props.get('unsupported_cell_layout'):
            if self.stack or depth > 1:
                self.reject(start, 'unverified_nested_properties')
            return
        if not depth and not props.get('in_table'):
            if self.stack and any(self.pending(frame) for frame in self.stack):
                self.reject(start, 'unfinished_nested_parent')
            self.stack.clear()
            return
        if props.get('in_table') != 1 or depth < 1:
            self.reject(start, 'inconsistent_table_depth')
            return
        require(depth <= MAX_DEPTH, {'stage': 'nested_tables', 'cp': start}, 'Nested depth budget exceeded', 'resource_limit')
        while self.stack and self.stack[-1]['depth'] > depth:
            child = self.stack.pop()
            if self.pending(child) or not child['rows'] or not self.stack:
                self.reject(start, 'unfinished_nested_row')
                return
            parent = self.stack[-1]
            parent['children'].append({'depth': child['depth'], 'row_fragments': child['rows'], 'grouping': 'unknown'})
            parent['has_nested'] = True
        if not self.stack:
            if depth != 1:
                self.reject(start, 'parent_cell_start_unverified')
                return
            self.stack.append(self.frame(1, start))
        if depth > self.stack[-1]['depth']:
            parent = self.stack[-1]
            if depth != parent['depth'] + 1 or not parent['chunks']:
                self.reject(start, 'parent_cell_start_unverified')
                return
            self.stack.append(self.frame(depth, start))
        frame = self.stack[-1]
        inner_cell, inner_row = props.get('inner_cell', 0), props.get('inner_row_end', 0)
        if depth == 1:
            if inner_cell or inner_row:
                self.reject(start, 'inner_marker_at_outer_depth')
                return
            row_end = props.get('row_end', 0)
            cell_end = mark == '\x07' and not row_end
            valid_mark = mark == '\x07' if row_end else mark in ('\r', '\x07')
        else:
            row_end, cell_end = inner_row, inner_cell
            valid_mark = mark == '\r' and not props.get('row_end') and not (row_end and cell_end)
        if not valid_mark:
            self.reject(start, 'wrong_nested_terminator')
            return
        if row_end:
            if text or frame['chunks'] or frame['children'] or not frame['cells']:
                self.reject(start, 'row_mark_not_after_cell')
                return
            row = {'depth': depth, 'cells': frame['cells'], 'source': {'cp_start': frame['start'], 'cp_end': end},
                   'structure_status': 'stored_boundary_fragment', 'layout': 'unknown'}
            if depth == 1:
                if frame['has_nested']:
                    self.rows.append(row)
            else:
                frame['rows'].append(row)
            frame['cells'] = []
            frame['start'] = end
            frame['cell_start'] = end
            frame['has_nested'] = False
            return
        frame['chunks'].append(text.replace('\x0b', '\n'))
        if cell_end:
            self.cells += 1
            require(self.cells <= MAX_CELLS and len(frame['cells']) < 63,
                    {'stage': 'nested_tables', 'cp': start}, 'Nested cell budget exceeded', 'resource_limit')
            frame['cells'].append({'text': '\n'.join(frame['chunks']), 'text_scope': 'direct_paragraphs_only',
                                   'nested_tables': frame['children'],
                                   'source': {'cp_start': frame['cell_start'], 'cp_end': end}})
            frame['chunks'], frame['children'] = [], []
            frame['cell_start'] = end

    def finish(self, cp):
        if any(self.pending(frame) or frame['depth'] > 1 for frame in self.stack):
            self.reject(cp, 'unfinished_nested_parent')
        return {'scope': 'verified_direct_boundaries_only', 'status': 'partial', 'rows': self.rows,
                'issues': self.issues, 'issue_count': self.issue_count,
                'issues_omitted': self.issue_count - len(self.issues)}
