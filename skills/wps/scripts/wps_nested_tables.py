"""Conservative MS-DOC nested row fragments from verified direct PAPX marks."""
MAX_DEPTH = 32
MAX_CELLS = 100000
MAX_EVENTS = 100000


class NestedRows:
    """Keep hierarchy separate from legacy flat rows; never certify layout."""

    def __init__(self, *, bookmarks=False):
        self.bookmarks = bookmarks
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
                'children': [], 'cells': [], 'rows': [], 'identities': [],
                'first_paragraph': None, 'has_nested': False}

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
            parent['children'].append(self.group_child(child))
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
        if frame['first_paragraph'] is None:
            frame['first_paragraph'] = props
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
            # PFInnerTtp identifies the separate row terminator even when
            # producers also retain PFInnerTableCell on that empty paragraph.
            # The row-end branch still requires closed cells and no content.
            valid_mark = mark == '\r' and not props.get('row_end')
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
                from wps_binary_table_groups import row_identity
                frame['rows'].append(row)
                frame['identities'].append(row_identity(props, frame['first_paragraph']))
            frame['cells'] = []
            frame['first_paragraph'] = None
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
            cell_source = frame['cells'][-1]['source']
            for child in frame['children']:
                for table in child['tables']:
                    table['parent_cell_source'] = dict(cell_source)
                    table['parent_cell_status'] = 'stored_boundaries'
            frame['chunks'], frame['children'] = [], []
            frame['cell_start'] = end

    def group_child(self, frame):
        """Index child rows without treating a depth run as one table.

        MS-DOC 2.4.3: adjacent rows join only when the stored grouping
        properties agree. Depth transitions prove the outer limits of this
        run, but unknown properties/bookmarks still split partial groups.
        Parent ownership is published only after its cell AND row close.
        """
        from wps_binary_table_groups import _adjacent_boundary, PROVEN_BOUNDARIES

        rows = frame['rows']
        tables = []
        proven = PROVEN_BOUNDARIES | {'parent_depth_transition'}

        def emit(first, limit, start_boundary, end_boundary):
            source = {'cp_start': rows[first]['source']['cp_start'],
                      'cp_end': rows[limit - 1]['source']['cp_end']}
            tables.append({'table_id': f"nested-{frame['depth']}-{source['cp_start']}",
                           'depth': frame['depth'], 'first_row_index': first,
                           'row_count': limit - first, 'source': source,
                           'grouping_status': ('stored' if start_boundary in proven and end_boundary in proven else 'partial'),
                           'start_boundary': start_boundary, 'end_boundary': end_boundary,
                           'layout': 'unknown', 'merges': 'unknown'})

        first, start_boundary = 0, 'parent_depth_transition'
        for index in range(1, len(rows)):
            boundary = _adjacent_boundary(frame['identities'][index - 1], frame['identities'][index], self.bookmarks)
            if rows[index - 1]['source']['cp_end'] != rows[index]['source']['cp_start']:
                boundary = 'unresolved_row_gap'
            if boundary is not None:
                emit(first, index, start_boundary, boundary)
                first, start_boundary = index, boundary
        emit(first, len(rows), start_boundary, 'parent_depth_transition')
        return {'depth': frame['depth'], 'row_fragments': rows,
                'grouping': 'unknown',  # Compatibility: this container is a run, not a table.
                'tables': tables,
                'table_grouping': 'stored' if all(t['grouping_status'] == 'stored' for t in tables) else 'partial'}

    def finish(self, cp):
        if any(self.pending(frame) or frame['depth'] > 1 for frame in self.stack):
            self.reject(cp, 'unfinished_nested_parent')
        return {'scope': 'verified_direct_boundaries_only', 'status': 'partial', 'rows': self.rows,
                'issues': self.issues, 'issue_count': self.issue_count,
                'issues_omitted': self.issue_count - len(self.issues)}
