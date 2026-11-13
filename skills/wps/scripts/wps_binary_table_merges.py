"""Pair stored MS-DOC table merge markers across adjacent physical rows."""
from bisect import bisect_right


MAX_MERGE_ISSUES = 100
MAX_GRID_COLUMNS = 256
_VERTICAL_ROLES = {'none', 'start', 'continuation'}


class _IssueCollector:
    """Keep exact issue totals while bounding retained diagnostics."""

    def __init__(self):
        self.items = []
        self.total = 0

    def add(self, code, row_index, first_cell=None):
        self.total += 1
        if len(self.items) >= MAX_MERGE_ISSUES:
            return
        issue = {'code': code, 'row_index': row_index}
        if first_cell is not None:
            issue['first_cell'] = first_cell
        self.items.append(issue)


def _is_int(value):
    return type(value) is int


def _valid_edges(value, cell_count):
    if not isinstance(value, list) or len(value) != cell_count + 1:
        return False
    if len(value) < 2 or any(not _is_int(edge) for edge in value):
        return False
    return all(left < right for left, right in zip(value, value[1:]))


def _parse_horizontal_merges(value, cell_count, row_index, issues):
    if not isinstance(value, list):
        issues.add('invalid_horizontal_merges', row_index)
        return [], False

    candidates = []
    valid = True
    for merge in value:
        if not isinstance(merge, dict):
            issues.add('invalid_horizontal_merge', row_index)
            valid = False
            continue
        first_cell = merge.get('first_cell')
        cell_count_in_merge = merge.get('cell_count')
        if not _is_int(first_cell) or not _is_int(cell_count_in_merge):
            issues.add('invalid_horizontal_merge', row_index)
            valid = False
            continue
        if cell_count_in_merge < 2:
            issues.add('invalid_horizontal_merge_span', row_index, first_cell)
            valid = False
            continue
        if first_cell < 0 or first_cell + cell_count_in_merge > cell_count:
            issues.add('horizontal_merge_out_of_bounds', row_index, first_cell)
            valid = False
            continue
        candidates.append((first_cell, cell_count_in_merge))

    candidates.sort()
    overlapping = set()
    maximum_end = -1
    maximum_end_index = None
    for index, (first_cell, merged_count) in enumerate(candidates):
        if first_cell < maximum_end:
            overlapping.update((maximum_end_index, index))
            issues.add('overlapping_horizontal_merges', row_index, first_cell)
            valid = False
        if first_cell + merged_count > maximum_end:
            maximum_end = first_cell + merged_count
            maximum_end_index = index
    merges = [merge for index, merge in enumerate(candidates) if index not in overlapping]
    return merges, valid


def _parse_row(row, row_index, issues):
    state = {'usable': False, 'grid_edges': None, 'logical_cells': []}
    if not isinstance(row, dict):
        issues.add('invalid_row_layout', row_index)
        return state

    physical_cells = row.get('cells')
    if not isinstance(physical_cells, list) or not physical_cells:
        issues.add('invalid_row_cells', row_index)
        return state
    if any(not isinstance(cell, dict) or not isinstance(cell.get('text'), str)
           for cell in physical_cells):
        issues.add('invalid_row_cells', row_index)
        return state

    layout = row.get('stored_cell_layout')
    if layout is None:
        issues.add('unknown_row_layout', row_index)
        return state
    if not isinstance(layout, dict):
        issues.add('invalid_row_layout', row_index)
        return state

    cell_count = len(physical_cells)
    raw_edges = layout.get('cell_edges_twips')
    edges_valid = _valid_edges(raw_edges, cell_count)
    if edges_valid:
        state['grid_edges'] = raw_edges
    else:
        issues.add('invalid_cell_edges', row_index)

    merges, horizontal_valid = _parse_horizontal_merges(
        layout.get('horizontal_merges'), cell_count, row_index, issues)
    raw_roles = layout.get('vertical_merge_roles')
    roles_valid = (isinstance(raw_roles, list) and len(raw_roles) == cell_count
                   and all(isinstance(role, str) and role in _VERTICAL_ROLES
                           for role in raw_roles))
    if not roles_valid:
        issues.add('invalid_vertical_merge_roles', row_index)

    if edges_valid:
        for first_cell, merged_count in merges:
            state.setdefault('horizontal_merges', []).append({
                'row_index': row_index,
                'first_cell': first_cell,
                'cell_count': merged_count,
                'left_twips': raw_edges[first_cell],
                'right_twips': raw_edges[first_cell + merged_count],
            })

    if not (edges_valid and horizontal_valid and roles_valid):
        return state

    horizontal_by_first = {first: count for first, count in merges}
    index = 0
    while index < cell_count:
        merged_count = horizontal_by_first.get(index, 1)
        role = raw_roles[index]
        role_valid = True
        if merged_count > 1:
            for follower in range(index + 1, index + merged_count):
                if raw_roles[follower] not in ('none', role):
                    role_valid = False
                    issues.add('incompatible_horizontal_vertical_roles', row_index, index)
                    break
        text_empty = all(physical_cells[cell_index]['text'] == ''
                         for cell_index in range(index, index + merged_count))
        if role == 'continuation' and not text_empty:
            issues.add('nonempty_vertical_continuation', row_index, index)
        state['logical_cells'].append({
            'first_cell': index,
            'cell_count': merged_count,
            'left_twips': raw_edges[index],
            'right_twips': raw_edges[index + merged_count],
            'role': role,
            'role_valid': role_valid,
            'text_empty': text_empty,
        })
        index += merged_count

    state['usable'] = True
    return state


def _cell_map(logical_cells):
    return {(cell['left_twips'], cell['right_twips']): cell for cell in logical_cells}


def _overlapping_cells(logical_cells, cell_starts, left_twips, right_twips):
    index = max(0, bisect_right(cell_starts, left_twips) - 1)
    while index < len(logical_cells) and logical_cells[index]['left_twips'] < right_twips:
        cell = logical_cells[index]
        if cell['right_twips'] > left_twips:
            yield cell
        index += 1


def _start_chain(row_index, cell):
    return {
        'row_index': row_index,
        'left_twips': cell['left_twips'],
        'right_twips': cell['right_twips'],
        'cells': [{'first_cell': cell['first_cell'], 'cell_count': cell['cell_count']}],
    }


def _finish_chain(chain, partial, vertical_merges, issues, reason=None):
    if len(chain['cells']) < 2:
        issues.add('unmatched_vertical_start', chain['row_index'], chain['cells'][0]['first_cell'])
        return
    vertical_merges.append({
        'row_index': chain['row_index'],
        'row_count': len(chain['cells']),
        'left_twips': chain['left_twips'],
        'right_twips': chain['right_twips'],
        'cells': chain['cells'],
        'status': 'partial' if partial else 'stored',
    })
    if reason is not None:
        issues.add(reason, chain['row_index'], chain['cells'][0]['first_cell'])


def _close_active_chains(active, current_cells, row_index, vertical_merges, issues):
    current_by_interval = _cell_map(current_cells)
    current_starts = [cell['left_twips'] for cell in current_cells]
    next_active = {}
    matched_intervals = set()
    conflict_intervals = set()

    for interval, chain in active.items():
        cell = current_by_interval.get(interval)
        if cell is not None:
            matched_intervals.add(interval)
            if not cell['role_valid']:
                _finish_chain(chain, True, vertical_merges, issues, 'vertical_merge_interrupted')
            elif cell['role'] == 'continuation':
                if cell['text_empty']:
                    chain['cells'].append({
                        'first_cell': cell['first_cell'],
                        'cell_count': cell['cell_count'],
                    })
                    next_active[interval] = chain
                else:
                    _finish_chain(chain, True, vertical_merges, issues)
            elif cell['role'] == 'start':
                _finish_chain(chain, False, vertical_merges, issues)
                next_active[interval] = _start_chain(row_index, cell)
            else:
                _finish_chain(chain, False, vertical_merges, issues)
            continue

        overlapping = list(_overlapping_cells(
            current_cells, current_starts, chain['left_twips'], chain['right_twips']))
        incompatible = [candidate for candidate in overlapping
                       if not candidate['role_valid'] or candidate['role'] == 'continuation']
        if incompatible:
            candidate = incompatible[0]
            issues.add('conflicting_vertical_span', row_index, candidate['first_cell'])
            for conflict in incompatible:
                conflict_intervals.add((conflict['left_twips'], conflict['right_twips']))
            _finish_chain(chain, True, vertical_merges, issues)
        else:
            _finish_chain(chain, False, vertical_merges, issues)

    for cell in current_cells:
        interval = (cell['left_twips'], cell['right_twips'])
        if interval in matched_intervals:
            continue
        if not cell['role_valid']:
            continue
        if cell['role'] == 'start':
            if interval not in next_active:
                next_active[interval] = _start_chain(row_index, cell)
        elif cell['role'] == 'continuation' and interval not in conflict_intervals:
            issues.add('orphan_vertical_continuation', row_index, cell['first_cell'])

    return next_active


def _make_grid_edges(row_states):
    edge_lists = [state['grid_edges'] for state in row_states]
    if not edge_lists or any(edges is None for edges in edge_lists):
        return None
    outer_edges = {(edges[0], edges[-1]) for edges in edge_lists}
    if len(outer_edges) != 1:
        return None
    grid_edges = sorted({edge for edges in edge_lists for edge in edges})
    if len(grid_edges) - 1 > MAX_GRID_COLUMNS:
        return None
    return grid_edges


def analyze_table_merges(rows, *, uncertain_end=False):
    """Return bounded horizontal and vertical merges from one row fragment."""
    issues = _IssueCollector()
    horizontal_merges = []
    vertical_merges = []
    if not isinstance(rows, list) or not rows:
        issues.add('invalid_rows', 0)
        return {
            'column_edges_twips': None,
            'horizontal_merges': horizontal_merges,
            'vertical_merges': vertical_merges,
            'merge_status': 'unknown',
            'unresolved_merge_count': issues.total,
            'merge_issues': issues.items,
            'merge_issues_omitted': 0,
        }

    row_states = []
    for row_index, row in enumerate(rows):
        state = _parse_row(row, row_index, issues)
        row_states.append(state)
        horizontal_merges.extend(state.get('horizontal_merges', ()))

    active = {}
    for row_index, state in enumerate(row_states):
        if not state['usable']:
            for chain in active.values():
                _finish_chain(chain, True, vertical_merges, issues, 'vertical_merge_interrupted')
            active = {}
            continue
        active = _close_active_chains(
            active, state['logical_cells'], row_index, vertical_merges, issues)

    if active:
        for chain in active.values():
            _finish_chain(chain, uncertain_end, vertical_merges, issues,
                          'uncertain_vertical_merge_end' if uncertain_end else None)

    vertical_merges.sort(key=lambda merge: (
        merge['row_index'], merge['left_twips'], merge['right_twips']))

    usable_layout_count = sum(state['usable'] for state in row_states)
    if usable_layout_count == 0:
        merge_status = 'unknown'
    elif issues.total or any(not state['usable'] for state in row_states):
        merge_status = 'partial'
    else:
        merge_status = 'stored'

    grid_edges = _make_grid_edges(row_states)
    return {
        'column_edges_twips': grid_edges,
        'horizontal_merges': horizontal_merges,
        'vertical_merges': vertical_merges,
        'merge_status': merge_status,
        'unresolved_merge_count': issues.total,
        'merge_issues': issues.items,
        'merge_issues_omitted': max(0, issues.total - len(issues.items)),
    }
